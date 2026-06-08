# SPDX-FileCopyrightText: Copyright (c) 2026, Redis
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
from pathlib import Path
from textwrap import dedent

import pytest
from agent_memory_client import create_memory_client
from nat.memory.models import MemoryItem
from nat.utils import run_workflow

from nvidia_nat_redis.redis_agent_memory import RedisAgentMemoryEditor
from nvidia_nat_redis.redis_agent_memory.memory import RedisAgentMemoryBackendConfig

pytestmark = [
    pytest.mark.filterwarnings(
        "ignore:get_working_memory is deprecated and will be removed in a future version.*:DeprecationWarning"
    ),
    pytest.mark.filterwarnings(
        "ignore:Calling \\.text\\(\\) as a method is deprecated.*:"
        "langchain_core._api.deprecation.LangChainDeprecationWarning"
    ),
]


async def _wait_for_results(
    editor: RedisAgentMemoryEditor,
    *,
    query: str,
    user_id: str,
    namespace: str,
    expected_substring: str,
    timeout_seconds: float = 20.0,
) -> list[MemoryItem]:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    expected = expected_substring.lower()

    while True:
        results = await editor.search(
            query=query,
            user_id=user_id,
            namespace=namespace,
            top_k=5,
        )
        if any((result.memory or "").lower().find(expected) >= 0 for result in results):
            return results
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError(f"Timed out waiting for search results containing {expected_substring!r}: {results!r}")
        await asyncio.sleep(1.0)


async def _wait_for_no_results(
    editor: RedisAgentMemoryEditor,
    *,
    query: str,
    user_id: str,
    namespace: str,
    expected_substring: str,
    timeout_seconds: float = 20.0,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    expected = expected_substring.lower()

    while True:
        results = await editor.search(
            query=query,
            user_id=user_id,
            namespace=namespace,
            top_k=5,
        )
        if not any((result.memory or "").lower().find(expected) >= 0 for result in results):
            return
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError(
                f"Timed out waiting for results containing {expected_substring!r} to disappear: {results!r}"
            )
        await asyncio.sleep(1.0)


def _write_config(tmp_path: Path, filename: str, content: str) -> Path:
    config_path = tmp_path / filename
    config_path.write_text(dedent(content).strip() + "\n", encoding="utf-8")
    return config_path


@pytest.mark.asyncio
async def test_working_memory_round_trip_against_real_ams(
    local_ams_stack: dict[str, str],
    unique_suffix: str,
) -> None:
    namespace = f"it-working-{unique_suffix}"
    user_id = f"user-{unique_suffix}"
    session_id = f"session-{unique_suffix}"

    config = RedisAgentMemoryBackendConfig(
        base_url=local_ams_stack["ams_url"],
        default_namespace=namespace,
        timeout=30.0,
    )
    client = await create_memory_client(
        base_url=config.base_url,
        timeout=config.timeout,
        default_namespace=config.default_namespace,
        default_model_name=config.default_model_name,
        default_context_window_max=config.default_context_window_max,
    )

    try:
        created, memory = await client.get_or_create_working_memory(
            session_id=session_id,
            user_id=user_id,
            namespace=namespace,
            model_name="gpt-4o-mini",
        )
        assert created is True
        assert memory.session_id == session_id
        assert memory.user_id == user_id

        await client.append_messages_to_working_memory(
            session_id=session_id,
            namespace=namespace,
            user_id=user_id,
            messages=[
                {"role": "user", "content": "Remember that I like tea."},
                {"role": "assistant", "content": "I will remember that."},
            ],
        )

        created_again, updated_memory = await client.get_or_create_working_memory(
            session_id=session_id,
            user_id=user_id,
            namespace=namespace,
            model_name="gpt-4o-mini",
        )
        assert created_again is False
        assert [message.role for message in updated_memory.messages][-2:] == ["user", "assistant"]
        assert updated_memory.messages[-2].content == "Remember that I like tea."
        assert updated_memory.messages[-1].content == "I will remember that."
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_editor_round_trip_against_real_ams(
    local_ams_stack_with_api: dict[str, str],
    unique_suffix: str,
) -> None:
    namespace = f"it-editor-{unique_suffix}"
    user_id = f"user-{unique_suffix}"
    session_id = f"session-{unique_suffix}"
    memory_text = "User prefers jasmine green tea after lunch."

    config = RedisAgentMemoryBackendConfig(
        base_url=local_ams_stack_with_api["ams_url"],
        default_namespace=namespace,
        timeout=30.0,
    )
    client = await create_memory_client(
        base_url=config.base_url,
        timeout=config.timeout,
        default_namespace=config.default_namespace,
        default_model_name=config.default_model_name,
        default_context_window_max=config.default_context_window_max,
    )
    editor = RedisAgentMemoryEditor(client=client)

    try:
        await editor.add_items(
            [
                MemoryItem(
                    user_id=user_id,
                    memory=memory_text,
                    tags=["preferences", "tea"],
                    metadata={
                        "session_id": session_id,
                        "namespace": namespace,
                        "entities": ["tea", "lunch"],
                        "memory_type": "semantic",
                    },
                )
            ]
        )

        results = await _wait_for_results(
            editor,
            query="Which tea does the user prefer after lunch?",
            user_id=user_id,
            namespace=namespace,
            expected_substring="jasmine green tea",
        )

        match = next(result for result in results if "jasmine green tea" in (result.memory or "").lower())
        assert match.user_id == user_id
        assert match.tags == ["preferences", "tea"]
        assert match.metadata["namespace"] == namespace

        await editor.remove_items(memory_id=match.metadata["id"])
        await _wait_for_no_results(
            editor,
            query="Which tea does the user prefer after lunch?",
            user_id=user_id,
            namespace=namespace,
            expected_substring="jasmine green tea",
        )
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_auto_memory_workflow_round_trip_with_openai(
    local_ams_stack_with_api: dict[str, str],
    openai_api_key: str,
    openai_model_name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    unique_suffix: str,
) -> None:
    namespace = f"it-auto-{unique_suffix}"
    user_id = f"user-{unique_suffix}"
    conversation_id = f"conversation-{unique_suffix}"

    monkeypatch.setenv("OPENAI_API_KEY", openai_api_key)
    config_path = _write_config(
        tmp_path,
        "auto_memory.yml",
        f"""
        general:
          telemetry:
            enabled: false

        llms:
          openai_llm:
            _type: openai
            model_name: {openai_model_name}
            temperature: 0.0
            max_tokens: 128

        functions:
          assistant_chat:
            _type: chat_completion
            llm_name: openai_llm
            system_prompt: >-
              You are a precise assistant. When the user asks for a stored fact,
              answer with only the requested fact and no extra wording.

        memory:
          redis_ltm:
            _type: redis_agent_memory_backend
            base_url: {local_ams_stack_with_api["ams_url"]}
            default_namespace: {namespace}

        workflow:
          _type: redis_agent_memory_auto_memory
          inner_agent_name: assistant_chat
          memory_name: redis_ltm
          default_user_id: fallback-user
          default_session_id: fallback-session
          memory_prompt:
            optimize_query: false
            long_term_search:
              limit: 5
          working_memory:
            namespace: {namespace}
            model_name: {openai_model_name}
            ttl_seconds: 86400
            long_term_memory_strategy:
              strategy: discrete
        """,
    )

    first = await run_workflow(
        config_file=config_path,
        prompt="Remember that my favorite tea is oolong. Reply with only stored.",
        to_type=str,
        session_kwargs={"user_id": user_id, "conversation_id": conversation_id},
    )
    second = await run_workflow(
        config_file=config_path,
        prompt="What is my favorite tea? Reply with only the tea name.",
        to_type=str,
        session_kwargs={"user_id": user_id, "conversation_id": conversation_id},
    )

    assert "stored" in first.lower()
    assert "oolong" in second.lower()


@pytest.mark.asyncio
async def test_tool_based_workflow_uses_real_memory_tools_with_openai(
    local_ams_stack_with_api: dict[str, str],
    openai_api_key: str,
    openai_model_name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    unique_suffix: str,
) -> None:
    namespace = f"it-tool-{unique_suffix}"
    user_id = f"user-{unique_suffix}"
    conversation_id = f"conversation-{unique_suffix}"
    preference = "Answer in exactly three words when possible."

    monkeypatch.setenv("OPENAI_API_KEY", openai_api_key)
    config_path = _write_config(
        tmp_path,
        "tool_memory.yml",
        f"""
        general:
          telemetry:
            enabled: false

        llms:
          openai_llm:
            _type: openai
            model_name: {openai_model_name}
            temperature: 0.0
            max_tokens: 256

        memory:
          redis_memory:
            _type: redis_agent_memory_backend
            base_url: {local_ams_stack_with_api["ams_url"]}
            default_namespace: {namespace}

        functions:
          get_memory:
            _type: get_memory
            memory: redis_memory
            description: |
              Always check memory for user preferences before answering.
              Use the active user_id from the conversation context.

          add_memory:
            _type: add_memory
            memory: redis_memory
            description: |
              Add durable user preferences and facts to long-term memory whenever the
              user shares them.

        workflow:
          _type: react_agent
          tool_names: [get_memory, add_memory]
          description: "A chat agent using Redis Agent Memory for long-term memory"
          llm_name: openai_llm
        """,
    )

    first = await run_workflow(
        config_file=config_path,
        prompt=(
            "Use the add_memory tool to save this exact preference for the active user: "
            f"'{preference}' After the tool succeeds, respond with only saved."
        ),
        to_type=str,
        session_kwargs={"user_id": user_id, "conversation_id": conversation_id},
    )
    second = await run_workflow(
        config_file=config_path,
        prompt=(
            "Use the get_memory tool to retrieve the user's saved preference. "
            "Then answer with only the saved preference text."
        ),
        to_type=str,
        session_kwargs={"user_id": user_id, "conversation_id": conversation_id},
    )

    assert "saved" in first.lower()
    assert "exactly three words" in second.lower()
