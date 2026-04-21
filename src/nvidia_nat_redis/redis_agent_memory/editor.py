# SPDX-FileCopyrightText: Copyright (c) 2026, Redis
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from agent_memory_client import MemoryAPIClient
from nat.builder.context import Context
from nat.memory.interfaces import MemoryEditor
from nat.memory.models import MemoryItem

logger = logging.getLogger(__name__)


def _memory_item_to_text(item: MemoryItem) -> str:
    """Derive durable memory text from a NAT MemoryItem."""
    if item.memory:
        return item.memory.strip()

    if item.conversation:
        parts = [message.get("content", "").strip() for message in item.conversation if isinstance(message, dict)]
        return " ".join(part for part in parts if part).strip()

    return ""


def _normalize_strings(value: Any) -> list[str] | None:
    if value is None:
        return None

    if isinstance(value, str):
        normalized = value.strip()
        return [normalized] if normalized else None

    if isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray, dict)):
        items = [str(item).strip() for item in value if str(item).strip()]
        return items or None

    raise TypeError(f"Expected a string or iterable of strings, got {type(value)!r}")


def _coerce_exact_filter(value: Any) -> Any:
    if value is None or isinstance(value, dict):
        return value
    return {"eq": value}


def _coerce_any_filter(value: Any) -> Any:
    if value is None or isinstance(value, dict):
        return value
    return {"any": _normalize_strings(value) or []}


def _primary_filter_value(value: Any, default: str) -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        if "eq" in value and value["eq"] is not None:
            return str(value["eq"])
        if "any" in value and value["any"]:
            return str(value["any"][0])
        if "all" in value and value["all"]:
            return str(value["all"][0])
    return default


def _coerce_memory_type(value: Any):
    from agent_memory_client.models import MemoryTypeEnum

    if value is None:
        return MemoryTypeEnum.SEMANTIC

    if isinstance(value, MemoryTypeEnum):
        return value

    try:
        return MemoryTypeEnum(str(value).lower())
    except ValueError as exc:  # pragma: no cover - defensive input validation
        valid_values = ", ".join(item.value for item in MemoryTypeEnum)
        raise ValueError(f"Unsupported memory_type {value!r}. Expected one of: {valid_values}") from exc


def _metadata_value(item: MemoryItem, field_name: str, override: Any = None) -> Any:
    if override is not None:
        return override
    if isinstance(item.metadata, dict):
        return item.metadata.get(field_name)
    return None


def _runtime_context_value(field_name: str) -> str | None:
    value = getattr(Context.get(), field_name, None)
    if isinstance(value, str):
        value = value.strip()
    return value or None


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


def _serialize_metadata_value(value: Any) -> Any:
    if hasattr(value, "value"):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _memory_record_to_metadata(record: Any) -> dict[str, Any]:
    metadata = {}

    raw_metadata = getattr(record, "metadata", None)
    if isinstance(raw_metadata, dict):
        metadata.update(raw_metadata)

    for attribute in (
        "id",
        "session_id",
        "namespace",
        "entities",
        "memory_type",
        "created_at",
        "updated_at",
        "last_accessed",
        "event_date",
        "extracted_from",
    ):
        value = getattr(record, attribute, None)
        if value is not None:
            metadata[attribute] = _serialize_metadata_value(value)

    return metadata


class RedisAgentMemoryEditor(MemoryEditor):
    """
    NAT MemoryEditor backed by Redis Agent Memory long-term memory APIs.

    The implementation intentionally stays close to NAT's existing memory editor
    shape while exposing the common Redis Agent Memory runtime filters.
    """

    def __init__(self, client: MemoryAPIClient):
        self._client = client

    async def add_items(self, items: list[MemoryItem], **kwargs: Any) -> None:
        """Insert long-term memory items through the Redis Agent Memory client."""
        if not items:
            return

        from agent_memory_client.models import ClientMemoryRecord

        deduplicate = bool(kwargs.get("deduplicate", True))
        records: list[ClientMemoryRecord] = []

        for item in items:
            text = _memory_item_to_text(item)
            if not text:
                logger.warning("Skipping MemoryItem with no memory text or conversation content")
                continue

            topics = _normalize_strings(kwargs.get("topics")) or _normalize_strings(item.tags)
            entities = _normalize_strings(_metadata_value(item, "entities", kwargs.get("entities")))
            session_id = _metadata_value(item, "session_id", kwargs.get("session_id")) or _runtime_context_value(
                "conversation_id"
            )
            record = ClientMemoryRecord(
                text=text,
                memory_type=_coerce_memory_type(_metadata_value(item, "memory_type", kwargs.get("memory_type"))),
                topics=topics,
                entities=entities,
                user_id=item.user_id,
                session_id=session_id,
                namespace=_metadata_value(item, "namespace", kwargs.get("namespace")),
                event_date=_metadata_value(item, "event_date", kwargs.get("event_date")),
            )
            records.append(record)

        if records:
            await self._client.create_long_term_memory(records, deduplicate=deduplicate)

    async def search(self, query: str, top_k: int = 5, **kwargs: Any) -> list[MemoryItem]:
        """Search long-term memory and translate results back into NAT MemoryItems."""
        user_id_filter = kwargs.pop("user_id", None) or _runtime_context_value("user_id")
        if user_id_filter is None:
            raise ValueError("search() requires user_id in kwargs for Redis Agent Memory")

        search_limit = int(kwargs.pop("limit", top_k))
        search_kwargs: dict[str, Any] = {
            "text": query,
            "limit": search_limit,
            "user_id": _coerce_exact_filter(user_id_filter),
        }

        if "offset" in kwargs:
            search_kwargs["offset"] = kwargs.pop("offset")

        for field_name, coercer in (
            ("session_id", _coerce_exact_filter),
            ("namespace", _coerce_exact_filter),
            ("topics", _coerce_any_filter),
            ("entities", _coerce_any_filter),
            ("memory_type", _coerce_exact_filter),
        ):
            if field_name in kwargs:
                search_kwargs[field_name] = coercer(kwargs.pop(field_name))

        for passthrough in ("created_at", "last_accessed", "distance_threshold", "recency", "optimize_query"):
            if passthrough in kwargs:
                search_kwargs[passthrough] = kwargs.pop(passthrough)

        search_kwargs.update(kwargs)
        results = await self._client.search_long_term_memory(**search_kwargs)

        default_user_id = _primary_filter_value(user_id_filter, default="default_user")
        memories: list[MemoryItem] = []

        for record in getattr(results, "memories", []) or []:
            text = getattr(record, "text", "") or ""
            topics = _normalize_strings(getattr(record, "topics", None)) or []
            similarity_score = getattr(record, "dist", None)
            result_user_id = getattr(record, "user_id", None) or default_user_id

            memories.append(
                MemoryItem(
                    user_id=result_user_id,
                    memory=text,
                    conversation=[{"role": "user", "content": text}] if text else [],
                    tags=topics,
                    metadata=_memory_record_to_metadata(record),
                    similarity_score=float(similarity_score) if similarity_score is not None else None,
                )
            )

        return memories

    async def remove_items(self, **kwargs: Any) -> None:
        """
        Remove long-term memories.

        Supported modes:
        - direct deletion by `memory_id` or `memory_ids`
        - filtered deletion by searching and deleting matching IDs
        """
        memory_ids = _normalize_memory_ids(kwargs.pop("memory_id", None), kwargs.pop("memory_ids", None))
        if memory_ids:
            await self._client.delete_long_term_memories(memory_ids)
            return

        query = kwargs.pop("query", "")
        batch_size = int(kwargs.pop("batch_size", 100))
        filter_fields = {
            "user_id": _coerce_exact_filter(kwargs.pop("user_id", None) or _runtime_context_value("user_id")),
            "session_id": _coerce_exact_filter(
                kwargs.pop("session_id", None) or _runtime_context_value("conversation_id")
            ),
            "namespace": _coerce_exact_filter(kwargs.pop("namespace", None)),
            "topics": _coerce_any_filter(kwargs.pop("topics", None)),
            "entities": _coerce_any_filter(kwargs.pop("entities", None)),
            "memory_type": _coerce_exact_filter(kwargs.pop("memory_type", None)),
        }

        if not query and all(value is None for value in filter_fields.values()):
            raise ValueError(
                "remove_items() requires memory_id, memory_ids, query, or at least one filter "
                "(user_id, session_id, namespace, topics, entities, memory_type)."
            )

        while True:
            search_kwargs = {"text": query, "limit": batch_size, "offset": 0}
            search_kwargs.update({key: value for key, value in filter_fields.items() if value is not None})
            results = await self._client.search_long_term_memory(**search_kwargs)
            matched_ids = [
                record.id
                for record in getattr(results, "memories", []) or []
                if getattr(record, "id", None)
            ]

            if not matched_ids:
                return

            await self._client.delete_long_term_memories(matched_ids)

            if len(matched_ids) < batch_size:
                return
