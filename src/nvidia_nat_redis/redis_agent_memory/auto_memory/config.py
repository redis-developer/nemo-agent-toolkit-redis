# SPDX-FileCopyrightText: Copyright (c) 2026, Redis
# SPDX-License-Identifier: Apache-2.0

"""Pydantic config models for the Redis Agent Memory NAT wrapper."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field, PositiveInt, field_validator

from nvidia_nat_redis._nat_api import FunctionBaseConfig, FunctionRef, MemoryRef

from .._text import normalize_optional_string

if TYPE_CHECKING:
    from agent_memory_client.models import MemoryStrategyConfig

MemoryTypeLiteral = Literal["episodic", "message", "semantic"]
MemoryStrategyLiteral = Literal["custom", "discrete", "preferences", "summary"]


class RedisAgentMemoryRecencyConfig(BaseModel):
    """
    Recency ranking options forwarded to Redis Agent Memory search.

    Set this under ``memory_prompt.long_term_search.recency`` to tune how Redis
    Agent Memory balances semantic similarity with freshness and novelty when
    hydrating prompts.
    """

    recency_boost: bool | None = None
    semantic_weight: float | None = None
    recency_weight: float | None = None
    freshness_weight: float | None = None
    novelty_weight: float | None = None
    half_life_last_access_days: float | None = None
    half_life_created_days: float | None = None
    server_side_recency: bool | None = None


class RedisAgentMemoryLongTermSearchConfig(BaseModel):
    """
    Long-term memory search settings used during prompt hydration.

    The wrapper converts plain YAML values into the filter payload expected by
    Redis Agent Memory ``memory_prompt``. Namespace and memory type are exact
    filters; topics and entities are ``any`` filters.
    """

    limit: PositiveInt = Field(default=5, description="Maximum long-term memories to retrieve per turn.")
    offset: int = Field(default=0, ge=0, description="Search result offset.")
    namespace: str | None = Field(default=None, description="Override namespace filter for long-term search.")
    topics: list[str] = Field(
        default_factory=list,
        description="Topic allowlist matched with a Redis Agent Memory any-filter.",
    )
    entities: list[str] = Field(
        default_factory=list,
        description="Entity allowlist matched with a Redis Agent Memory any-filter.",
    )
    memory_type: MemoryTypeLiteral | None = Field(default=None, description="Optional long-term memory type filter.")
    distance_threshold: float | None = Field(default=None, description="Optional semantic distance threshold.")
    recency: RedisAgentMemoryRecencyConfig | None = Field(
        default=None,
        description="Optional client-side recency ranking configuration.",
    )

    @field_validator("namespace", mode="before")
    @classmethod
    def _normalize_namespace(_cls, value: str | None) -> str | None:
        return normalize_optional_string(value)

    def to_client_payload(self) -> dict[str, Any]:
        """
        Serialize configured search options into a Redis Agent Memory payload.

        Empty optional filters are omitted so the server applies only the
        constraints explicitly configured for this wrapper.
        """
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
    """
    Settings for Redis Agent Memory ``memory_prompt`` hydration.

    These options control the prompt that is built immediately before the inner
    NAT chat function is invoked.
    """

    optimize_query: bool = Field(
        default=False,
        description="Whether Redis Agent Memory should rewrite the query before long-term memory search.",
    )
    long_term_search: RedisAgentMemoryLongTermSearchConfig = Field(default_factory=RedisAgentMemoryLongTermSearchConfig)


class RedisAgentMemoryStrategyConfig(BaseModel):
    """
    Long-term promotion strategy stored on Redis Agent Memory sessions.

    Redis Agent Memory uses this strategy to decide how working-memory turns are
    promoted into long-term memory.
    """

    strategy: MemoryStrategyLiteral = Field(default="discrete", description="Redis Agent Memory extraction strategy.")
    config: dict[str, Any] = Field(
        default_factory=dict,
        description="Strategy-specific Redis Agent Memory configuration.",
    )

    def to_client_model(self) -> MemoryStrategyConfig:
        """Convert the Pydantic config into the generated client model."""
        from agent_memory_client.models import MemoryStrategyConfig

        return MemoryStrategyConfig(strategy=self.strategy, config=dict(self.config))


class RedisAgentMemoryWorkingMemoryConfig(BaseModel):
    """
    Working-memory options applied to wrapper-managed sessions.

    ``namespace``, ``model_name``, and ``context_window_max`` are passed to Redis
    Agent Memory whenever the wrapper creates, loads, hydrates, or appends to a
    working-memory session.
    """

    namespace: str | None = Field(default=None, description="Namespace for working-memory sessions and prompt lookups.")
    model_name: str | None = Field(
        default=None,
        description="Model name forwarded to Redis Agent Memory token-aware operations.",
    )
    context_window_max: PositiveInt | None = Field(
        default=None,
        description="Explicit context window forwarded to Redis Agent Memory token-aware operations.",
    )
    ttl_seconds: PositiveInt | None = Field(
        default=None,
        description="Optional TTL persisted when a wrapper session is created.",
    )
    long_term_memory_strategy: RedisAgentMemoryStrategyConfig = Field(default_factory=RedisAgentMemoryStrategyConfig)

    @field_validator("namespace", "model_name", mode="before")
    @classmethod
    def _normalize_optional_strings(_cls, value: str | None) -> str | None:
        return normalize_optional_string(value)


class RedisAgentMemoryAutoMemoryConfig(FunctionBaseConfig, name="redis_agent_memory_auto_memory"):
    """
    NAT function config for automatic Redis Agent Memory orchestration.

    This is the config model behind ``_type: redis_agent_memory_auto_memory``.
    The registered function wraps an inner chat function, resolves NAT runtime
    ``user_id`` and ``conversation_id`` into Redis Agent Memory identity, calls
    ``memory_prompt`` to hydrate the prompt, invokes the inner function, and
    appends the completed user/assistant turn back to working memory.
    """

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
        normalized = normalize_optional_string(value)
        if normalized is None:
            raise ValueError("value must not be empty")
        return normalized
