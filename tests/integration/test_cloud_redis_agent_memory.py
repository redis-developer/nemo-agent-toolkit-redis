# SPDX-FileCopyrightText: Copyright (c) 2026, Redis
# SPDX-License-Identifier: Apache-2.0

"""Integration tests against the live Redis Agent Memory cloud service.

Run with credentials loaded from .env:

    set -a && source .env && set +a
    python -m pytest tests/integration/test_cloud_redis_agent_memory.py -v
"""

from __future__ import annotations

import asyncio

import pytest
from nat.memory.models import MemoryItem

from nvidia_nat_redis.cloud_redis_agent_memory import CloudRedisAgentMemoryEditor
from nvidia_nat_redis.cloud_redis_agent_memory.memory import CloudRedisAgentMemoryBackendConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _wait_for_results(
    editor: CloudRedisAgentMemoryEditor,
    *,
    query: str,
    user_id: str,
    namespace: str,
    expected_substring: str,
    timeout_seconds: float = 30.0,
) -> list[MemoryItem]:
    """Poll search until ``expected_substring`` appears in a result, or timeout."""
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    expected = expected_substring.lower()

    while True:
        results = await editor.search(query=query, user_id=user_id, namespace=namespace, top_k=5)
        if any((item.memory or "").lower().find(expected) >= 0 for item in results):
            return results
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError(
                f"Timed out waiting for search results containing {expected_substring!r}.\n"
                f"Last results: {[item.memory for item in results]!r}"
            )
        await asyncio.sleep(2.0)


async def _wait_for_no_results(
    editor: CloudRedisAgentMemoryEditor,
    *,
    query: str,
    user_id: str,
    namespace: str,
    expected_substring: str,
    timeout_seconds: float = 30.0,
) -> None:
    """Poll search until ``expected_substring`` no longer appears, or timeout."""
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    expected = expected_substring.lower()

    while True:
        results = await editor.search(query=query, user_id=user_id, namespace=namespace, top_k=5)
        if not any((item.memory or "").lower().find(expected) >= 0 for item in results):
            return
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError(
                f"Timed out waiting for {expected_substring!r} to disappear from results.\n"
                f"Last results: {[item.memory for item in results]!r}"
            )
        await asyncio.sleep(2.0)


def _make_editor(cloud_ams_stack: dict[str, str]) -> CloudRedisAgentMemoryEditor:
    from redis_agent_memory import AgentMemory

    client = AgentMemory(
        cloud_ams_stack["base_url"],
        api_key=cloud_ams_stack["api_key"],
        store_id=cloud_ams_stack["store_id"],
    )
    return CloudRedisAgentMemoryEditor(client=client)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_and_search_round_trip(
    cloud_ams_stack: dict[str, str],
    unique_suffix: str,
) -> None:
    """Add a memory item, verify it appears in search, then clean up."""
    namespace = f"it-cloud-{unique_suffix}"
    user_id = f"user-{unique_suffix}"
    memory_text = "User enjoys long hikes in the mountains on weekends."

    editor = _make_editor(cloud_ams_stack)

    await editor.add_items(
        [
            MemoryItem(
                user_id=user_id,
                memory=memory_text,
                tags=["hiking", "outdoors"],
            )
        ],
        namespace=namespace,
    )

    results = await _wait_for_results(
        editor,
        query="What outdoor activities does the user enjoy?",
        user_id=user_id,
        namespace=namespace,
        expected_substring="hikes",
    )

    match = next(r for r in results if "hikes" in (r.memory or "").lower())
    assert match.user_id == user_id
    assert "hiking" in (match.tags or []) or "outdoors" in (match.tags or [])
    assert match.metadata.get("namespace") == namespace

    # Clean up
    await editor.remove_items(memory_id=match.metadata["id"])
    await _wait_for_no_results(
        editor,
        query="What outdoor activities does the user enjoy?",
        user_id=user_id,
        namespace=namespace,
        expected_substring="hikes",
    )


@pytest.mark.asyncio
async def test_search_respects_namespace_isolation(
    cloud_ams_stack: dict[str, str],
    unique_suffix: str,
) -> None:
    """A memory written to namespace A must not appear in a search against namespace B."""
    namespace_a = f"it-cloud-ns-a-{unique_suffix}"
    namespace_b = f"it-cloud-ns-b-{unique_suffix}"
    user_id = f"user-{unique_suffix}"
    memory_text = "User drinks exactly three espressos every morning."

    editor = _make_editor(cloud_ams_stack)

    await editor.add_items(
        [MemoryItem(user_id=user_id, memory=memory_text)],
        namespace=namespace_a,
    )

    # Confirm it's in namespace A
    await _wait_for_results(
        editor,
        query="coffee habits",
        user_id=user_id,
        namespace=namespace_a,
        expected_substring="espresso",
    )

    # Must NOT appear in namespace B
    results_b = await editor.search(
        query="coffee habits",
        user_id=user_id,
        namespace=namespace_b,
        top_k=5,
    )
    assert not any("espresso" in (r.memory or "").lower() for r in results_b), (
        f"Memory from namespace_a leaked into namespace_b: {[r.memory for r in results_b]!r}"
    )

    # Clean up namespace A
    await editor.remove_items(user_id=user_id, namespace=namespace_a)


@pytest.mark.asyncio
async def test_add_multiple_items_all_searchable(
    cloud_ams_stack: dict[str, str],
    unique_suffix: str,
) -> None:
    """Multiple distinct items added in one call are each independently searchable."""
    namespace = f"it-cloud-multi-{unique_suffix}"
    user_id = f"user-{unique_suffix}"

    editor = _make_editor(cloud_ams_stack)

    items = [
        MemoryItem(user_id=user_id, memory="User is allergic to peanuts."),
        MemoryItem(user_id=user_id, memory="User speaks fluent Japanese."),
    ]
    await editor.add_items(items, namespace=namespace)

    await _wait_for_results(
        editor,
        query="food allergies",
        user_id=user_id,
        namespace=namespace,
        expected_substring="peanuts",
    )
    await _wait_for_results(
        editor,
        query="languages the user knows",
        user_id=user_id,
        namespace=namespace,
        expected_substring="japanese",
    )

    # Clean up
    await editor.remove_items(user_id=user_id, namespace=namespace)


@pytest.mark.asyncio
async def test_remove_items_by_user_id_deletes_all_records(
    cloud_ams_stack: dict[str, str],
    unique_suffix: str,
) -> None:
    """remove_items filtered by user_id clears all records for that user in the namespace."""
    namespace = f"it-cloud-rm-{unique_suffix}"
    user_id = f"user-{unique_suffix}"

    editor = _make_editor(cloud_ams_stack)

    await editor.add_items(
        [
            MemoryItem(user_id=user_id, memory="User prefers window seats on flights."),
            MemoryItem(user_id=user_id, memory="User always orders aisle seats on trains."),
        ],
        namespace=namespace,
    )

    # Wait for at least one to be indexed
    await _wait_for_results(
        editor,
        query="seating preferences",
        user_id=user_id,
        namespace=namespace,
        expected_substring="seat",
    )

    await editor.remove_items(user_id=user_id, namespace=namespace)

    await _wait_for_no_results(
        editor,
        query="seating preferences",
        user_id=user_id,
        namespace=namespace,
        expected_substring="seat",
    )


@pytest.mark.asyncio
async def test_search_returns_empty_when_no_records_exist(
    cloud_ams_stack: dict[str, str],
    unique_suffix: str,
) -> None:
    """Searching a namespace that has never had records returns an empty list."""
    editor = _make_editor(cloud_ams_stack)

    results = await editor.search(
        query="anything at all",
        user_id=f"user-{unique_suffix}",
        namespace=f"it-cloud-empty-{unique_suffix}",
        top_k=5,
    )

    assert results == []


@pytest.mark.asyncio
async def test_search_result_fields_are_populated(
    cloud_ams_stack: dict[str, str],
    unique_suffix: str,
) -> None:
    """Verify the full MemoryItem returned from search has expected field values."""
    namespace = f"it-cloud-fields-{unique_suffix}"
    user_id = f"user-{unique_suffix}"
    memory_text = "User reads science fiction novels before bed."

    editor = _make_editor(cloud_ams_stack)

    await editor.add_items(
        [MemoryItem(user_id=user_id, memory=memory_text, tags=["reading", "sci-fi"])],
        namespace=namespace,
    )

    results = await _wait_for_results(
        editor,
        query="reading habits",
        user_id=user_id,
        namespace=namespace,
        expected_substring="science fiction",
    )

    item = next(r for r in results if "science fiction" in (r.memory or "").lower())
    assert item.user_id == user_id
    assert item.memory
    assert isinstance(item.metadata, dict)
    assert "id" in item.metadata
    assert item.metadata.get("namespace") == namespace

    # Clean up
    await editor.remove_items(memory_id=item.metadata["id"])


@pytest.mark.asyncio
async def test_config_connects_via_env_vars(
    cloud_ams_stack: dict[str, str],
    unique_suffix: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CloudRedisAgentMemoryBackendConfig picks up credentials from env vars."""
    import os

    monkeypatch.setenv("AGENT_MEMORY_API_KEY", cloud_ams_stack["api_key"])
    monkeypatch.setenv("AGENT_MEMORY_STORE_ID", cloud_ams_stack["store_id"])  # matches .env key

    from redis_agent_memory import AgentMemory

    config = CloudRedisAgentMemoryBackendConfig(base_url=cloud_ams_stack["base_url"])
    assert config.api_key is None  # not set on config, comes from env in factory
    assert config.store_id is None  # same

    # Construct manually to verify env-var path reaches the service
    api_key = os.environ["AGENT_MEMORY_API_KEY"]
    store_id = os.environ["AGENT_MEMORY_STORE_ID"]
    client = AgentMemory(config.base_url, api_key=api_key, store_id=store_id)
    editor = CloudRedisAgentMemoryEditor(client=client)

    namespace = f"it-cloud-env-{unique_suffix}"
    user_id = f"user-{unique_suffix}"

    await editor.add_items(
        [MemoryItem(user_id=user_id, memory="Env var auth test.", tags=["test"])],
        namespace=namespace,
    )
    await _wait_for_results(
        editor,
        query="auth test",
        user_id=user_id,
        namespace=namespace,
        expected_substring="auth test",
    )
    await editor.remove_items(user_id=user_id, namespace=namespace)
