# SPDX-FileCopyrightText: Copyright (c) 2026, Redis
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from agent_memory_client.models import WorkingMemory
from nat.builder.function_info import FunctionInfo
from nat.data_models.api_server import (
    ChatRequest,
    ChatRequestOrMessage,
    ChatResponse,
    Message,
    Usage,
    UserMessageContentRoleType,
)

from nvidia_nat_redis.redis_agent_memory.auto_memory.config import RedisAgentMemoryAutoMemoryConfig
from nvidia_nat_redis.redis_agent_memory.auto_memory.register import redis_agent_memory_auto_memory
from nvidia_nat_redis.redis_agent_memory.auto_memory.service import RedisAgentMemoryAutoMemoryService
from nvidia_nat_redis.redis_agent_memory.memory import RedisAgentMemoryBackendConfig


def _working_memory(
    session_id: str = "session-1",
    user_id: str = "user-1",
    ttl_seconds: int | None = None,
) -> WorkingMemory:
    return WorkingMemory(
        session_id=session_id,
        user_id=user_id,
        messages=[],
        memories=[],
        data={},
        ttl_seconds=ttl_seconds,
    )


def _chat_response(text: str) -> ChatResponse:
    token_count = max(len(text.split()), 1)
    return ChatResponse.from_string(
        text,
        usage=Usage(prompt_tokens=1, completion_tokens=token_count, total_tokens=token_count + 1),
    )


@pytest.fixture(name="auto_memory_config")
def auto_memory_config_fixture() -> RedisAgentMemoryAutoMemoryConfig:
    return RedisAgentMemoryAutoMemoryConfig(
        inner_agent_name="assistant_chat",
        memory_name="redis_memory",
        working_memory={
            "namespace": "nat",
            "model_name": "gpt-4o-mini",
            "context_window_max": 2048,
            "ttl_seconds": 3600,
            "long_term_memory_strategy": {"strategy": "discrete"},
        },
        memory_prompt={
            "optimize_query": True,
            "long_term_search": {"limit": 3, "topics": ["preferences"]},
        },
    )


def test_long_term_search_config_serializes_to_ams_filters(
    auto_memory_config: RedisAgentMemoryAutoMemoryConfig,
) -> None:
    payload = auto_memory_config.memory_prompt.long_term_search.to_client_payload()

    assert payload == {
        "limit": 3,
        "offset": 0,
        "topics": {"any": ["preferences"]},
    }


def test_service_resolve_identity_prefers_runtime_context(
    auto_memory_config: RedisAgentMemoryAutoMemoryConfig,
) -> None:
    service = RedisAgentMemoryAutoMemoryService(client=AsyncMock(), config=auto_memory_config)

    with patch(
        "nvidia_nat_redis.redis_agent_memory.auto_memory.service.Context.get",
        return_value=SimpleNamespace(user_id="runtime-user", conversation_id="runtime-session"),
    ):
        identity = service.resolve_identity()

    assert identity.user_id == "runtime-user"
    assert identity.session_id == "runtime-session"


async def test_service_ensure_working_memory_sets_ttl_for_new_sessions(
    auto_memory_config: RedisAgentMemoryAutoMemoryConfig,
) -> None:
    client = AsyncMock()
    client.get_or_create_working_memory = AsyncMock(return_value=(True, _working_memory()))
    client.put_working_memory = AsyncMock(return_value=_working_memory(ttl_seconds=3600))
    service = RedisAgentMemoryAutoMemoryService(client=client, config=auto_memory_config)

    identity = service.resolve_identity(SimpleNamespace(user_id="user-1", conversation_id="session-1"))
    options = service.resolve_working_memory_options()

    memory = await service.ensure_working_memory(identity, options)

    assert memory.ttl_seconds == 3600
    client.get_or_create_working_memory.assert_awaited_once_with(
        session_id="session-1",
        user_id="user-1",
        namespace="nat",
        model_name="gpt-4o-mini",
        context_window_max=2048,
        long_term_memory_strategy=options.long_term_memory_strategy,
    )
    client.put_working_memory.assert_awaited_once()
    put_call = client.put_working_memory.await_args
    assert put_call.args[0] == "session-1"
    assert put_call.kwargs["memory"].ttl_seconds == 3600
    assert put_call.kwargs["user_id"] == "user-1"


async def test_service_build_prompt_messages_preserves_caller_history(
    auto_memory_config: RedisAgentMemoryAutoMemoryConfig,
) -> None:
    client = AsyncMock()
    client.memory_prompt = AsyncMock(
        return_value={
            "messages": [
                {"role": "system", "content": {"type": "text", "text": "Memory context"}},
                {"role": "user", "content": {"type": "text", "text": "What do I like?"}},
            ]
        }
    )
    service = RedisAgentMemoryAutoMemoryService(client=client, config=auto_memory_config)
    identity = service.resolve_identity(SimpleNamespace(user_id="user-1", conversation_id="session-1"))
    options = service.resolve_working_memory_options()

    messages = await service.build_prompt_messages(
        original_messages=[
            Message(role=UserMessageContentRoleType.SYSTEM, content="Be concise."),
            Message(role=UserMessageContentRoleType.USER, content="Remember that I like tea."),
            Message(role=UserMessageContentRoleType.ASSISTANT, content="I will remember that."),
            Message(role=UserMessageContentRoleType.USER, content="What do I like?"),
        ],
        identity=identity,
        options=options,
        query="What do I like?",
    )

    assert [message.role for message in messages] == [
        UserMessageContentRoleType.SYSTEM,
        UserMessageContentRoleType.SYSTEM,
        UserMessageContentRoleType.USER,
        UserMessageContentRoleType.ASSISTANT,
        UserMessageContentRoleType.USER,
    ]
    assert [message.content for message in messages] == [
        "Be concise.",
        "Memory context",
        "Remember that I like tea.",
        "I will remember that.",
        "What do I like?",
    ]


async def test_service_run_hydrates_prompt_and_appends_turn_for_conversation(
    auto_memory_config: RedisAgentMemoryAutoMemoryConfig,
) -> None:
    client = AsyncMock()
    client.get_or_create_working_memory = AsyncMock(return_value=(False, _working_memory()))
    client.memory_prompt = AsyncMock(
        return_value={
            "messages": [
                {"role": "system", "content": {"type": "text", "text": "Memory context"}},
                {"role": "assistant", "content": {"type": "text", "text": "Previous answer"}},
                {"role": "user", "content": {"type": "text", "text": "What do I like?"}},
            ]
        }
    )
    client.append_messages_to_working_memory = AsyncMock()
    service = RedisAgentMemoryAutoMemoryService(client=client, config=auto_memory_config)
    inner_agent = SimpleNamespace(
        input_schema=ChatRequestOrMessage,
        ainvoke=AsyncMock(return_value=_chat_response("You like tea.")),
    )
    value = ChatRequestOrMessage(
        messages=[
            Message(role=UserMessageContentRoleType.SYSTEM, content="Be concise."),
            Message(role=UserMessageContentRoleType.USER, content="What do I like?"),
        ],
        temperature=0.2,
    )

    with patch(
        "nvidia_nat_redis.redis_agent_memory.auto_memory.service.Context.get",
        return_value=SimpleNamespace(user_id="runtime-user", conversation_id="runtime-session"),
    ):
        result = await service.run(inner_agent=inner_agent, value=value)

    assert isinstance(result, ChatResponse)
    assert result.choices[0].message.content == "You like tea."

    client.memory_prompt.assert_awaited_once_with(
        query="What do I like?",
        session_id="runtime-session",
        namespace="nat",
        model_name="gpt-4o-mini",
        context_window_max=2048,
        long_term_search={"limit": 3, "offset": 0, "topics": {"any": ["preferences"]}},
        user_id="runtime-user",
        optimize_query=True,
    )
    inner_call = inner_agent.ainvoke.await_args.args[0]
    assert [message.role for message in inner_call.messages] == [
        UserMessageContentRoleType.SYSTEM,
        UserMessageContentRoleType.SYSTEM,
        UserMessageContentRoleType.ASSISTANT,
        UserMessageContentRoleType.USER,
    ]
    assert inner_call.messages[0].content == "Be concise."
    assert inner_call.messages[1].content == "Memory context"
    assert inner_call.messages[-1].content == "What do I like?"

    client.append_messages_to_working_memory.assert_awaited_once()
    append_call = client.append_messages_to_working_memory.await_args
    assert append_call.kwargs["session_id"] == "runtime-session"
    assert append_call.kwargs["namespace"] == "nat"
    assert append_call.kwargs["user_id"] == "runtime-user"
    assert [message.role for message in append_call.kwargs["messages"]] == ["user", "assistant"]
    assert append_call.kwargs["messages"][1].content == "You like tea."


async def test_service_run_returns_string_for_string_input(
    auto_memory_config: RedisAgentMemoryAutoMemoryConfig,
) -> None:
    client = AsyncMock()
    client.get_or_create_working_memory = AsyncMock(return_value=(False, _working_memory()))
    client.memory_prompt = AsyncMock(return_value={"messages": [{"role": "user", "content": "hello"}]})
    client.append_messages_to_working_memory = AsyncMock()
    service = RedisAgentMemoryAutoMemoryService(client=client, config=auto_memory_config)
    inner_agent = SimpleNamespace(input_schema=ChatRequestOrMessage, ainvoke=AsyncMock(return_value="world"))

    with patch(
        "nvidia_nat_redis.redis_agent_memory.auto_memory.service.Context.get",
        return_value=SimpleNamespace(user_id="runtime-user", conversation_id="runtime-session"),
    ):
        result = await service.run(inner_agent=inner_agent, value=ChatRequestOrMessage(input_message="hello"))

    assert result == "world"


def test_service_extract_user_query_requires_final_user_message(
    auto_memory_config: RedisAgentMemoryAutoMemoryConfig,
) -> None:
    service = RedisAgentMemoryAutoMemoryService(client=AsyncMock(), config=auto_memory_config)
    request = ChatRequest(
        messages=[Message(role=UserMessageContentRoleType.ASSISTANT, content="Not a user turn")],
    )

    with pytest.raises(ValueError, match="role='user'"):
        service.extract_user_query(request)


async def test_register_rejects_non_ams_memory_configs(
    auto_memory_config: RedisAgentMemoryAutoMemoryConfig,
) -> None:
    builder = SimpleNamespace(get_memory_client_config=lambda _name: object())

    with pytest.raises(ValueError, match="redis_agent_memory_backend"):
        async with redis_agent_memory_auto_memory(auto_memory_config, builder):
            pass


async def test_register_builds_function_for_chat_request_inner_agents(
    auto_memory_config: RedisAgentMemoryAutoMemoryConfig,
) -> None:
    client = AsyncMock()
    client.get_or_create_working_memory = AsyncMock(return_value=(False, _working_memory()))
    client.memory_prompt = AsyncMock(return_value={"messages": [{"role": "user", "content": "hello"}]})
    client.append_messages_to_working_memory = AsyncMock()
    client.close = AsyncMock()
    inner_agent = SimpleNamespace(input_schema=ChatRequestOrMessage, ainvoke=AsyncMock(return_value="world"))
    builder = SimpleNamespace(
        get_memory_client_config=lambda _name: RedisAgentMemoryBackendConfig(do_auto_retry=False),
        get_function=AsyncMock(return_value=inner_agent),
    )

    with (
        patch(
            "nvidia_nat_redis.redis_agent_memory.auto_memory.register.create_memory_client",
            new=AsyncMock(return_value=client),
        ),
        patch(
            "nvidia_nat_redis.redis_agent_memory.auto_memory.service.Context.get",
            return_value=SimpleNamespace(user_id="runtime-user", conversation_id="runtime-session"),
        ),
    ):
        async with redis_agent_memory_auto_memory(auto_memory_config, builder) as function_info:
            assert isinstance(function_info, FunctionInfo)
            assert await function_info.single_fn(ChatRequestOrMessage(input_message="hello")) == "world"

    client.close.assert_awaited_once()


async def test_register_wraps_ams_client_with_retry(
    auto_memory_config: RedisAgentMemoryAutoMemoryConfig,
) -> None:
    client = SimpleNamespace(close=AsyncMock())
    retry_client = SimpleNamespace(
        get_or_create_working_memory=AsyncMock(return_value=(False, _working_memory())),
        memory_prompt=AsyncMock(return_value={"messages": [{"role": "user", "content": "hello"}]}),
        append_messages_to_working_memory=AsyncMock(),
    )
    inner_agent = SimpleNamespace(input_schema=ChatRequestOrMessage, ainvoke=AsyncMock(return_value="world"))
    memory_config = RedisAgentMemoryBackendConfig(
        do_auto_retry=True,
        num_retries=7,
        retry_on_status_codes=[429, 503],
        retry_on_errors=["Too Many Requests", "temporary"],
    )
    builder = SimpleNamespace(
        get_memory_client_config=lambda _name: memory_config,
        get_function=AsyncMock(return_value=inner_agent),
    )

    with (
        patch(
            "nvidia_nat_redis.redis_agent_memory.auto_memory.register.create_memory_client",
            new=AsyncMock(return_value=client),
        ),
        patch(
            "nvidia_nat_redis.redis_agent_memory.auto_memory.register.patch_with_retry",
            return_value=retry_client,
        ) as patch_retry,
        patch(
            "nvidia_nat_redis.redis_agent_memory.auto_memory.service.Context.get",
            return_value=SimpleNamespace(user_id="runtime-user", conversation_id="runtime-session"),
        ),
    ):
        async with redis_agent_memory_auto_memory(auto_memory_config, builder) as function_info:
            assert await function_info.single_fn(ChatRequestOrMessage(input_message="hello")) == "world"

    patch_retry.assert_called_once_with(
        client,
        retries=7,
        retry_codes=[429, 503],
        retry_on_messages=["Too Many Requests", "temporary"],
    )
    retry_client.memory_prompt.assert_awaited_once()
    client.close.assert_awaited_once()
