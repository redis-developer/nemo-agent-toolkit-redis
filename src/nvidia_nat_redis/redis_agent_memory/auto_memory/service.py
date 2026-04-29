# SPDX-FileCopyrightText: Copyright (c) 2026, Redis
# SPDX-License-Identifier: Apache-2.0

"""Runtime orchestration for the Redis Agent Memory automatic NAT wrapper."""

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

from .._text import message_content_to_text, normalize_optional_string
from .config import RedisAgentMemoryAutoMemoryConfig


@dataclass(frozen=True, slots=True)
class RedisAgentMemoryIdentity:
    """Resolved Redis Agent Memory identity for one NAT invocation."""

    user_id: str
    session_id: str


@dataclass(frozen=True, slots=True)
class RedisAgentMemoryWorkingMemoryOptions:
    """Working-memory options forwarded on Redis Agent Memory client calls."""

    namespace: str | None
    model_name: str | None
    context_window_max: int | None
    ttl_seconds: int | None
    long_term_memory_strategy: Any


def _prompt_message_to_nat_message(raw_message: Any) -> Message:
    if hasattr(raw_message, "model_dump"):
        raw_message = raw_message.model_dump(mode="json")

    if not isinstance(raw_message, dict):
        raise TypeError(f"Expected Redis Agent Memory memory_prompt message dict, got {type(raw_message)!r}")

    role_value = raw_message.get("role", UserMessageContentRoleType.ASSISTANT)
    role = UserMessageContentRoleType(role_value)
    content = message_content_to_text(raw_message.get("content", ""))
    return Message(role=role, content=content)


def _message_signature(message: Message) -> tuple[UserMessageContentRoleType, str]:
    return message.role, message_content_to_text(message.content)


def _partition_system_messages(messages: list[Message]) -> tuple[list[Message], list[Message]]:
    system_messages = [message for message in messages if message.role == UserMessageContentRoleType.SYSTEM]
    conversation_messages = [message for message in messages if message.role != UserMessageContentRoleType.SYSTEM]
    return system_messages, conversation_messages


def _split_prompt_history(
    prompt_messages: list[Message],
    query: str,
    fallback_final_user_message: Message,
) -> tuple[list[Message], Message]:
    if prompt_messages and _message_signature(prompt_messages[-1]) == (UserMessageContentRoleType.USER, query):
        return prompt_messages[:-1], prompt_messages[-1]

    return prompt_messages, fallback_final_user_message


def _merge_message_history(base_messages: list[Message], extra_messages: list[Message]) -> list[Message]:
    # Preserve caller-supplied turns without replaying overlapping history already returned by Redis Agent Memory.
    max_overlap = min(len(base_messages), len(extra_messages))
    for overlap_size in range(max_overlap, 0, -1):
        base_overlap = [_message_signature(message) for message in base_messages[-overlap_size:]]
        extra_overlap = [_message_signature(message) for message in extra_messages[:overlap_size]]
        if base_overlap == extra_overlap:
            return base_messages + extra_messages[overlap_size:]

    return base_messages + extra_messages


class RedisAgentMemoryAutoMemoryService:
    """
    Orchestrate Redis Agent Memory around a NAT chat function.

    The service is the runtime implementation behind
    ``_type: redis_agent_memory_auto_memory``. Each invocation loads or creates
    a Redis Agent Memory working-memory session, asks Redis Agent Memory to
    build a hydrated prompt, invokes the configured inner NAT function, and
    appends the completed turn back into working memory.
    """

    def __init__(self, client: Any, config: RedisAgentMemoryAutoMemoryConfig):
        """Create the service with a Redis Agent Memory client and wrapper config."""
        self._client = client
        self._config = config

    def resolve_identity(self, context: Context | None = None) -> RedisAgentMemoryIdentity:
        """
        Resolve Redis Agent Memory ``user_id`` and ``session_id``.

        NAT ``user_id`` maps directly to Redis Agent Memory ``user_id``. NAT
        ``conversation_id`` maps to Redis Agent Memory ``session_id`` so a
        stable NAT conversation gets stable working memory. Configured defaults
        are used only when the runtime context does not provide a value.
        """
        context = context or Context.get()

        user_id = normalize_optional_string(context.user_id) or self._config.default_user_id
        session_id = normalize_optional_string(context.conversation_id) or self._config.default_session_id

        if not user_id or not session_id:
            raise ValueError("Redis Agent Memory wrapper requires a user_id and session_id.")

        return RedisAgentMemoryIdentity(user_id=user_id, session_id=session_id)

    def resolve_working_memory_options(self) -> RedisAgentMemoryWorkingMemoryOptions:
        """Resolve wrapper config into options reused across working-memory calls."""
        working_memory = self._config.working_memory
        return RedisAgentMemoryWorkingMemoryOptions(
            namespace=working_memory.namespace,
            model_name=working_memory.model_name,
            context_window_max=working_memory.context_window_max,
            ttl_seconds=working_memory.ttl_seconds,
            long_term_memory_strategy=working_memory.long_term_memory_strategy.to_client_model(),
        )

    async def run(self, inner_agent: Function, value: ChatRequestOrMessage) -> ChatResponse | str:
        """
        Run one memory-managed chat turn through the wrapped NAT function.

        The incoming value is converted to ``ChatRequest`` for prompt hydration.
        String inputs still return strings when possible, preserving the NAT
        function interface expected by callers that invoke the wrapper with a
        simple message.
        """
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
        """
        Return the final user message text from a chat request.

        Redis Agent Memory prompt hydration is query-driven, so the wrapper
        requires the final message to be a non-empty user turn.
        """
        message = request.messages[-1]
        if message.role != UserMessageContentRoleType.USER:
            raise ValueError("Redis Agent Memory wrapper requires the final input message to have role='user'.")

        query = message_content_to_text(message.content)
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
        """
        Build the request messages passed to the inner NAT function.

        The Redis Agent Memory ``memory_prompt`` response may include system
        memory context, remembered conversation history, and the current user
        query. Caller-supplied system messages are preserved first, prompt
        system messages are appended after them, overlapping conversation
        history is deduplicated, and the final user message remains last.
        """
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
        original_system_messages, original_conversation_history = _partition_system_messages(original_history)
        prompt_history, final_user_message = _split_prompt_history(prompt_messages, query, original_messages[-1])
        prompt_system_messages, prompt_conversation_history = _partition_system_messages(prompt_history)
        conversation_history = _merge_message_history(prompt_conversation_history, original_conversation_history)

        return original_system_messages + prompt_system_messages + conversation_history + [final_user_message]

    async def ensure_working_memory(
        self,
        identity: RedisAgentMemoryIdentity,
        options: RedisAgentMemoryWorkingMemoryOptions,
    ) -> Any:
        """
        Create or load the Redis Agent Memory working-memory session.

        When a new session is created and ``ttl_seconds`` is configured, the
        wrapper persists that TTL with a follow-up update because Redis Agent
        Memory creation returns the complete session model.
        """
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
        """
        Append the completed user and assistant messages to working memory.

        Redis Agent Memory can later use the stored turn for prompt hydration
        and long-term memory promotion according to the configured strategy.
        """
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
        """
        Convert an inner-agent response into text for working-memory append.

        The method first asks NAT's global converter for a string and then falls
        back through common chat response shapes used by NAT and LLM clients.
        """
        try:
            return GlobalTypeConverter.get().convert(response, to_type=str)
        except Exception:
            if isinstance(response, str):
                return response
            if hasattr(response, "choices") and getattr(response, "choices", None):
                choice = response.choices[0]
                message = getattr(choice, "message", None)
                content = getattr(message, "content", None)
                return message_content_to_text(content)
            if hasattr(response, "output"):
                return str(response.output)
            if hasattr(response, "value") and response.value is not None:
                return str(response.value)
            return str(response)
