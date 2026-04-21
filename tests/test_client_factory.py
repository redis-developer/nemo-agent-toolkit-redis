# SPDX-FileCopyrightText: Copyright (c) 2026, Redis
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from nvidia_nat_redis.redis_agent_memory.client_factory import create_agent_memory_client


async def test_create_agent_memory_client_forwards_memory_config_defaults() -> None:
    config = SimpleNamespace(
        base_url="http://localhost:8000",
        timeout=12.5,
        default_namespace="nat",
        default_model_name="gpt-4o-mini",
        default_context_window_max=4096,
    )

    with patch(
        "nvidia_nat_redis.redis_agent_memory.client_factory.create_memory_client",
        new=AsyncMock(return_value="client"),
    ) as create_mock:
        client = await create_agent_memory_client(config)

    assert client == "client"
    create_mock.assert_awaited_once_with(
        base_url="http://localhost:8000",
        timeout=12.5,
        default_namespace="nat",
        default_model_name="gpt-4o-mini",
        default_context_window_max=4096,
    )
