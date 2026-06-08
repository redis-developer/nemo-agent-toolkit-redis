# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Unit tests for RedisEditor backed by a real local Redis instance.

These tests require Redis Stack to be reachable at ``$REDIS_URL``
(default: ``redis://localhost:6379``).  They are skipped automatically when
Redis is not available.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse
from uuid import uuid4

import pytest
import redis.asyncio as aioredis
import redis.exceptions as redis_exceptions
from nat.memory.models import MemoryItem
from redisvl.index import AsyncSearchIndex
from redisvl.schema import IndexSchema

from nat.plugins.redis.redis_editor import RedisEditor

# ---------------------------------------------------------------------------
# Hardcoded embedding vectors — same pattern as the integration tests.
# Orthogonal 6-dim unit vectors guarantee deterministic KNN ranking.
# ---------------------------------------------------------------------------

EMBEDDING_DIM = 6

VEC_A       = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0]
VEC_B       = [0.0, 1.0, 0.0, 0.0, 0.0, 0.0]
VEC_C       = [0.0, 0.0, 1.0, 0.0, 0.0, 0.0]
VEC_DEFAULT = [0.0, 0.0, 0.0, 0.0, 0.0, 1.0]

_TEXT_TO_VEC: dict[str, list[float]] = {
    "Memory A": VEC_A,
    "Memory B": VEC_B,
    "Memory C": VEC_C,
    "query_a":  VEC_A,   # query that should rank "Memory A" first
    "query_b":  VEC_B,   # query that should rank "Memory B" first
}


class _FakeEmbedder:
    """Plain-Python embedder returning hardcoded orthogonal vectors."""

    def embed_query(self, text: str) -> list[float]:
        return _TEXT_TO_VEC.get(text, VEC_DEFAULT)

    async def aembed_query(self, text: str) -> list[float]:
        return self.embed_query(text)


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------


def _build_schema(key_prefix: str, embedding_dim: int = EMBEDDING_DIM) -> IndexSchema:
    """Build a redisvl IndexSchema scoped to *key_prefix*."""
    return IndexSchema.from_dict(
        {
            "index": {
                # Use a per-test index name so parallel runs don't collide.
                "name": f"test_{key_prefix}",
                "prefix": f"{key_prefix}:memory",
                "key_separator": ":",
                "storage_type": "json",
            },
            "fields": [
                {"name": "user_id", "type": "tag"},
                {"name": "tags",    "type": "tag", "path": "$.tags[*]"},
                {"name": "memory",  "type": "text"},
                {
                    "name": "embedding",
                    "type": "vector",
                    "attrs": {
                        "algorithm":       "hnsw",
                        "datatype":        "float32",
                        "dims":            embedding_dim,
                        "distance_metric": "l2",
                        "initial_cap":     100,
                        "m":               16,
                        "ef_construction": 200,
                        "ef_runtime":      10,
                    },
                },
            ],
        }
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
async def redis_editor():
    """RedisEditor backed by a real local Redis index.

    Skips automatically if Redis is not reachable.  Each test gets its own
    key-prefix and index so tests are fully isolated.
    """
    url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    parsed = urlparse(url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 6379

    client = aioredis.Redis(
        host=host, port=port, db=0,
        decode_responses=True,
        socket_timeout=3.0,
        socket_connect_timeout=3.0,
    )
    try:
        await client.ping()
    except (redis_exceptions.ConnectionError, redis_exceptions.TimeoutError) as exc:
        await client.aclose()
        pytest.skip(f"Redis not reachable at {host}:{port} — {exc}")

    key_prefix = f"editor_test_{uuid4().hex[:10]}"
    schema = _build_schema(key_prefix)
    index = AsyncSearchIndex(schema=schema, redis_client=client)
    await index.create(overwrite=True, drop=True)

    editor = RedisEditor(index=index, embedder=_FakeEmbedder())

    yield editor

    await editor.remove_items()
    try:
        await index.delete(drop=True)
    except Exception:
        pass
    await client.aclose()


@pytest.fixture()
def sample_memory_item() -> MemoryItem:
    return MemoryItem(
        conversation=[
            {"role": "user",      "content": "Hi, I'm vegetarian and allergic to nuts."},
            {"role": "assistant", "content": "Noted!"},
        ],
        user_id="user123",
        memory="Memory A",
        metadata={"key1": "value1"},
        tags=["tag1", "tag2"],
    )


# ---------------------------------------------------------------------------
# add_items tests
# ---------------------------------------------------------------------------


async def test_add_items_success(
    redis_editor: RedisEditor,
    sample_memory_item: MemoryItem,
) -> None:
    """Adding a MemoryItem stores a retrievable key in Redis."""
    await redis_editor.add_items([sample_memory_item])

    # Verify through search: the item must be findable by user_id.
    results = await redis_editor.search(
        query="Memory A", top_k=5, user_id=sample_memory_item.user_id
    )
    assert any(r.memory == "Memory A" for r in results)

    found = next(r for r in results if r.memory == "Memory A")
    assert found.user_id == sample_memory_item.user_id
    assert found.tags == sample_memory_item.tags
    assert found.metadata == sample_memory_item.metadata
    assert found.conversation == sample_memory_item.conversation
    assert found.similarity_score is not None


async def test_add_items_empty_list(redis_editor: RedisEditor) -> None:
    """An empty batch leaves the index empty."""
    await redis_editor.add_items([])

    results = await redis_editor.search(query="anything", top_k=5, user_id="nobody")
    assert results == []


# ---------------------------------------------------------------------------
# search tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_success(redis_editor: RedisEditor) -> None:
    """Items are returned in KNN order; the closest vector ranks first."""
    user_id = "search_user"
    await redis_editor.add_items([
        MemoryItem(user_id=user_id, memory="Memory A", tags=["a"]),
        MemoryItem(user_id=user_id, memory="Memory B", tags=["b"]),
    ])

    # "query_a" maps to VEC_A, which is identical to "Memory A"'s vector.
    results = await redis_editor.search(query="query_a", top_k=2, user_id=user_id)

    assert len(results) == 2
    assert results[0].memory == "Memory A"       # nearest neighbour
    assert results[0].similarity_score == pytest.approx(0.0, abs=1e-4)  # distance 0
    assert results[1].memory == "Memory B"


@pytest.mark.asyncio
async def test_search_with_similarity_threshold_filters_results(
    redis_editor: RedisEditor,
) -> None:
    """Items whose L2 distance exceeds similarity_threshold are excluded.

    VEC_A and VEC_B are orthogonal unit vectors; their L2 distance is sqrt(2) ≈ 1.41.
    A tight threshold of 0.01 keeps only the exact match (distance ≈ 0).
    A loose threshold of 10.0 keeps both items.
    """
    user_id = "threshold_user"
    await redis_editor.add_items([
        MemoryItem(user_id=user_id, memory="Memory A"),
        MemoryItem(user_id=user_id, memory="Memory B"),
    ])

    # Tight: only the exact match survives
    exact = await redis_editor.search(
        query="query_a", top_k=2, user_id=user_id, similarity_threshold=0.01
    )
    assert len(exact) == 1
    assert exact[0].memory == "Memory A"

    # Loose: both items survive
    all_results = await redis_editor.search(
        query="query_a", top_k=2, user_id=user_id, similarity_threshold=10.0
    )
    assert len(all_results) == 2
