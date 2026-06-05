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

import logging

import redis.asyncio as redis
from redisvl.index import AsyncSearchIndex
from redisvl.schema import IndexSchema, StorageType  # noqa: F401 — re-exported for callers

logger = logging.getLogger(__name__)

INDEX_NAME = "memory_idx"
DEFAULT_DIM = 384  # Default embedding dimension


def build_index_schema(key_prefix: str, embedding_dim: int = DEFAULT_DIM) -> IndexSchema:
    """Build a redisvl IndexSchema for the memory index.

    Keys are stored as ``{key_prefix}:memory:{id}`` (JSON documents).
    The schema defines four indexed fields:

    - ``user_id`` — TagField for exact-match user scoping.
    - ``tags``    — TagField over the tags array elements (``$.tags[*]``).
    - ``memory``  — TextField for full-text indexing of the memory string.
    - ``embedding`` — HNSW VectorField (FLOAT32, L2 distance).

    Non-indexed fields (``conversation``, ``metadata``) are stored in the
    JSON document but not in the RediSearch schema; they are retrieved via
    direct JSON fetch when needed.

    Args:
        key_prefix: Root key prefix (e.g. ``"nat"``). Keys will be
            ``{key_prefix}:memory:{hex_id}``.
        embedding_dim: Dimensionality of the embedding vectors.

    Returns:
        IndexSchema: redisvl schema ready for use with :class:`AsyncSearchIndex`.
    """
    logger.info("Building index schema with prefix=%s, dim=%d", key_prefix, embedding_dim)

    return IndexSchema.from_dict(
        {
            "index": {
                "name": INDEX_NAME,
                # Keys are {key_prefix}:memory:{id}; the redisvl prefix covers
                # exactly that namespace when combined with the ":" separator.
                "prefix": f"{key_prefix}:memory",
                "key_separator": ":",
                "storage_type": "json",
            },
            "fields": [
                # TagField — exact-match filter (no stemming/tokenisation).
                # redisvl auto-derives the JSON path as "$.user_id" for JSON storage.
                {"name": "user_id", "type": "tag"},
                # Array TagField — explicit path required for JSONPath wildcard.
                {"name": "tags", "type": "tag", "path": "$.tags[*]"},
                # TextField — full-text search over the memory string.
                {"name": "memory", "type": "text"},
                # HNSW VectorField — approximate nearest-neighbour search.
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


async def ensure_index_exists(
    client: redis.Redis,
    key_prefix: str,
    embedding_dim: int | None,
) -> AsyncSearchIndex:
    """Ensure the RediSearch index exists, creating it if necessary.

    Uses redisvl's :class:`AsyncSearchIndex` which calls ``FT.CREATE`` only
    when the index is absent (``overwrite=False`` is a no-op if the index
    already exists with the correct schema).

    Args:
        client: An already-connected async redis-py client.
        key_prefix: Root key prefix passed to :func:`build_index_schema`.
        embedding_dim: Embedding dimension. If ``None``, falls back to
            :data:`DEFAULT_DIM`.

    Returns:
        AsyncSearchIndex: The redisvl index, wired to the supplied *client*.
    """
    dim = embedding_dim or DEFAULT_DIM
    schema = build_index_schema(key_prefix, dim)
    # Pass redis_client directly to __init__ (preferred over the deprecated set_client).
    index = AsyncSearchIndex(schema=schema, redis_client=client)

    # create(overwrite=False) is a no-op when the index already exists.
    await index.create(overwrite=False)
    logger.info("Index '%s' is ready (prefix=%s, dim=%d)", INDEX_NAME, key_prefix, dim)
    return index
