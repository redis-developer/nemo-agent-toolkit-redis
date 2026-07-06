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


def test_topics_filter_is_not_silently_dropped() -> None:
    # TopicsFilter has no `any` field; the wrong field name serializes to {} and
    # drops the filter, so searches/deletes run broader than requested.
    from nvidia_nat_redis.cloud_redis_agent_memory.editor import _build_long_term_filter

    mem_filter = _build_long_term_filter(
        user_id=None, namespace=None, session_id=None, topics=["a", "b"], memory_type=None
    )
    dumped = mem_filter.topics.model_dump(exclude_none=True, by_alias=True)
    assert dumped == {"in": ["a", "b"]}
