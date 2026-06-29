# SPDX-FileCopyrightText: Copyright (c) 2026, Redis
# SPDX-License-Identifier: Apache-2.0

"""Editor-level tests for CloudRedisAgentMemoryEditor against the live cloud service.

Requires env vars (load your .env before running):
  AGENT_MEMORY_ENDPOINT
  AGENT_MEMORY_API_KEY
  AGENT_MEMORY_STORE_ID

    set -a && source .env && set +a
    python -m pytest tests/test_cloud_redis_agent_memory_editor.py -v
"""

from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest
from nat.builder.context import Context
from nat.memory.models import MemoryItem

from nvidia_nat_redis.cloud_redis_agent_memory import CloudRedisAgentMemoryEditor

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def cloud_credentials() -> dict[str, str]:
    endpoint = os.environ.get("AGENT_MEMORY_ENDPOINT")
    api_key = os.environ.get("AGENT_MEMORY_API_KEY")
    store_id = os.environ.get("AGENT_MEMORY_STORE_ID")

    missing = [
        name
        for name, val in [
            ("AGENT_MEMORY_ENDPOINT", endpoint),
            ("AGENT_MEMORY_API_KEY", api_key),
            ("AGENT_MEMORY_STORE_ID", store_id),
        ]
        if not val
    ]

    if missing:
        pytest.skip(f"Cloud credentials not set: {', '.join(missing)}")

    return {"base_url": endpoint, "api_key": api_key, "store_id": store_id}


@pytest.fixture(scope="session")
def editor(cloud_credentials: dict[str, str]) -> CloudRedisAgentMemoryEditor:
    from redis_agent_memory import AgentMemory

    client = AgentMemory(
        cloud_credentials["base_url"],
        api_key=cloud_credentials["api_key"],
        store_id=cloud_credentials["store_id"],
    )
    return CloudRedisAgentMemoryEditor(client=client)


@pytest.fixture
def unique_suffix() -> str:
    return uuid4().hex[:10]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _poll_for_text(
    editor: CloudRedisAgentMemoryEditor,
    *,
    query: str,
    user_id: str,
    namespace: str,
    expected: str,
    timeout: float = 30.0,
) -> list[MemoryItem]:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        results = await editor.search(query=query, user_id=user_id, namespace=namespace, top_k=10)
        if any(expected.lower() in (r.memory or "").lower() for r in results):
            return results
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError(f"Timed out waiting for {expected!r} in results: {[r.memory for r in results]!r}")
        await asyncio.sleep(2.0)


async def _poll_gone(
    editor: CloudRedisAgentMemoryEditor,
    *,
    query: str,
    user_id: str,
    namespace: str,
    expected: str,
    timeout: float = 30.0,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        results = await editor.search(query=query, user_id=user_id, namespace=namespace, top_k=10)
        if not any(expected.lower() in (r.memory or "").lower() for r in results):
            return
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError(f"Timed out waiting for {expected!r} to disappear: {[r.memory for r in results]!r}")
        await asyncio.sleep(2.0)


# ---------------------------------------------------------------------------
# add_items
# ---------------------------------------------------------------------------


async def test_add_items_stores_memory_and_metadata(
    editor: CloudRedisAgentMemoryEditor,
    unique_suffix: str,
) -> None:
    namespace = f"it-add-meta-{unique_suffix}"
    user_id = f"user-{unique_suffix}"

    await editor.add_items(
        [
            MemoryItem(
                user_id=user_id,
                memory="User prefers concise answers.",
                tags=["preferences", "formatting"],
                metadata={"sessionId": "session-1"},
            )
        ],
        namespace=namespace,
    )

    results = await _poll_for_text(
        editor,
        query="response style preferences",
        user_id=user_id,
        namespace=namespace,
        expected="concise",
    )

    match = next(r for r in results if "concise" in (r.memory or "").lower())
    assert match.user_id == user_id
    assert "preferences" in (match.tags or []) or "formatting" in (match.tags or [])
    assert match.metadata.get("namespace") == namespace

    await editor.remove_items(user_id=user_id, namespace=namespace)


async def test_add_items_uses_conversation_when_memory_missing(
    editor: CloudRedisAgentMemoryEditor,
    unique_suffix: str,
) -> None:
    namespace = f"it-add-conv-{unique_suffix}"
    user_id = f"user-{unique_suffix}"

    await editor.add_items(
        [
            MemoryItem(
                user_id=user_id,
                memory=None,
                conversation=[
                    {"role": "user", "content": "I like short summaries."},
                    {"role": "assistant", "content": "I will keep responses concise."},
                ],
            )
        ],
        namespace=namespace,
    )

    results = await _poll_for_text(
        editor,
        query="summary preferences",
        user_id=user_id,
        namespace=namespace,
        expected="short summaries",
    )

    assert any("short summaries" in (r.memory or "").lower() for r in results)

    await editor.remove_items(user_id=user_id, namespace=namespace)


async def test_add_items_uses_runtime_conversation_id_as_session(
    editor: CloudRedisAgentMemoryEditor,
    unique_suffix: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    namespace = f"it-add-ctx-{unique_suffix}"
    user_id = f"user-{unique_suffix}"
    ctx_session = f"session-from-context-{unique_suffix}"

    import types

    monkeypatch.setattr(
        Context, "get", classmethod(lambda cls: types.SimpleNamespace(conversation_id=ctx_session, user_id=None))
    )

    await editor.add_items(
        [MemoryItem(user_id=user_id, memory="Remember my timezone is PST.")],
        namespace=namespace,
    )

    results = await _poll_for_text(
        editor,
        query="timezone",
        user_id=user_id,
        namespace=namespace,
        expected="PST",
    )

    match = next(r for r in results if "pst" in (r.memory or "").lower())
    # sessionId in metadata should match what we set via context
    assert match.metadata.get("sessionId") == ctx_session

    await editor.remove_items(user_id=user_id, namespace=namespace)


async def test_add_items_kwargs_override_per_item_values(
    editor: CloudRedisAgentMemoryEditor,
    unique_suffix: str,
) -> None:
    namespace = f"it-add-override-{unique_suffix}"
    item_user = f"item-user-{unique_suffix}"
    override_user = f"override-user-{unique_suffix}"

    await editor.add_items(
        [MemoryItem(user_id=item_user, memory="Override test memory.", tags=["item-topic"])],
        user_id=override_user,
        namespace=namespace,
        topics=["override-topic"],
    )

    # Should be searchable under override_user, not item_user
    results = await _poll_for_text(
        editor,
        query="override test",
        user_id=override_user,
        namespace=namespace,
        expected="override test",
    )
    assert results

    # Should not appear under original item_user
    results_item = await editor.search(query="override test", user_id=item_user, namespace=namespace, top_k=5)
    assert not any("override test" in (r.memory or "").lower() for r in results_item)

    await editor.remove_items(user_id=override_user, namespace=namespace)


async def test_add_items_each_record_gets_unique_id(
    editor: CloudRedisAgentMemoryEditor,
    unique_suffix: str,
) -> None:
    namespace = f"it-add-ids-{unique_suffix}"
    user_id = f"user-{unique_suffix}"
    texts = ["Memory alpha.", "Memory beta.", "Memory gamma."]

    await editor.add_items(
        [MemoryItem(user_id=user_id, memory=t) for t in texts],
        namespace=namespace,
    )

    await _poll_for_text(editor, query="Memory alpha", user_id=user_id, namespace=namespace, expected="alpha")

    results = await editor.search(query="Memory", user_id=user_id, namespace=namespace, top_k=10)
    ids = [r.metadata["id"] for r in results if r.metadata.get("id")]
    assert len(set(ids)) == len(ids), f"Duplicate IDs found: {ids}"

    await editor.remove_items(user_id=user_id, namespace=namespace)


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


async def test_search_requires_user_id(editor: CloudRedisAgentMemoryEditor) -> None:
    with pytest.raises(ValueError, match="user_id"):
        await editor.search(query="preferences")


async def test_search_uses_runtime_user_id_from_context(
    editor: CloudRedisAgentMemoryEditor,
    unique_suffix: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import types

    namespace = f"it-search-ctx-{unique_suffix}"
    user_id = f"ctx-user-{unique_suffix}"

    await editor.add_items(
        [MemoryItem(user_id=user_id, memory="Context user memory.")],
        namespace=namespace,
    )
    await _poll_for_text(editor, query="context user", user_id=user_id, namespace=namespace, expected="context user")

    monkeypatch.setattr(Context, "get", classmethod(lambda cls: types.SimpleNamespace(user_id=user_id)))

    results = await editor.search(query="context user", namespace=namespace, top_k=5)
    assert any("context user" in (r.memory or "").lower() for r in results)

    await editor.remove_items(user_id=user_id, namespace=namespace)


async def test_search_result_fields_are_populated(
    editor: CloudRedisAgentMemoryEditor,
    unique_suffix: str,
) -> None:
    namespace = f"it-search-fields-{unique_suffix}"
    user_id = f"user-{unique_suffix}"

    await editor.add_items(
        [MemoryItem(user_id=user_id, memory="User prefers concise answers.", tags=["preferences"])],
        namespace=namespace,
    )

    results = await _poll_for_text(
        editor,
        query="response format preferences",
        user_id=user_id,
        namespace=namespace,
        expected="concise",
    )

    item = next(r for r in results if "concise" in (r.memory or "").lower())
    assert item.user_id == user_id
    assert item.memory
    assert isinstance(item.metadata, dict)
    assert "id" in item.metadata
    assert item.metadata.get("namespace") == namespace

    await editor.remove_items(user_id=user_id, namespace=namespace)


async def test_search_namespace_filter_isolates_results(
    editor: CloudRedisAgentMemoryEditor,
    unique_suffix: str,
) -> None:
    ns_a = f"it-ns-a-{unique_suffix}"
    ns_b = f"it-ns-b-{unique_suffix}"
    user_id = f"user-{unique_suffix}"

    await editor.add_items(
        [MemoryItem(user_id=user_id, memory="Namespace isolation test phrase.")],
        namespace=ns_a,
    )
    await _poll_for_text(editor, query="isolation", user_id=user_id, namespace=ns_a, expected="isolation")

    results_b = await editor.search(query="isolation", user_id=user_id, namespace=ns_b, top_k=5)
    assert not any("isolation" in (r.memory or "").lower() for r in results_b)

    await editor.remove_items(user_id=user_id, namespace=ns_a)


# ---------------------------------------------------------------------------
# remove_items
# ---------------------------------------------------------------------------


async def test_remove_items_by_memory_id(
    editor: CloudRedisAgentMemoryEditor,
    unique_suffix: str,
) -> None:
    namespace = f"it-rm-id-{unique_suffix}"
    user_id = f"user-{unique_suffix}"

    await editor.add_items(
        [MemoryItem(user_id=user_id, memory="Delete by ID test.")],
        namespace=namespace,
    )
    results = await _poll_for_text(
        editor, query="delete by id", user_id=user_id, namespace=namespace, expected="delete by id"
    )
    record_id = results[0].metadata["id"]

    await editor.remove_items(memory_id=record_id)

    await _poll_gone(editor, query="delete by id", user_id=user_id, namespace=namespace, expected="delete by id")


async def test_remove_items_by_user_id_searches_and_deletes(
    editor: CloudRedisAgentMemoryEditor,
    unique_suffix: str,
) -> None:
    namespace = f"it-rm-user-{unique_suffix}"
    user_id = f"user-{unique_suffix}"

    await editor.add_items(
        [
            MemoryItem(user_id=user_id, memory="First memory to delete."),
            MemoryItem(user_id=user_id, memory="Second memory to delete."),
        ],
        namespace=namespace,
    )
    await _poll_for_text(
        editor, query="memory to delete", user_id=user_id, namespace=namespace, expected="first memory"
    )

    await editor.remove_items(user_id=user_id, namespace=namespace)

    await _poll_gone(
        editor, query="memory to delete", user_id=user_id, namespace=namespace, expected="memory to delete"
    )


async def test_remove_items_requires_selector(editor: CloudRedisAgentMemoryEditor) -> None:
    with pytest.raises(ValueError, match="requires memory_id"):
        await editor.remove_items()


async def test_remove_items_paginates_beyond_first_batch(
    editor: CloudRedisAgentMemoryEditor,
    unique_suffix: str,
) -> None:
    """Filtered deletion must clear ALL matches, not just the first page.

    Regression test: with ``batch_size`` smaller than the record count, the
    delete loop has to advance through pages. If pagination is broken, only the
    first ``batch_size`` records are deleted and the rest survive.
    """
    namespace = f"it-rm-page-{unique_suffix}"
    user_id = f"user-{unique_suffix}"
    record_count = 5

    await editor.add_items(
        [MemoryItem(user_id=user_id, memory=f"Pagination delete record number {i}.") for i in range(record_count)],
        namespace=namespace,
    )

    # Wait until all records are indexed before deleting.
    deadline = asyncio.get_running_loop().time() + 30.0
    while True:
        results = await editor.search(query="pagination delete record", user_id=user_id, namespace=namespace, top_k=20)
        indexed = [r for r in results if "pagination delete record" in (r.memory or "").lower()]
        if len(indexed) >= record_count:
            break
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError(f"Only {len(indexed)}/{record_count} records indexed before deletion.")
        await asyncio.sleep(2.0)

    # batch_size < record_count forces the delete loop to span multiple pages.
    await editor.remove_items(user_id=user_id, namespace=namespace, batch_size=2)

    await _poll_gone(
        editor,
        query="pagination delete record",
        user_id=user_id,
        namespace=namespace,
        expected="pagination delete record",
    )


async def test_search_result_metadata_includes_service_timestamps(
    editor: CloudRedisAgentMemoryEditor,
    unique_suffix: str,
) -> None:
    """Search results must surface the cloud-assigned ``createdAt``/``updatedAt``.

    Regression test: the cloud SDK exposes snake_case attributes, so reading
    camelCase attribute names off the record silently yields ``None`` and the
    timestamp metadata is dropped.
    """
    namespace = f"it-meta-ts-{unique_suffix}"
    user_id = f"user-{unique_suffix}"

    await editor.add_items(
        [MemoryItem(user_id=user_id, memory="Timestamp metadata check phrase.")],
        namespace=namespace,
    )

    results = await _poll_for_text(
        editor,
        query="timestamp metadata check",
        user_id=user_id,
        namespace=namespace,
        expected="timestamp metadata",
    )

    match = next(r for r in results if "timestamp metadata" in (r.memory or "").lower())
    assert match.metadata.get("createdAt") is not None, match.metadata
    assert match.metadata.get("updatedAt") is not None, match.metadata

    await editor.remove_items(user_id=user_id, namespace=namespace)
