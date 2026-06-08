# SPDX-FileCopyrightText: Copyright (c) 2026, Redis
# SPDX-License-Identifier: Apache-2.0

"""Integration tests verifying that developers using NeMo Agent Toolkit can create and use
memory tools backed by the standard Redis integration (nat.plugins.redis).

Requirements
------------
- A running Redis Stack instance (redis/redis-stack or Redis with RediSearch + RedisJSON).
  Default: ``localhost:6379``.  Override with the ``REDIS_URL`` environment variable.
- No Docker required; tests connect directly to the local Redis instance.

Embedding approach
------------------
Embeddings are produced by a ``redisvl.utils.vectorize.CustomVectorizer`` wrapping the
module-level ``_embed`` function, which returns hardcoded orthogonal unit vectors.
This matches the style used in redis-vl-python's own integration tests: a plain callable
is registered with ``CustomVectorizer`` and exposed as a session-scoped ``vectorizer``
fixture.  No external embedding API is required for Tier 1 tests.

Run
---
    python -m pytest tests/integration/test_nat_redis_memory_tools.py

All tests run by default — no flags required.  The ``redis_client`` fixture skips
gracefully if Redis is unreachable; the ``openai_api_key`` fixture skips the two
workflow tests if ``OPENAI_API_KEY`` is not set.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from textwrap import dedent
from urllib.parse import urlparse

import pytest
import redis.asyncio as aioredis
import redis.exceptions as redis_exceptions
from nat.memory.models import MemoryItem
from redisvl.index import AsyncSearchIndex
from redisvl.schema import IndexSchema
from redisvl.utils.vectorize import CustomVectorizer

from nat.plugins.redis.redis_editor import RedisEditor
from nat.plugins.redis.schema import INDEX_NAME, ensure_index_exists

# ---------------------------------------------------------------------------
# Hardcoded embedding vectors
#
# Each is a 6-dimensional orthogonal unit vector assigned to a specific memory
# string.  Orthogonality guarantees that the L2 distance between any two
# *distinct* vectors is always sqrt(2) ≈ 1.41, making KNN ranking perfectly
# deterministic without any external embedding service.
#
# Query strings are mapped to the vector of the "correct" answer so the nearest
# neighbour is always the item we expect to rank first.
# ---------------------------------------------------------------------------

EMBEDDING_DIM = 6

VEC_ALLERGY = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0]  # "User is allergic to peanuts"
VEC_TRAVEL = [0.0, 1.0, 0.0, 0.0, 0.0, 0.0]  # "User prefers window seats on flights"
VEC_CODING = [0.0, 0.0, 1.0, 0.0, 0.0, 0.0]  # "User's favourite language is Python"
VEC_COFFEE = [0.0, 0.0, 0.0, 1.0, 0.0, 0.0]  # "User drinks espresso every morning"
VEC_MUSIC = [0.0, 0.0, 0.0, 0.0, 1.0, 0.0]  # "User is learning to play the guitar"
VEC_DEFAULT = [0.0, 0.0, 0.0, 0.0, 0.0, 1.0]  # fallback for any unmapped text

QUERY_ALLERGY = "Does the user have any food allergies?"
QUERY_COFFEE = "What does the user drink in the morning?"

_TEXT_TO_VEC: dict[str, list[float]] = {
    "User is allergic to peanuts": VEC_ALLERGY,
    "User prefers window seats on flights": VEC_TRAVEL,
    "User's favourite language is Python": VEC_CODING,
    "User drinks espresso every morning": VEC_COFFEE,
    "User is learning to play the guitar": VEC_MUSIC,
    QUERY_ALLERGY: VEC_ALLERGY,
    QUERY_COFFEE: VEC_COFFEE,
}


def _embed(text: str) -> list[float]:
    """Return a hardcoded vector for known texts; fall back to VEC_DEFAULT.

    This is the callable passed to ``CustomVectorizer`` — a single plain
    function, exactly as used in redis-vl-python's integration tests.
    """
    return _TEXT_TO_VEC.get(text, VEC_DEFAULT)


# ---------------------------------------------------------------------------
# Adapter: bridges redisvl's CustomVectorizer to RedisEditor's LangChain-style
# Embeddings interface (aembed_query / aembed_documents).
# ---------------------------------------------------------------------------


class _VectorizerEmbedder:
    """Wraps a ``CustomVectorizer`` to satisfy ``RedisEditor``'s embedder interface.

    ``RedisEditor`` calls ``aembed_query`` / ``aembed_documents``.
    ``CustomVectorizer`` exposes ``embed`` / ``embed_many`` / ``aembed`` / ``aembed_many``.
    This adapter maps one API to the other.
    """

    def __init__(self, vectorizer: CustomVectorizer) -> None:
        self._v = vectorizer

    def embed_query(self, text: str) -> list[float]:
        return self._v.embed(text)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._v.embed_many(texts)

    async def aembed_query(self, text: str) -> list[float]:
        return await self._v.aembed(text)

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return await self._v.aembed_many(texts)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _build_index_schema(key_prefix: str, embedding_dim: int = EMBEDDING_DIM) -> IndexSchema:
    """Build a redisvl IndexSchema for test fixtures.

    Mirrors the production schema in schema.py: user_id and tags as TagFields,
    memory as TextField, embedding as HNSW VectorField.  Defined inline so that
    the tests do not depend on the production schema.py's compiled .pyc state.
    """
    return IndexSchema.from_dict(
        {
            "index": {
                "name": INDEX_NAME,
                "prefix": f"{key_prefix}:memory",
                "key_separator": ":",
                "storage_type": "json",
            },
            "fields": [
                {"name": "user_id", "type": "tag"},
                {"name": "tags", "type": "tag", "path": "$.tags[*]"},
                {"name": "memory", "type": "text"},
                {
                    "name": "embedding",
                    "type": "vector",
                    "attrs": {
                        "algorithm": "hnsw",
                        "datatype": "float32",
                        "dims": embedding_dim,
                        "distance_metric": "l2",
                        "initial_cap": 100,
                        "m": 16,
                        "ef_construction": 200,
                        "ef_runtime": 10,
                    },
                },
            ],
        }
    )


async def _force_recreate_index(
    client: aioredis.Redis, key_prefix: str, embedding_dim: int = EMBEDDING_DIM
) -> AsyncSearchIndex:
    """Drop any existing index and create a fresh one via redisvl's AsyncSearchIndex.

    Returns the AsyncSearchIndex so callers can pass it directly to RedisEditor.
    ``create(overwrite=True, drop=True)`` deletes both the index definition and
    all associated document keys before creating the new index.
    """
    schema = _build_index_schema(key_prefix, embedding_dim)
    index = AsyncSearchIndex(schema=schema, redis_client=client)
    await index.create(overwrite=True, drop=True)
    return index


def _parse_redis_url(url: str) -> tuple[str, int]:
    parsed = urlparse(url)
    return parsed.hostname or "127.0.0.1", parsed.port or 6379


def _write_config(tmp_path: Path, filename: str, content: str) -> Path:
    config_path = tmp_path / filename
    config_path.write_text(dedent(content).strip() + "\n", encoding="utf-8")
    return config_path


@pytest.fixture(scope="session")
def vectorizer() -> CustomVectorizer:
    """Session-scoped CustomVectorizer backed by hardcoded orthogonal unit vectors.

    Mirrors the pattern used in redis-vl-python's integration tests: a single
    ``embed`` callable is passed to ``CustomVectorizer``; no external API needed.
    """
    return CustomVectorizer(embed=_embed)


@pytest.fixture()
async def redis_client(local_redis_params: tuple[str, int]):
    """Function-scoped async Redis client. Skips if Redis is unreachable."""
    host, port = local_redis_params
    client = aioredis.Redis(
        host=host,
        port=port,
        db=0,
        decode_responses=True,
        socket_timeout=3.0,
        socket_connect_timeout=3.0,
    )
    try:
        await client.ping()
    except (redis_exceptions.ConnectionError, redis_exceptions.TimeoutError) as exc:
        await client.aclose()
        pytest.skip(f"Redis not reachable at {host}:{port} — {exc}")
    yield client
    await client.aclose()


@pytest.fixture()
async def redis_editor(
    redis_client: aioredis.Redis,
    vectorizer: CustomVectorizer,
    unique_suffix: str,
):
    """RedisEditor backed by local Redis and the session-scoped CustomVectorizer.

    The RediSearch index is dropped and recreated using the inline schema defined
    in this test file — bypassing schema.py so that stale .pyc files cannot
    interfere with the field types used.
    """
    key_prefix = f"nat_it_{unique_suffix}"
    index = await _force_recreate_index(redis_client, key_prefix)

    editor = RedisEditor(index=index, embedder=_VectorizerEmbedder(vectorizer))

    yield editor

    # Teardown: clear all documents then drop the index so subsequent tests
    # start with a clean slate.
    await editor.remove_items()
    try:
        await index.delete(drop=True)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Type-registry — no Redis required, runs in any pytest session
# ---------------------------------------------------------------------------


async def test_redis_memory_type_registered_in_nat() -> None:
    """``redis_memory`` must appear in NAT's global type registry so developers
    can reference ``_type: redis_memory`` in YAML workflow configs.
    """
    from nat.cli.type_registry import GlobalTypeRegistry

    import nat.plugins.redis.register  # noqa: F401 — triggers entry-point side-effects

    registered = GlobalTypeRegistry.get().get_registered_memorys()
    assert any(r.local_name == "redis_memory" for r in registered), (
        f"'redis_memory' not found in NAT type registry. Registered: {registered}"
    )


# ---------------------------------------------------------------------------
# Schema / index lifecycle
# ---------------------------------------------------------------------------


async def test_redis_schema_creates_index(
    redis_client: aioredis.Redis,
    unique_suffix: str,
) -> None:
    """``ensure_index_exists`` creates a RediSearch index containing an embedding field."""
    key_prefix = f"schema_test_{unique_suffix}"

    # Start clean using redisvl's delete helper if an index already exists.
    scratch = AsyncSearchIndex(schema=_build_index_schema(key_prefix), redis_client=redis_client)
    try:
        await scratch.delete(drop=True)
    except Exception:
        pass

    index = await ensure_index_exists(
        client=redis_client,
        key_prefix=key_prefix,
        embedding_dim=EMBEDDING_DIM,
    )

    info = await index.info()
    # Stringify the attributes block so the check is robust across redisvl
    # protocol versions (RESP2 returns flat lists; RESP3 returns dicts).
    assert "embedding" in str(info.get("attributes", "")), (
        f"Expected 'embedding' in index attributes; got: {info.get('attributes')}"
    )


async def test_redis_schema_create_is_idempotent(
    redis_client: aioredis.Redis,
    unique_suffix: str,
) -> None:
    """Calling ``ensure_index_exists`` a second time on an existing index must not raise."""
    key_prefix = f"idem_test_{unique_suffix}"

    scratch = AsyncSearchIndex(schema=_build_index_schema(key_prefix), redis_client=redis_client)
    try:
        await scratch.delete(drop=True)
    except Exception:
        pass

    await ensure_index_exists(client=redis_client, key_prefix=key_prefix, embedding_dim=EMBEDDING_DIM)
    # Second call must be a no-op — not raise.
    index = await ensure_index_exists(client=redis_client, key_prefix=key_prefix, embedding_dim=EMBEDDING_DIM)
    assert await index.info() is not None


# ---------------------------------------------------------------------------
# RedisEditor CRUD
# ---------------------------------------------------------------------------


async def test_redis_editor_add_item_stores_correct_fields(
    redis_editor: RedisEditor,
    redis_client: aioredis.Redis,
    unique_suffix: str,
) -> None:
    """Adding a MemoryItem writes a JSON key to Redis with the expected field values."""
    item = MemoryItem(
        user_id=f"user_{unique_suffix}",
        memory="Prefers Python over Java",
        tags=["language", "preference"],
        metadata={"source": "integration_test"},
    )

    await redis_editor.add_items([item])

    pattern = f"{redis_editor._key_prefix}:memory:*"
    keys = [k async for k in redis_client.scan_iter(match=pattern)]
    assert len(keys) == 1, f"Expected 1 key; found: {keys}"

    stored = await redis_client.json().get(keys[0])
    assert stored["memory"] == "Prefers Python over Java"
    assert stored["user_id"] == f"user_{unique_suffix}"
    assert stored["tags"] == ["language", "preference"]
    assert stored["metadata"]["source"] == "integration_test"


async def test_redis_editor_add_multiple_items_stores_all(
    redis_editor: RedisEditor,
    redis_client: aioredis.Redis,
    unique_suffix: str,
) -> None:
    """``add_items`` with a batch writes one Redis key per item."""
    items = [MemoryItem(user_id=f"user_{unique_suffix}", memory=f"Fact number {i}", tags=[f"tag{i}"]) for i in range(3)]

    await redis_editor.add_items(items)

    pattern = f"{redis_editor._key_prefix}:memory:*"
    keys = [k async for k in redis_client.scan_iter(match=pattern)]
    assert len(keys) == 3, f"Expected 3 keys; found: {len(keys)}"


async def test_redis_editor_remove_items_clears_prefix(
    redis_editor: RedisEditor,
    redis_client: aioredis.Redis,
    unique_suffix: str,
) -> None:
    """``remove_items`` deletes every key under the editor's prefix."""
    items = [MemoryItem(user_id=f"user_{unique_suffix}", memory=f"Memory {i}") for i in range(4)]
    await redis_editor.add_items(items)

    pattern = f"{redis_editor._key_prefix}:memory:*"
    before = [k async for k in redis_client.scan_iter(match=pattern)]
    assert len(before) == 4

    await redis_editor.remove_items()

    after = [k async for k in redis_client.scan_iter(match=pattern)]
    assert len(after) == 0, f"Expected 0 keys after remove_items; found: {len(after)}"


async def test_redis_editor_add_item_without_memory_string(
    redis_editor: RedisEditor,
    redis_client: aioredis.Redis,
    unique_suffix: str,
) -> None:
    """Items with only a conversation (no ``memory`` string) are stored without an
    embedding vector and must not raise.
    """
    item = MemoryItem(
        user_id=f"user_{unique_suffix}",
        conversation=[
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ],
        tags=["greeting"],
    )

    await redis_editor.add_items([item])

    pattern = f"{redis_editor._key_prefix}:memory:*"
    keys = [k async for k in redis_client.scan_iter(match=pattern)]
    assert len(keys) == 1

    stored = await redis_client.json().get(keys[0])
    assert stored["memory"] == ""
    assert stored["tags"] == ["greeting"]


# ---------------------------------------------------------------------------
# RedisEditor search
# ---------------------------------------------------------------------------


async def test_redis_editor_search_returns_stored_items(
    redis_editor: RedisEditor,
    unique_suffix: str,
) -> None:
    """KNN search returns all items stored for the given user."""
    user_id = f"user_{unique_suffix}"
    await redis_editor.add_items(
        [
            MemoryItem(user_id=user_id, memory="User likes coffee", tags=["beverage"]),
            MemoryItem(user_id=user_id, memory="User lives in Berlin", tags=["location"]),
        ]
    )
    await asyncio.sleep(0.1)  # allow RediSearch to index the new documents

    results = await redis_editor.search(query="Tell me about the user", top_k=10, user_id=user_id)

    memories = {r.memory for r in results}
    assert "User likes coffee" in memories
    assert "User lives in Berlin" in memories
    for result in results:
        assert result.user_id == user_id
        assert result.similarity_score is not None


async def test_redis_editor_search_scoped_to_user_id(
    redis_editor: RedisEditor,
    unique_suffix: str,
) -> None:
    """Search results must not contain items belonging to a different user_id."""
    user_a = f"user_a_{unique_suffix}"
    user_b = f"user_b_{unique_suffix}"

    await redis_editor.add_items(
        [
            MemoryItem(user_id=user_a, memory="User A secret fact"),
            MemoryItem(user_id=user_b, memory="User B secret fact"),
        ]
    )
    await asyncio.sleep(0.1)

    results_a = await redis_editor.search(query="secret fact", top_k=10, user_id=user_a)

    assert all(r.user_id == user_a for r in results_a), "Search for user_a must not return items belonging to user_b"
    memories = {r.memory for r in results_a}
    assert "User A secret fact" in memories
    assert "User B secret fact" not in memories


async def test_redis_editor_semantic_search_ranks_by_vector_distance(
    redis_editor: RedisEditor,
    unique_suffix: str,
) -> None:
    """The item whose vector equals the query vector ranks first.

    ``QUERY_ALLERGY`` maps to ``VEC_ALLERGY`` (distance 0.0).  All other stored
    items have orthogonal vectors so their distance is sqrt(2) ≈ 1.41.
    """
    user_id = f"user_{unique_suffix}"
    await redis_editor.add_items(
        [
            MemoryItem(user_id=user_id, memory="User is allergic to peanuts", tags=["health"]),
            MemoryItem(user_id=user_id, memory="User prefers window seats on flights", tags=["travel"]),
            MemoryItem(user_id=user_id, memory="User's favourite language is Python", tags=["tech"]),
        ]
    )
    await asyncio.sleep(0.1)

    results = await redis_editor.search(query=QUERY_ALLERGY, top_k=3, user_id=user_id)

    assert len(results) > 0
    assert results[0].memory == "User is allergic to peanuts", (
        f"Allergy item should rank first (distance 0); got: {results[0].memory!r}"
    )
    assert results[0].similarity_score == pytest.approx(0.0, abs=1e-4)


async def test_redis_editor_similarity_threshold_filters_distant_results(
    redis_editor: RedisEditor,
    unique_suffix: str,
) -> None:
    """``similarity_threshold`` filters out items whose L2 distance exceeds the value.

    With orthogonal vectors, non-matching items have distance sqrt(2) ≈ 1.41.
    A threshold of 0.01 keeps only the exact match; 10.0 keeps all three.
    """
    user_id = f"user_{unique_suffix}"
    await redis_editor.add_items(
        [
            MemoryItem(user_id=user_id, memory="User drinks espresso every morning"),
            MemoryItem(user_id=user_id, memory="User is learning to play the guitar"),
            MemoryItem(user_id=user_id, memory="User's favourite language is Python"),
        ]
    )
    await asyncio.sleep(0.1)

    exact_results = await redis_editor.search(query=QUERY_COFFEE, top_k=3, user_id=user_id, similarity_threshold=0.01)
    assert len(exact_results) == 1
    assert exact_results[0].memory == "User drinks espresso every morning"

    all_results = await redis_editor.search(query=QUERY_COFFEE, top_k=3, user_id=user_id, similarity_threshold=10.0)
    assert len(all_results) == 3


# ---------------------------------------------------------------------------
# End-to-end NAT workflow tests (require OPENAI_API_KEY for the LLM)
# ---------------------------------------------------------------------------


async def test_nat_react_agent_stores_and_retrieves_memory_via_redis_memory(
    local_redis_params: tuple[str, int],
    openai_api_key: str,
    openai_model_name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    unique_suffix: str,
) -> None:
    """A NAT ``react_agent`` wired with ``get_memory`` / ``add_memory`` tools backed
    by ``_type: redis_memory`` stores a preference in Turn 1 and recalls it in Turn 2.
    """
    from nat.utils import run_workflow

    monkeypatch.setenv("OPENAI_API_KEY", openai_api_key)

    host, port = local_redis_params
    key_prefix = f"nat_wf_{unique_suffix}"
    user_id = f"user_{unique_suffix}"
    conversation_id = f"conv_{unique_suffix}"
    preference = "Always respond in bullet points."

    # Pre-create the index with the correct TagField schema so NAT's
    # ensure_index_exists (which may use a stale .pyc) reuses this correct index.
    # Use 1536 dims to match the text-embedding-3-small model used in the workflow.
    async with aioredis.Redis(host=host, port=port, db=0, decode_responses=True) as _rc:
        await _force_recreate_index(_rc, key_prefix, embedding_dim=1536)

    config_path = _write_config(
        tmp_path,
        "redis_memory_tools.yml",
        f"""
        general:
          telemetry:
            enabled: false

        embedders:
          openai_embed:
            _type: openai
            model_name: text-embedding-3-small

        llms:
          openai_llm:
            _type: openai
            model_name: {openai_model_name}
            temperature: 0.0
            max_tokens: 256

        memory:
          redis_mem:
            _type: redis_memory
            host: {host}
            port: {port}
            key_prefix: {key_prefix}
            embedder: openai_embed

        functions:
          get_memory:
            _type: get_memory
            memory: redis_mem
            description: |
              Check memory for saved user preferences and facts before answering.
              Always pass the active user_id from the conversation context.

          add_memory:
            _type: add_memory
            memory: redis_mem
            description: |
              Save user preferences and facts to long-term memory whenever the
              user shares them.

        workflow:
          _type: react_agent
          tool_names: [get_memory, add_memory]
          description: "A chat agent with Redis-backed memory tools (nat.plugins.redis)"
          llm_name: openai_llm
        """,
    )

    # Turn 1: save the preference
    first_reply = await run_workflow(
        config_file=config_path,
        prompt=(
            f"Use the add_memory tool to save this preference for user '{user_id}': "
            f"'{preference}'  Then reply with only the word: saved."
        ),
        to_type=str,
        session_kwargs={"user_id": user_id, "conversation_id": conversation_id},
    )
    assert "saved" in first_reply.lower(), f"Expected save confirmation; got: {first_reply!r}"

    # Turn 2: recall the preference
    second_reply = await run_workflow(
        config_file=config_path,
        prompt=(
            f"Use the get_memory tool to look up the preference saved for user '{user_id}'. "
            "Then repeat that preference back to me exactly."
        ),
        to_type=str,
        session_kwargs={"user_id": user_id, "conversation_id": conversation_id},
    )
    assert "bullet" in second_reply.lower(), f"Expected recalled preference to mention 'bullet'; got: {second_reply!r}"


async def test_nat_memory_tools_persist_across_independent_workflow_sessions(
    local_redis_params: tuple[str, int],
    openai_api_key: str,
    openai_model_name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    unique_suffix: str,
) -> None:
    """Memory written in Session A must be retrievable by an independent Session B —
    the core durability guarantee for production deployments.
    """
    from nat.utils import run_workflow

    monkeypatch.setenv("OPENAI_API_KEY", openai_api_key)

    host, port = local_redis_params
    key_prefix = f"persist_{unique_suffix}"
    user_id = f"user_{unique_suffix}"

    # Pre-create the index with the correct TagField schema so NAT's
    # ensure_index_exists (which may use a stale .pyc) reuses this correct index.
    # Use 1536 dims to match the text-embedding-3-small model used in the workflow.
    async with aioredis.Redis(host=host, port=port, db=0, decode_responses=True) as _rc:
        await _force_recreate_index(_rc, key_prefix, embedding_dim=1536)

    config_path = _write_config(
        tmp_path,
        "persist_test.yml",
        f"""
        general:
          telemetry:
            enabled: false

        embedders:
          openai_embed:
            _type: openai
            model_name: text-embedding-3-small

        llms:
          openai_llm:
            _type: openai
            model_name: {openai_model_name}
            temperature: 0.0
            max_tokens: 128

        memory:
          redis_mem:
            _type: redis_memory
            host: {host}
            port: {port}
            key_prefix: {key_prefix}
            embedder: openai_embed

        functions:
          get_memory:
            _type: get_memory
            memory: redis_mem

          add_memory:
            _type: add_memory
            memory: redis_mem

        workflow:
          _type: react_agent
          tool_names: [get_memory, add_memory]
          description: "Persistence test agent"
          llm_name: openai_llm
        """,
    )

    # Session A: store a unique fact
    await run_workflow(
        config_file=config_path,
        prompt=(
            f"Use add_memory to save this fact for user '{user_id}': "
            "Their favourite colour is ultraviolet.  Reply with: stored."
        ),
        to_type=str,
        session_kwargs={"user_id": user_id, "conversation_id": f"session_a_{unique_suffix}"},
    )

    await asyncio.sleep(1.0)

    # Session B: independent run — fact must still be there
    recall = await run_workflow(
        config_file=config_path,
        prompt=(
            f"Use get_memory to look up the favourite colour for user '{user_id}'. Reply with only the colour name."
        ),
        to_type=str,
        session_kwargs={"user_id": user_id, "conversation_id": f"session_b_{unique_suffix}"},
    )
    assert "ultraviolet" in recall.lower(), f"Expected 'ultraviolet' in recalled reply; got: {recall!r}"
