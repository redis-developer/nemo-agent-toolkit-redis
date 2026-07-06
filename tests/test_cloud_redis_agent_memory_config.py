# SPDX-FileCopyrightText: Copyright (c) 2026, Redis
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from nvidia_nat_redis.cloud_redis_agent_memory.memory import (
    CloudRedisAgentMemoryBackendConfig,
    cloud_redis_agent_memory_backend_client,
)


async def test_timeout_ms_threaded_into_client() -> None:
    config = CloudRedisAgentMemoryBackendConfig(
        base_url="https://example.invalid",
        api_key="k",
        store_id="s",
        timeout_ms=1234,
    )

    async with cloud_redis_agent_memory_backend_client(config, _builder=None) as editor:
        assert editor._client.sdk_configuration.timeout_ms == 1234
