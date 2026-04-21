# SPDX-FileCopyrightText: Copyright (c) 2026, Redis
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from nat.builder.context import Context
from nat.builder.function import Function
from nat.data_models.api_server import (
    ChatRequest,
    ChatRequestOrMessage,
    ChatResponse,
    Message,
    UserMessageContentRoleType,
)
from nat.utils.type_converter import GlobalTypeConverter

from .config import RedisAgentMemoryAutoMemoryConfig


@dataclass(frozen=True, slots=True)
class RedisAgentMemoryIdentity:
    user_id: str
    session_id: str


@dataclass(frozen=True, slots=True)
class RedisAgentMemoryWorkingMemoryOptions:
    namespace: str | None
    model_name: str | None
    context_window_max: int | None
    ttl_seconds: int | None
    long_term_memory_strategy: Any


def _strip(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _message_content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if hasattr(item, "model_dump"):
                item = item.model_dump(mode="json")
            if isinstance(item, dict) and item.get("type") == "text" and item.get("text"):
                parts.append(str(item["text"]))
            elif item:
                parts.append(str(item))
        return "\n".join(part for part in parts if part).strip()
    if isinstance(content, dict) and content.get("type") == "text" and content.get("text"):
        return str(content["text"]).strip()
    return str(content).strip()


def _prompt_message_to_nat_message(raw_message: Any) -> Message:
    if hasattr(raw_message, "model_dump"):
        raw_message = raw_message.model_dump(mode="json")

    if not isinstance(raw_message, dict):
        raise TypeError(f"Expected AMS memory_prompt message dict, got {type(raw_message)!r}")

    role_value = raw_message.get("role", UserMessageContentRoleType.ASSISTANT)
    role = UserMessageContentRoleType(role_value)
    content = _message_content_to_text(raw_message.get("content", ""))
    return Message(role=role, content=content)


def _message_signature(message: Message) -> tuple[UserMessageContentRoleType, str]:
    return message.role, _message_content_to_text(message.content)


def _merge_message_history(base_messages: list[Message], extra_messages: list[Message]) -> list[Message]:
    # Preserve caller-supplied turns without replaying overlapping history already returned by AMS.
    max_overlap = min(len(base_messages), len(extra_messages))
    for overlap_size in range(max_overlap, 0, -1):
        base_overlap = [_message_signature(message) for message in base_messages[-overlap_size:]]
        extra_overlap = [_message_signature(message) for message in extra_messages[:overlap_size]]
        if base_overlap == extra_overlap:
            return base_messages + extra_messages[overlap_size:]

    return base_messages + extra_messages


class RedisAgentMemoryAutoMemoryService:
    """Redis Agent Memory orchestration behind the NAT-native wrapper."""

    def __init__(self, client: Any, config: RedisAgentMemoryAutoMemoryConfig):
        self._client = client
        self._config = config

    def resolve_identity(self, context: Context | None = None) -> RedisAgentMemoryIdentity:
        context = context or Context.get()

        user_id = _strip(context.user_id) or self._config.default_user_id
        session_id = _strip(context.conversation_id) or self._config.default_session_id

        if not user_id or not session_id:
            raise ValueError("Redis Agent Memory wrapper requires a user_id and session_id.")

        return RedisAgentMemoryIdentity(user_id=user_id, session_id=session_id)

    def resolve_working_memory_options(self) -> RedisAgentMemoryWorkingMemoryOptions:
        working_memory = self._config.working_memory
        return RedisAgentMemoryWorkingMemoryOptions(
            namespace=working_memory.namespace,
            model_name=working_memory.model_name,
            context_window_max=working_memory.context_window_max,
            ttl_seconds=working_memory.ttl_seconds,
            long_term_memory_strategy=working_memory.long_term_memory_strategy.to_client_model(),
        )

    async def run(self, inner_agent: Function, value: ChatRequestOrMessage) -> ChatResponse | str:
        converter = GlobalTypeConverter.get()
        request = converter.convert(value, to_type=ChatRequest)
        identity = self.resolve_identity()
        options = self.resolve_working_memory_options()

        await self.ensure_working_memory(identity, options)

        user_query = self.extract_user_query(request)
        hydrated_request = request.model_copy(
            update={"messages": await self.build_prompt_messages(request.messages, identity, options, user_query)}
        )

        response = await inner_agent.ainvoke(hydrated_request)
        assistant_text = self.extract_assistant_text(response)
        await self.append_turn(identity, options, user_query, assistant_text)

        if value.is_string:
            try:
                return converter.convert(response, to_type=str)
            except Exception:
                return assistant_text

        try:
            return converter.convert(response, to_type=ChatResponse)
        except Exception:
            return converter.convert(assistant_text, to_type=ChatResponse)

    def extract_user_query(self, request: ChatRequest) -> str:
        message = request.messages[-1]
        if message.role != UserMessageContentRoleType.USER:
            raise ValueError("Redis Agent Memory wrapper requires the final input message to have role='user'.")

        query = _message_content_to_text(message.content)
        if not query:
            raise ValueError("Redis Agent Memory wrapper requires a non-empty user message.")

        return query

    async def build_prompt_messages(
        self,
        original_messages: list[Message],
        identity: RedisAgentMemoryIdentity,
        options: RedisAgentMemoryWorkingMemoryOptions,
        query: str,
    ) -> list[Message]:
        prompt = await self._client.memory_prompt(
            query=query,
            session_id=identity.session_id,
            namespace=options.namespace,
            model_name=options.model_name,
            context_window_max=options.context_window_max,
            long_term_search=self._config.memory_prompt.long_term_search.to_client_payload(),
            user_id=identity.user_id,
            optimize_query=self._config.memory_prompt.optimize_query,
        )

        prompt_messages = [_prompt_message_to_nat_message(message) for message in prompt.get("messages", [])]
        original_history = original_messages[:-1]
        original_system_messages = [
            message for message in original_history if message.role == UserMessageContentRoleType.SYSTEM
        ]
        original_conversation_history = [
            message for message in original_history if message.role != UserMessageContentRoleType.SYSTEM
        ]

        if prompt_messages and _message_signature(prompt_messages[-1]) == (UserMessageContentRoleType.USER, query):
            prompt_history = prompt_messages[:-1]
            final_user_message = prompt_messages[-1]
        else:
            prompt_history = prompt_messages
            final_user_message = original_messages[-1]

        prompt_system_messages = [
            message for message in prompt_history if message.role == UserMessageContentRoleType.SYSTEM
        ]
        prompt_conversation_history = [
            message for message in prompt_history if message.role != UserMessageContentRoleType.SYSTEM
        ]
        conversation_history = _merge_message_history(prompt_conversation_history, original_conversation_history)

        return original_system_messages + prompt_system_messages + conversation_history + [final_user_message]

    async def ensure_working_memory(
        self,
        identity: RedisAgentMemoryIdentity,
        options: RedisAgentMemoryWorkingMemoryOptions,
    ) -> Any:
        created, memory = await self._client.get_or_create_working_memory(
            session_id=identity.session_id,
            user_id=identity.user_id,
            namespace=options.namespace,
            model_name=options.model_name,
            context_window_max=options.context_window_max,
            long_term_memory_strategy=options.long_term_memory_strategy,
        )

        if created and options.ttl_seconds is not None and getattr(memory, "ttl_seconds", None) != options.ttl_seconds:
            memory = await self._client.put_working_memory(
                identity.session_id,
                memory=memory.model_copy(update={"ttl_seconds": options.ttl_seconds}),
                user_id=identity.user_id,
                model_name=options.model_name,
                context_window_max=options.context_window_max,
            )

        return memory

    async def append_turn(
        self,
        identity: RedisAgentMemoryIdentity,
        options: RedisAgentMemoryWorkingMemoryOptions,
        user_text: str,
        assistant_text: str,
    ) -> None:
        from agent_memory_client.models import MemoryMessage

        now = datetime.now(UTC)
        messages = [
            MemoryMessage(role="user", content=user_text, created_at=now),
            MemoryMessage(role="assistant", content=assistant_text, created_at=now),
        ]
        await self._client.append_messages_to_working_memory(
            session_id=identity.session_id,
            messages=messages,
            namespace=options.namespace,
            model_name=options.model_name,
            context_window_max=options.context_window_max,
            user_id=identity.user_id,
        )

    def extract_assistant_text(self, response: Any) -> str:
        try:
            return GlobalTypeConverter.get().convert(response, to_type=str)
        except Exception:
            if isinstance(response, str):
                return response
            if hasattr(response, "choices") and getattr(response, "choices", None):
                choice = response.choices[0]
                message = getattr(choice, "message", None)
                content = getattr(message, "content", None)
                return _message_content_to_text(content)
            if hasattr(response, "output"):
                return str(response.output)
            if hasattr(response, "value") and response.value is not None:
                return str(response.value)
            return str(response)
