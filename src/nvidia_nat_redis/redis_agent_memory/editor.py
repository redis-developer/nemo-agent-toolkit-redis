# SPDX-FileCopyrightText: Copyright (c) 2026, Redis
# SPDX-License-Identifier: Apache-2.0

"""NAT ``MemoryEditor`` adapter for Redis Agent Memory long-term memory."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime
from typing import TYPE_CHECKING, Any

from agent_memory_client import MemoryAPIClient
from nat.builder.context import Context
from nat.memory.interfaces import MemoryEditor
from nat.memory.models import MemoryItem

from ._text import message_content_to_text

if TYPE_CHECKING:
    from agent_memory_client.models import MemoryTypeEnum

logger = logging.getLogger(__name__)


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


def _coerce_exact_filter(value: Any) -> Any:
    if value is None or isinstance(value, dict):
        return value
    return {"eq": value}


def _coerce_any_filter(value: Any) -> Any:
    if value is None or isinstance(value, dict):
        return value
    return {"any": _normalize_strings(value) or []}


LONG_TERM_FILTER_COERCERS: dict[str, Callable[[Any], Any]] = {
    "session_id": _coerce_exact_filter,
    "namespace": _coerce_exact_filter,
    "topics": _coerce_any_filter,
    "entities": _coerce_any_filter,
    "memory_type": _coerce_exact_filter,
}
REMOVE_FILTER_COERCERS: dict[str, Callable[[Any], Any]] = {
    "user_id": _coerce_exact_filter,
    **LONG_TERM_FILTER_COERCERS,
}
SEARCH_PASSTHROUGH_FIELDS = ("created_at", "last_accessed", "distance_threshold", "recency", "optimize_query")


def _pop_coerced_filters(
    values: dict[str, Any],
    coercers: Mapping[str, Callable[[Any], Any]],
) -> dict[str, Any]:
    filters: dict[str, Any] = {}
    for field_name, coercer in coercers.items():
        if field_name in values:
            filters[field_name] = coercer(values.pop(field_name))
    return filters


def _coerce_filter_values(
    values: Mapping[str, Any],
    coercers: Mapping[str, Callable[[Any], Any]],
) -> dict[str, Any]:
    return {field_name: coercers[field_name](value) for field_name, value in values.items()}


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


def _coerce_memory_type(value: Any) -> MemoryTypeEnum:
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
    NAT ``MemoryEditor`` implementation backed by Redis Agent Memory.

    NAT tools call this class through the standard ``add_items``, ``search``,
    and ``remove_items`` memory contract. The editor translates NAT
    :class:`MemoryItem` objects into Redis Agent Memory long-term memory records
    and maps Redis Agent Memory search results back into ``MemoryItem`` objects.

    The integration preserves NAT runtime identity where possible: ``user_id``
    is required for searches and may come from the current NAT context, while
    ``conversation_id`` is used as the default Redis Agent Memory ``session_id``
    when items are added or removed without an explicit session filter.
    """

    def __init__(self, client: MemoryAPIClient):
        """Create an editor around an already constructed Redis Agent Memory client."""
        self._client = client

    async def add_items(self, items: list[MemoryItem], **kwargs: Any) -> None:
        """
        Store NAT memory items as Redis Agent Memory long-term memories.

        Text comes from ``MemoryItem.memory`` first and falls back to the text
        content in ``MemoryItem.conversation``. Tags become Redis Agent Memory
        topics. Per-item metadata or call-level kwargs can provide
        ``session_id``, ``namespace``, ``entities``, ``memory_type``, and
        ``event_date``. When ``session_id`` is absent, the current NAT
        ``conversation_id`` is used if available.

        Keyword Args:
            deduplicate: Whether Redis Agent Memory should deduplicate the
                created records. Defaults to ``True``.
            topics: Override topics for every inserted item.
            entities: Override entities for every inserted item.
            session_id: Override the Redis Agent Memory session ID.
            namespace: Override the Redis Agent Memory namespace.
            memory_type: Redis Agent Memory type, such as ``semantic``.
            event_date: Optional event timestamp forwarded to Redis Agent Memory.
        """
        if not items:
            return

        from agent_memory_client.models import ClientMemoryRecord

        deduplicate = bool(kwargs.get("deduplicate", True))
        topic_override = _normalize_strings(kwargs.get("topics"))
        entity_override = kwargs.get("entities")
        session_id_override = kwargs.get("session_id")
        namespace_override = kwargs.get("namespace")
        memory_type_override = kwargs.get("memory_type")
        event_date_override = kwargs.get("event_date")
        default_session_id = _runtime_context_value("conversation_id")
        records: list[ClientMemoryRecord] = []

        for item in items:
            text = _memory_item_to_text(item)
            if not text:
                logger.warning("Skipping MemoryItem with no memory text or conversation content")
                continue

            topics = topic_override or _normalize_strings(item.tags)
            entities = _normalize_strings(_metadata_value(item, "entities", entity_override))
            session_id = _metadata_value(item, "session_id", session_id_override) or default_session_id
            record = ClientMemoryRecord(
                text=text,
                memory_type=_coerce_memory_type(_metadata_value(item, "memory_type", memory_type_override)),
                topics=topics,
                entities=entities,
                user_id=item.user_id,
                session_id=session_id,
                namespace=_metadata_value(item, "namespace", namespace_override),
                event_date=_metadata_value(item, "event_date", event_date_override),
            )
            records.append(record)

        if records:
            await self._client.create_long_term_memory(records, deduplicate=deduplicate)

    async def search(self, query: str, top_k: int = 5, **kwargs: Any) -> list[MemoryItem]:
        """
        Search Redis Agent Memory long-term memories and return NAT items.

        ``user_id`` is mandatory for Redis Agent Memory search. Pass it in
        kwargs or run inside a NAT context that provides ``user_id``. Filter
        kwargs are converted to Redis Agent Memory filter objects when callers
        pass plain values or lists; pre-built filter dictionaries are forwarded
        unchanged.

        Keyword Args:
            user_id: Required user filter unless supplied by NAT context.
            limit: Search limit. Overrides ``top_k`` when provided.
            offset: Search result offset.
            session_id: Exact session filter.
            namespace: Exact namespace filter.
            topics: Topic any-filter.
            entities: Entity any-filter.
            memory_type: Exact Redis Agent Memory type filter.
            created_at: Redis Agent Memory created-at filter.
            last_accessed: Redis Agent Memory last-accessed filter.
            distance_threshold: Optional semantic distance threshold.
            recency: Optional Redis Agent Memory recency ranking config.
            optimize_query: Whether Redis Agent Memory should rewrite the query.
        """
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

        search_kwargs.update(_pop_coerced_filters(kwargs, LONG_TERM_FILTER_COERCERS))

        for passthrough in SEARCH_PASSTHROUGH_FIELDS:
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
        Remove Redis Agent Memory long-term memories.

        Supported modes are direct deletion by ``memory_id`` or ``memory_ids``,
        and filtered deletion by repeatedly searching for matching memories and
        deleting the returned IDs. Filtered deletion requires ``query`` or at
        least one filter so an empty call cannot delete all memory accidentally.

        Keyword Args:
            memory_id: Single Redis Agent Memory record ID to delete.
            memory_ids: Iterable of Redis Agent Memory record IDs to delete.
            query: Optional search text used for filtered deletion.
            batch_size: Search/delete page size for filtered deletion.
            user_id: Exact user filter; defaults to NAT context ``user_id``.
            session_id: Exact session filter; defaults to NAT context
                ``conversation_id``.
            namespace: Exact namespace filter.
            topics: Topic any-filter.
            entities: Entity any-filter.
            memory_type: Exact Redis Agent Memory type filter.
        """
        memory_ids = _normalize_memory_ids(kwargs.pop("memory_id", None), kwargs.pop("memory_ids", None))
        if memory_ids:
            await self._client.delete_long_term_memories(memory_ids)
            return

        query = kwargs.pop("query", "")
        batch_size = int(kwargs.pop("batch_size", 100))
        filter_fields = _coerce_filter_values(
            {
                "user_id": kwargs.pop("user_id", None) or _runtime_context_value("user_id"),
                "session_id": kwargs.pop("session_id", None) or _runtime_context_value("conversation_id"),
                "namespace": kwargs.pop("namespace", None),
                "topics": kwargs.pop("topics", None),
                "entities": kwargs.pop("entities", None),
                "memory_type": kwargs.pop("memory_type", None),
            },
            REMOVE_FILTER_COERCERS,
        )

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
