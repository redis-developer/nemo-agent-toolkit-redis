# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
import secrets

from nat.memory.interfaces import MemoryEditor
from nat.memory.models import MemoryItem

from redisvl.index import AsyncSearchIndex
from redisvl.query import VectorQuery
from redisvl.query.filter import Tag

logger = logging.getLogger(__name__)

# Kept as a module constant so callers that import INDEX_NAME from redis_editor
# still work, even though schema.py is the canonical definition.
from .schema import INDEX_NAME  # noqa: E402


class RedisEditor(MemoryEditor):
    """
    Implements the NAT MemoryEditor interface for direct Redis memory storage.

    Uses redisvl's :class:`AsyncSearchIndex` for all index-level operations
    (create, load, vector search, clear).  Falls back to the underlying
    redis-py client (via ``index.client``) for full JSON document retrieval
    of non-indexed fields (``conversation`` and ``metadata``), which redisvl
    does not expose through a higher-level abstraction.
    """

    def __init__(self, index: AsyncSearchIndex, embedder) -> None:
        """
        Args:
            index: A fully initialised :class:`AsyncSearchIndex` pointing at
                the memory index.  The index's schema prefix must follow the
                convention ``{key_prefix}:memory`` (set by
                :func:`.schema.ensure_index_exists`).
            embedder: Any object that implements ``aembed_query(text) ->
                list[float]``.  Typically a LangChain ``Embeddings`` instance
                or a redisvl ``CustomVectorizer`` adapter.
        """
        self._index = index
        self._embedder = embedder

        # Derive the root key prefix from the schema.
        # schema.index.prefix == "{key_prefix}:memory"  with separator ":"
        schema_prefix = self._index.schema.index.prefix
        sep = self._index.schema.index.key_separator
        memory_suffix = f"{sep}memory"
        self._key_prefix = (
            schema_prefix[: -len(memory_suffix)]
            if schema_prefix.endswith(memory_suffix)
            else schema_prefix
        )

    # ------------------------------------------------------------------
    # MemoryEditor interface
    # ------------------------------------------------------------------

    async def add_items(self, items: list[MemoryItem]) -> None:
        """Insert multiple MemoryItems into Redis.

        For items that carry a ``memory`` string, an embedding vector is
        computed and stored alongside the document so that vector search works.
        Items with only a ``conversation`` (no ``memory``) are stored without
        an embedding and will not appear in KNN search results.

        Documents are loaded via :meth:`AsyncSearchIndex.load` (redisvl),
        which serialises each dict as a JSON document and notifies RediSearch
        to index it automatically.
        """
        logger.debug("Attempting to add %d items", len(items))

        for memory_item in items:
            memory_id = secrets.token_hex(4)
            memory_key = f"{self._key_prefix}:memory:{memory_id}"

            doc: dict = {
                "conversation": memory_item.conversation,
                "user_id": memory_item.user_id,
                "tags": memory_item.tags,
                "metadata": memory_item.metadata,
                "memory": memory_item.memory or "",
            }

            if memory_item.memory:
                logger.debug("Computing embedding for memory text")
                doc["embedding"] = await self._embedder.aembed_query(memory_item.memory)

            try:
                # load() stores the document as a JSON key and triggers
                # RediSearch auto-indexing for the declared schema fields.
                await self._index.load([doc], keys=[memory_key])
                logger.debug("Stored memory at %s", memory_key)
            except Exception as e:
                logger.error("Failed to store memory item: %s", e)
                raise

    async def search(self, query: str, top_k: int = 5, **kwargs) -> list[MemoryItem]:
        """Retrieve items relevant to *query* via HNSW KNN vector search.

        Args:
            query: The query string whose embedding is compared against stored
                memory embeddings.
            top_k: Maximum number of candidates to return from the KNN step.
            kwargs:
                - ``user_id`` (str): Scope results to a specific user.  When
                  absent, falls back to the literal string ``"redis"``.
                - ``similarity_threshold`` (float | None): Maximum L2 distance
                  to accept.  Results with ``vector_distance > threshold`` are
                  filtered out.  Lower is more similar (0.0 = identical).

        Returns:
            list[MemoryItem]: Matching items, ordered by ascending L2 distance.
        """
        user_id = kwargs.get("user_id", "redis")
        similarity_threshold = kwargs.get("similarity_threshold", None)

        logger.debug("search: query=%r top_k=%d user_id=%s", query, top_k, user_id)

        try:
            query_vector = await self._embedder.aembed_query(query)
        except Exception as e:
            logger.error("Failed to generate embedding: %s", e)
            raise

        # Tag filter for exact user_id scoping — no stemming or tokenisation.
        filter_expr = Tag("user_id") == user_id

        vq = VectorQuery(
            vector=query_vector,
            vector_field_name="embedding",
            # Only request indexed scalar fields here.  Non-indexed fields
            # (conversation, metadata) are fetched separately below.
            return_fields=["user_id", "memory", "tags"],
            filter_expression=filter_expr,
            num_results=top_k,
            return_score=True,  # includes "vector_distance" in each result
            dtype="float32",
        )

        try:
            hits = await self._index.query(vq)
        except Exception as e:
            logger.error("Search failed: %s", e)
            raise

        logger.debug("KNN returned %d hits (total)", len(hits))

        # Precompute the prefix+separator string for extracting document IDs.
        prefix_sep = (
            self._index.schema.index.prefix + self._index.schema.index.key_separator
        )

        memories: list[MemoryItem] = []
        for hit in hits:
            score = float(hit.get("vector_distance", 0.0))

            if similarity_threshold is not None and score > similarity_threshold:
                logger.debug("Filtered hit (score %.4f > threshold %.4f)", score, similarity_threshold)
                continue

            # Fetch the full JSON document to retrieve non-indexed fields
            # (conversation, metadata).  redisvl's AsyncSearchIndex.fetch()
            # reads the complete JSON value; the underlying transport is
            # redis-py's json().get(), kept here because redisvl's query()
            # only returns schema-declared fields.
            doc_id = hit["id"][len(prefix_sep):]
            full_doc = await self._index.fetch(doc_id)

            if full_doc is None:
                logger.warning("Document %s not found during full fetch", hit["id"])
                continue

            memories.append(self._create_memory_item(full_doc, user_id, score))

        logger.debug("Returning %d memory items after filtering", len(memories))
        return memories

    async def remove_items(self, **kwargs) -> None:
        """Remove all memory items stored under this editor's key prefix.

        Uses :meth:`AsyncSearchIndex.clear`, which deletes every document
        tracked by the RediSearch index in batches.  Non-indexed keys under
        the same prefix (if any) are left untouched.

        The ``**kwargs`` API is preserved for interface compatibility; they are
        not used by this implementation (removal is always prefix-wide).
        """
        try:
            await self._index.clear()
        except Exception as e:
            logger.error("Failed to remove items: %s", e)
            raise

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _create_memory_item(
        self,
        data: dict,
        user_id: str,
        similarity_score: float | None = None,
    ) -> MemoryItem:
        """Construct a MemoryItem from a raw JSON document dict."""
        tags = data.get("tags", [])
        if isinstance(tags, str):
            tags = [tags]
        elif not isinstance(tags, list):
            tags = []

        return MemoryItem(
            conversation=data.get("conversation", []),
            user_id=user_id,
            memory=data.get("memory", ""),
            tags=tags,
            metadata=data.get("metadata", {}),
            similarity_score=similarity_score,
        )
