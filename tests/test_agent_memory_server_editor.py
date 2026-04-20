# SPDX-FileCopyrightText: Copyright (c) 2026, Redis
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from nat.memory.models import MemoryItem

from nvidia_nat_redis.agent_memory_server import AgentMemoryServerEditor, RedisAgentMemoryServerEditor


@pytest.fixture(name="mock_client")
def mock_client_fixture() -> AsyncMock:
    client = AsyncMock()
    client.create_long_term_memory = AsyncMock()
    client.search_long_term_memory = AsyncMock()
    client.delete_long_term_memories = AsyncMock()
    return client


@pytest.fixture(name="editor")
def editor_fixture(mock_client: AsyncMock) -> RedisAgentMemoryServerEditor:
    return RedisAgentMemoryServerEditor(client=mock_client)


def test_editor_alias() -> None:
    assert AgentMemoryServerEditor is RedisAgentMemoryServerEditor


async def test_add_items_uses_memory_and_metadata(editor: RedisAgentMemoryServerEditor, mock_client: AsyncMock) -> None:
    item = MemoryItem(
        user_id="user-123",
        memory="User prefers concise answers.",
        tags=["preferences", "formatting"],
        metadata={
            "session_id": "session-1",
            "namespace": "nat",
            "entities": ["user"],
            "memory_type": "semantic",
        },
    )

    await editor.add_items([item])

    mock_client.create_long_term_memory.assert_called_once()
    records = mock_client.create_long_term_memory.call_args.args[0]
    assert len(records) == 1
    assert records[0].text == "User prefers concise answers."
    assert records[0].user_id == "user-123"
    assert records[0].session_id == "session-1"
    assert records[0].namespace == "nat"
    assert list(records[0].topics) == ["preferences", "formatting"]
    assert list(records[0].entities) == ["user"]


async def test_add_items_uses_conversation_when_memory_missing(
    editor: RedisAgentMemoryServerEditor, mock_client: AsyncMock
) -> None:
    item = MemoryItem(
        user_id="user-456",
        memory=None,
        conversation=[
            {"role": "user", "content": "I like short summaries."},
            {"role": "assistant", "content": "I will keep responses concise."},
        ],
    )

    await editor.add_items([item], namespace="nat")

    records = mock_client.create_long_term_memory.call_args.args[0]
    assert records[0].text == "I like short summaries. I will keep responses concise."
    assert records[0].namespace == "nat"


async def test_search_requires_user_id(editor: RedisAgentMemoryServerEditor) -> None:
    with pytest.raises(ValueError, match="user_id"):
        await editor.search(query="preferences")


async def test_search_translates_results(editor: RedisAgentMemoryServerEditor, mock_client: AsyncMock) -> None:
    mock_client.search_long_term_memory.return_value = SimpleNamespace(
        memories=[
            SimpleNamespace(
                id="mem-1",
                text="User prefers concise answers.",
                user_id="user-123",
                topics=["preferences"],
                entities=["user"],
                namespace="nat",
                session_id="session-1",
                memory_type=SimpleNamespace(value="semantic"),
                dist=0.12,
                metadata=None,
            )
        ]
    )

    results = await editor.search(
        query="How should I format responses?",
        top_k=3,
        user_id="user-123",
        namespace="nat",
        topics=["preferences"],
    )

    mock_client.search_long_term_memory.assert_called_once()
    call_kwargs = mock_client.search_long_term_memory.call_args.kwargs
    assert call_kwargs["text"] == "How should I format responses?"
    assert call_kwargs["limit"] == 3
    assert call_kwargs["user_id"] == {"eq": "user-123"}
    assert call_kwargs["namespace"] == {"eq": "nat"}
    assert call_kwargs["topics"] == {"any": ["preferences"]}

    assert len(results) == 1
    assert results[0].memory == "User prefers concise answers."
    assert results[0].user_id == "user-123"
    assert results[0].tags == ["preferences"]
    assert results[0].similarity_score == 0.12
    assert results[0].metadata["id"] == "mem-1"
    assert results[0].metadata["namespace"] == "nat"


async def test_remove_items_by_memory_id(editor: RedisAgentMemoryServerEditor, mock_client: AsyncMock) -> None:
    await editor.remove_items(memory_id="mem-1")
    mock_client.delete_long_term_memories.assert_called_once_with(["mem-1"])


async def test_remove_items_by_user_id_searches_and_deletes(
    editor: RedisAgentMemoryServerEditor, mock_client: AsyncMock
) -> None:
    mock_client.search_long_term_memory.side_effect = [
        SimpleNamespace(memories=[SimpleNamespace(id="mem-1"), SimpleNamespace(id="mem-2")]),
        SimpleNamespace(memories=[]),
    ]

    await editor.remove_items(user_id="user-123", batch_size=2)

    assert mock_client.search_long_term_memory.call_count == 2
    mock_client.delete_long_term_memories.assert_called_once_with(["mem-1", "mem-2"])


async def test_remove_items_requires_selector(editor: RedisAgentMemoryServerEditor) -> None:
    with pytest.raises(ValueError, match="requires memory_id"):
        await editor.remove_items()
