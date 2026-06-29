# SPDX-FileCopyrightText: Copyright (c) 2026, Redis
# SPDX-License-Identifier: Apache-2.0

"""NAT ``MemoryEditor`` adapter for Redis Agent Memory cloud long-term memory."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable
from datetime import datetime
from typing import TYPE_CHECKING, Any

# Runtime request context is not exported by nat.plugin_api.
from nat.builder.context import Context

from nvidia_nat_redis._nat_api import MemoryEditor, MemoryItem
from nvidia_nat_redis.redis_agent_memory._text import message_content_to_text

if TYPE_CHECKING:
    from redis_agent_memory import AgentMemory

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Text and value helpers (cloud-specific; shared patterns with open-source editor)
# ---------------------------------------------------------------------------


def _memory_item_to_text(item: MemoryItem) -> str:
    """Derive durable memory text from a NAT MemoryItem."""
    if item.memory:
        return message_content_to_text(item.memory)

    if item.conversation:
        parts: list[str] = []
        for message in item.conversation:
            if hasattr(message, "model_dump"):
                message = message.model_dump(mode="json")

            content = message.get("content", "") if isinstance(message, dict) else getattr(message, "content", message)
            text = message_content_to_text(content)
            if text:
                parts.append(text)

        return " ".join(parts)

    return ""


def _normalize_strings(value: Any) -> list[str] | None:
    if value is None:
        return None

    if isinstance(value, str):
        normalized = value.strip()
        return [normalized] if normalized else None

    if isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray, dict)):
        items = [normalized for item in value if (normalized := str(item).strip())]
        return items or None

    raise TypeError(f"Expected a string or iterable of strings, got {type(value)!r}")


def _normalize_memory_ids(single_id: Any = None, many_ids: Any = None) -> list[str]:
    ids: list[str] = []

    if single_id is not None:
        ids.append(str(single_id))

    if many_ids is not None:
        if isinstance(many_ids, str):
            ids.append(many_ids)
        else:
            ids.extend(str(value) for value in many_ids)

    return [value for value in ids if value]


def _runtime_context_value(field_name: str) -> str | None:
    value = getattr(Context.get(), field_name, None)
    if isinstance(value, str):
        value = value.strip()
    return value or None


def _metadata_value(item: MemoryItem, field_name: str, override: Any = None) -> Any:
    if override is not None:
        return override
    if isinstance(item.metadata, dict):
        return item.metadata.get(field_name)
    return None


def _serialize_metadata_value(value: Any) -> Any:
    if hasattr(value, "value"):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _cloud_record_to_metadata(record: Any) -> dict[str, Any]:
    """Build a NAT MemoryItem metadata dict from a cloud SDK MemoryRecord.

    The SDK models expose snake_case Python attributes (e.g. ``session_id``)
    even though their wire/alias form is camelCase. Read the snake_case
    attribute and surface the camelCase key in metadata to keep the cloud
    field naming used elsewhere in this adapter.
    """
    metadata: dict[str, Any] = {}

    for attribute, key in (
        ("id", "id"),
        ("session_id", "sessionId"),
        ("namespace", "namespace"),
        ("topics", "topics"),
        ("memory_type", "memoryType"),
        ("attributes", "attributes"),
        ("created_at", "createdAt"),
        ("updated_at", "updatedAt"),
    ):
        value = getattr(record, attribute, None)
        if value is not None:
            metadata[key] = _serialize_metadata_value(value)

    return metadata


# ---------------------------------------------------------------------------
# Filter builders
# ---------------------------------------------------------------------------


def _build_long_term_filter(
    *,
    user_id: str | None,
    namespace: str | None,
    session_id: str | None,
    topics: list[str] | None,
    memory_type: str | None,
) -> Any | None:
    """Construct a ``LongTermMemoryFilter`` from simple values, or return ``None``."""
    from redis_agent_memory.models import (
        LongTermMemoryFilter,
        MemoryTypeFilter,
        NamespaceFilter,
        OwnerIDFilter,
        SessionIDFilter,
        TopicsFilter,
    )

    kwargs: dict[str, Any] = {}
    if user_id:
        kwargs["ownerId"] = OwnerIDFilter(eq=user_id)
    if namespace:
        kwargs["namespace"] = NamespaceFilter(eq=namespace)
    if session_id:
        kwargs["sessionId"] = SessionIDFilter(eq=session_id)
    if topics:
        kwargs["topics"] = TopicsFilter(any=topics)
    if memory_type:
        kwargs["memoryType"] = MemoryTypeFilter(eq=memory_type)

    return LongTermMemoryFilter(**kwargs) if kwargs else None


# ---------------------------------------------------------------------------
# Editor
# ---------------------------------------------------------------------------


class CloudRedisAgentMemoryEditor(MemoryEditor):
    """
    NAT ``MemoryEditor`` implementation backed by the Redis Agent Memory cloud service.

    Uses the ``redis-agent-memory`` SDK (``AgentMemory`` client) rather than the
    open-source ``agent-memory-client``.  The cloud service authenticates via a
    Bearer token (``api_key``) and scopes records to a ``store_id``.

    Field mapping compared to the open-source adapter:
    - ``user_id``     → ``ownerId``  (camelCase cloud field)
    - ``tags``        → ``topics``
    - ``metadata``    → ``attributes``
    - Response items are in ``.items`` (not ``.memories``)
    - ``CreateMemoryRecord.id`` is caller-generated (UUID4)
    - No ``dist``/score field on ``MemoryRecord``; ``similarity_score`` is omitted
    """

    def __init__(self, client: AgentMemory):
        """Create the editor around a constructed ``AgentMemory`` client."""
        self._client = client

    async def add_items(self, items: list[MemoryItem], **kwargs: Any) -> None:
        """
        Store NAT memory items as cloud long-term memory records.

        Text comes from ``MemoryItem.memory`` first and falls back to the text
        content in ``MemoryItem.conversation``. Tags become cloud ``topics``.
        Each record receives a freshly generated UUID ``id`` required by the
        cloud SDK.

        Keyword Args:
            user_id: Override ``ownerId`` for every inserted record.
            topics: Override topics for every inserted item.
            session_id: Override the cloud session ID.
            namespace: Override the cloud namespace.
            memory_type: Optional memory type string forwarded to the cloud.
            attributes: Optional attributes dict forwarded to the cloud.
        """
        if not items:
            return

        from redis_agent_memory.models import CreateMemoryRecord

        topic_override = _normalize_strings(kwargs.get("topics"))
        user_id_override = kwargs.get("user_id")
        session_id_override = kwargs.get("session_id")
        namespace_override = kwargs.get("namespace")
        memory_type_override = kwargs.get("memory_type")
        attributes_override = kwargs.get("attributes")
        default_session_id = _runtime_context_value("conversation_id")
        default_user_id = _runtime_context_value("user_id")

        records: list[CreateMemoryRecord] = []

        for item in items:
            text = _memory_item_to_text(item)
            if not text:
                logger.warning("Skipping MemoryItem with no memory text or conversation content")
                continue

            owner_id = user_id_override or (item.user_id if item.user_id else None) or default_user_id or "default"
            session_id = _metadata_value(item, "sessionId", session_id_override) or default_session_id
            namespace = _metadata_value(item, "namespace", namespace_override)
            topics = topic_override or _normalize_strings(item.tags)
            memory_type = _metadata_value(item, "memoryType", memory_type_override)
            attributes = _metadata_value(item, "attributes", attributes_override)

            records.append(
                CreateMemoryRecord(
                    id=str(uuid.uuid4()),
                    text=text,
                    ownerId=owner_id,
                    sessionId=session_id,
                    namespace=namespace,
                    topics=topics,
                    memoryType=memory_type,
                    attributes=attributes,
                )
            )

        if records:
            await self._client.bulk_create_long_term_memories_async(memories=records)

    async def search(self, query: str, top_k: int = 5, **kwargs: Any) -> list[MemoryItem]:
        """
        Search cloud long-term memories and return NAT items.

        ``user_id`` is mandatory. Pass it via kwargs or run inside a NAT context
        that provides ``user_id``.

        Keyword Args:
            user_id: Required owner filter unless supplied by NAT context.
            limit: Search limit; overrides ``top_k`` when provided.
            session_id: Exact session filter.
            namespace: Exact namespace filter.
            topics: Topic any-filter (list or single string).
            memory_type: Exact memory type filter.
            distance_threshold: Minimum similarity score (0.0–1.0).
            page_token: Pagination token from a prior response.
        """
        from redis_agent_memory.models import SearchLongTermMemoryRequestContent

        user_id = kwargs.pop("user_id", None) or _runtime_context_value("user_id")
        if user_id is None:
            raise ValueError("search() requires user_id in kwargs for Redis Cloud Agent Memory")

        limit = int(kwargs.pop("limit", top_k))
        session_id = kwargs.pop("session_id", None)
        namespace = kwargs.pop("namespace", None)
        topics = _normalize_strings(kwargs.pop("topics", None))
        memory_type = kwargs.pop("memory_type", None)
        similarity_threshold = kwargs.pop("distance_threshold", None)
        page_token = kwargs.pop("page_token", None)

        mem_filter = _build_long_term_filter(
            user_id=user_id,
            namespace=namespace,
            session_id=session_id,
            topics=topics,
            memory_type=memory_type,
        )

        request = SearchLongTermMemoryRequestContent(
            text=query or None,
            limit=limit,
            similarityThreshold=float(similarity_threshold) if similarity_threshold is not None else None,
            filter=mem_filter,
            pageToken=page_token,
        )

        response = await self._client.search_long_term_memory_async(request=request)

        memories: list[MemoryItem] = []
        for record in getattr(response, "items", []) or []:
            text = getattr(record, "text", "") or ""
            record_topics = _normalize_strings(getattr(record, "topics", None)) or []
            result_user_id = getattr(record, "ownerId", None) or user_id

            memories.append(
                MemoryItem(
                    user_id=result_user_id,
                    memory=text,
                    conversation=[{"role": "user", "content": text}] if text else [],
                    tags=record_topics,
                    metadata=_cloud_record_to_metadata(record),
                )
            )

        return memories

    async def remove_items(self, **kwargs: Any) -> None:
        """
        Remove cloud long-term memories.

        Direct deletion by ``memory_id`` or ``memory_ids``, or filtered deletion
        using page-token pagination. Filtered deletion requires ``query`` or at
        least one filter to prevent accidental full wipe.

        Keyword Args:
            memory_id: Single record ID to delete.
            memory_ids: Iterable of record IDs to delete.
            query: Optional search text for filtered deletion.
            batch_size: Page size for filtered deletion.
            user_id: Owner filter; defaults to NAT context ``user_id``.
            session_id: Exact session filter; defaults to NAT context ``conversation_id``.
            namespace: Exact namespace filter.
            topics: Topic any-filter.
            memory_type: Exact memory type filter.
        """
        memory_ids = _normalize_memory_ids(kwargs.pop("memory_id", None), kwargs.pop("memory_ids", None))
        if memory_ids:
            await self._client.bulk_delete_long_term_memories_async(memory_ids=memory_ids)
            return

        from redis_agent_memory.models import SearchLongTermMemoryRequestContent

        query = kwargs.pop("query", None)
        batch_size = int(kwargs.pop("batch_size", 100))
        user_id = kwargs.pop("user_id", None) or _runtime_context_value("user_id")
        session_id = kwargs.pop("session_id", None) or _runtime_context_value("conversation_id")
        namespace = kwargs.pop("namespace", None)
        topics = _normalize_strings(kwargs.pop("topics", None))
        memory_type = kwargs.pop("memory_type", None)

        if not query and not any([user_id, session_id, namespace, topics, memory_type]):
            raise ValueError(
                "remove_items() requires memory_id, memory_ids, query, or at least one filter "
                "(user_id, session_id, namespace, topics, memory_type)."
            )

        mem_filter = _build_long_term_filter(
            user_id=user_id,
            namespace=namespace,
            session_id=session_id,
            topics=topics,
            memory_type=memory_type,
        )

        # Collect every matching id by paginating to the end *before* deleting.
        # Deleting inside the pagination loop perturbs the cursor (deleted rows
        # shift the offset, so later pages get skipped), so the search and the
        # delete phases are kept separate.
        matched_ids: list[str] = []
        seen_ids: set[str] = set()
        page_token: str | None = None

        while True:
            request = SearchLongTermMemoryRequestContent(
                text=query or None,
                limit=batch_size,
                filter=mem_filter,
                pageToken=page_token,
            )
            response = await self._client.search_long_term_memory_async(request=request)
            page_ids = [r.id for r in (getattr(response, "items", []) or []) if getattr(r, "id", None)]

            for memory_id in page_ids:
                if memory_id not in seen_ids:
                    seen_ids.add(memory_id)
                    matched_ids.append(memory_id)

            page_token = getattr(response, "next_page_token", None)
            if not page_token or len(page_ids) < batch_size:
                break

        for start in range(0, len(matched_ids), batch_size):
            chunk = matched_ids[start : start + batch_size]
            await self._client.bulk_delete_long_term_memories_async(memory_ids=chunk)
