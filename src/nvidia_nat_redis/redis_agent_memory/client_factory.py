# SPDX-FileCopyrightText: Copyright (c) 2026, Redis
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from typing import Protocol

from agent_memory_client import MemoryAPIClient, create_memory_client


class RedisAgentMemoryClientConfig(Protocol):
    """Runtime settings needed to construct an AMS client."""

    base_url: str
    timeout: float
    default_namespace: str
    default_model_name: str | None
    default_context_window_max: int | None


async def create_agent_memory_client(config: RedisAgentMemoryClientConfig) -> MemoryAPIClient:
    """Build a Redis Agent Memory client from the shared package config surface."""
    return await create_memory_client(
        base_url=config.base_url,
        timeout=config.timeout,
        default_namespace=config.default_namespace,
        default_model_name=config.default_model_name,
        default_context_window_max=config.default_context_window_max,
    )
