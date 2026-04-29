# SPDX-FileCopyrightText: Copyright (c) 2026, Redis
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from typing import Any, Literal

from nat.data_models.component_ref import FunctionRef, MemoryRef
from nat.data_models.function import FunctionBaseConfig
from pydantic import BaseModel, Field, PositiveInt, field_validator

MemoryTypeLiteral = Literal["episodic", "message", "semantic"]
MemoryStrategyLiteral = Literal["custom", "discrete", "preferences", "summary"]


def _strip_optional(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


class RedisAgentMemoryRecencyConfig(BaseModel):
    """Client-side recency re-ranking options forwarded to AMS search."""

    recency_boost: bool | None = None
    semantic_weight: float | None = None
    recency_weight: float | None = None
    freshness_weight: float | None = None
    novelty_weight: float | None = None
    half_life_last_access_days: float | None = None
    half_life_created_days: float | None = None
    server_side_recency: bool | None = None


class RedisAgentMemoryLongTermSearchConfig(BaseModel):
    """Typed subset of AMS long-term memory prompt search settings."""

    limit: PositiveInt = Field(default=5, description="Maximum long-term memories to retrieve per turn.")
    offset: int = Field(default=0, ge=0, description="Search result offset.")
    namespace: str | None = Field(default=None, description="Override namespace filter for long-term search.")
    topics: list[str] = Field(default_factory=list, description="Topic allowlist matched with AMS any-filter.")
    entities: list[str] = Field(default_factory=list, description="Entity allowlist matched with AMS any-filter.")
    memory_type: MemoryTypeLiteral | None = Field(default=None, description="Optional long-term memory type filter.")
    distance_threshold: float | None = Field(default=None, description="Optional semantic distance threshold.")
    recency: RedisAgentMemoryRecencyConfig | None = Field(
        default=None,
        description="Optional client-side recency ranking configuration.",
    )

    @field_validator("namespace", mode="before")
    @classmethod
    def _normalize_namespace(_cls, value: str | None) -> str | None:
        return _strip_optional(value)

    def to_client_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"limit": self.limit, "offset": self.offset}

        if self.namespace is not None:
            payload["namespace"] = {"eq": self.namespace}
        if self.topics:
            payload["topics"] = {"any": self.topics}
        if self.entities:
            payload["entities"] = {"any": self.entities}
        if self.memory_type is not None:
            payload["memory_type"] = {"eq": self.memory_type}
        if self.distance_threshold is not None:
            payload["distance_threshold"] = self.distance_threshold
        if self.recency is not None:
            payload["recency"] = self.recency.model_dump(exclude_none=True)

        return payload


class RedisAgentMemoryPromptConfig(BaseModel):
    """Settings for AMS memory prompt hydration."""

    optimize_query: bool = Field(
        default=False,
        description="Whether AMS should rewrite the query before long-term memory search.",
    )
    long_term_search: RedisAgentMemoryLongTermSearchConfig = Field(default_factory=RedisAgentMemoryLongTermSearchConfig)


class RedisAgentMemoryStrategyConfig(BaseModel):
    """Long-term promotion strategy stored on working memory sessions."""

    strategy: MemoryStrategyLiteral = Field(default="discrete", description="AMS extraction strategy.")
    config: dict[str, Any] = Field(default_factory=dict, description="Strategy-specific AMS configuration.")

    def to_client_model(self):
        from agent_memory_client.models import MemoryStrategyConfig

        return MemoryStrategyConfig(strategy=self.strategy, config=dict(self.config))


class RedisAgentMemoryWorkingMemoryConfig(BaseModel):
    """Working-memory options applied to native wrapper sessions."""

    namespace: str | None = Field(default=None, description="Namespace for working-memory sessions and prompt lookups.")
    model_name: str | None = Field(default=None, description="Model name forwarded to AMS token-aware operations.")
    context_window_max: PositiveInt | None = Field(
        default=None,
        description="Explicit context window forwarded to AMS token-aware operations.",
    )
    ttl_seconds: PositiveInt | None = Field(
        default=None,
        description="Optional TTL persisted when a wrapper session is created.",
    )
    long_term_memory_strategy: RedisAgentMemoryStrategyConfig = Field(default_factory=RedisAgentMemoryStrategyConfig)

    @field_validator("namespace", "model_name", mode="before")
    @classmethod
    def _normalize_optional_strings(_cls, value: str | None) -> str | None:
        return _strip_optional(value)


class RedisAgentMemoryAutoMemoryConfig(FunctionBaseConfig, name="redis_agent_memory_auto_memory"):
    """Native Redis Agent Memory wrapper for automatic prompt hydration and turn capture."""

    description: str = Field(
        default="Redis Agent Memory native wrapper that hydrates prompts from working memory and long-term memory.",
        description="Human-readable description for the wrapped workflow.",
    )
    inner_agent_name: FunctionRef = Field(
        description="Name of the NAT function that receives the hydrated ChatRequest."
    )
    memory_name: MemoryRef = Field(description="Name of the Redis Agent Memory backend in the memory config section.")
    default_user_id: str = Field(
        default="default_user",
        description="Fallback user ID when NAT runtime context does not provide one.",
    )
    default_session_id: str = Field(
        default="default_session",
        description="Fallback session ID when NAT runtime context does not provide one.",
    )
    memory_prompt: RedisAgentMemoryPromptConfig = Field(default_factory=RedisAgentMemoryPromptConfig)
    working_memory: RedisAgentMemoryWorkingMemoryConfig = Field(default_factory=RedisAgentMemoryWorkingMemoryConfig)

    @field_validator("default_user_id", "default_session_id", mode="before")
    @classmethod
    def _normalize_required_strings(_cls, value: str) -> str:
        normalized = _strip_optional(value)
        if normalized is None:
            raise ValueError("value must not be empty")
        return normalized
