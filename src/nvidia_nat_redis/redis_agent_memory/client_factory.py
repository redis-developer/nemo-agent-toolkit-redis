# SPDX-FileCopyrightText: Copyright (c) 2026, Redis
# SPDX-License-Identifier: Apache-2.0

"""Shared Redis Agent Memory client construction for NAT integration surfaces."""

from __future__ import annotations

from typing import Protocol

from agent_memory_client import MemoryAPIClient, create_memory_client


class RedisAgentMemoryClientConfig(Protocol):
    """
    Minimal config contract required to construct a Redis Agent Memory client.

    Both NAT surfaces use the same client settings so memory tools and the
    automatic wrapper connect to Redis Agent Memory consistently.
    """

    base_url: str
    timeout: float
    default_namespace: str
    default_model_name: str | None
    default_context_window_max: int | None


async def create_agent_memory_client(config: RedisAgentMemoryClientConfig) -> MemoryAPIClient:
    """
    Build a Redis Agent Memory API client from NAT component config.

    The helper centralizes forwarding of shared defaults such as namespace,
    model name, and context window so the backend and wrapper do not drift.
    The caller owns the returned client's lifecycle and must close it.
    """
    return await create_memory_client(
        base_url=config.base_url,
        timeout=config.timeout,
        default_namespace=config.default_namespace,
        default_model_name=config.default_model_name,
        default_context_window_max=config.default_context_window_max,
    )
