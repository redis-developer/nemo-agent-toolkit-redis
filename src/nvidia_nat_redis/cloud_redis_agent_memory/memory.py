# SPDX-FileCopyrightText: Copyright (c) 2026, Redis
# SPDX-License-Identifier: Apache-2.0

"""NAT memory backend registration for Redis Agent Memory cloud long-term memory."""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator

# RetryMixin and patch_with_retry are retry implementation helpers not exported
# by nat.plugin_api in the currently supported NAT versions.
from nat.data_models.retry_mixin import RetryMixin
from nat.utils.exception_handlers.automatic_retries import patch_with_retry
from pydantic import Field, SecretStr

from nvidia_nat_redis._nat_api import Builder, MemoryBaseConfig, MemoryEditor, register_memory

from .editor import CloudRedisAgentMemoryEditor


class CloudRedisAgentMemoryBackendConfig(MemoryBaseConfig, RetryMixin, name="cloud_redis_agent_memory"):
    """
    Configure the Redis Agent Memory cloud service as a NAT ``MemoryEditor`` backend.

    This is the config model behind ``_type: cloud_redis_agent_memory`` in a
    NAT ``memory`` section. Authentication uses a Bearer ``api_key`` and the
    service scopes records to ``store_id``.

    Credentials can be provided via config fields or the environment variables
    ``AGENT_MEMORY_API_KEY`` and ``AGENT_MEMORY_STORE_ID``.

    Requires the ``redis-agent-memory`` package (``pip install redis-agent-memory``).
    """

    base_url: str = Field(description="Redis Agent Memory cloud service URL (e.g. https://api.redis.io/memory/v1).")
    api_key: SecretStr | None = Field(
        default=None,
        description="Bearer token for cloud authentication. Falls back to AGENT_MEMORY_API_KEY env var.",
    )
    store_id: str | None = Field(
        default=None,
        description="Cloud store identifier. Falls back to AGENT_MEMORY_STORE_ID env var.",
    )
    timeout_ms: int = Field(default=30_000, description="HTTP timeout for client operations in milliseconds.")


@register_memory(config_type=CloudRedisAgentMemoryBackendConfig)
async def cloud_redis_agent_memory_backend_client(
    config: CloudRedisAgentMemoryBackendConfig,
    _builder: Builder,
) -> AsyncGenerator[MemoryEditor, None]:
    """
    Yield a NAT ``MemoryEditor`` connected to the Redis Agent Memory cloud service.

    The registered component owns the underlying HTTP client lifecycle for the
    duration of the NAT memory context. Consumers receive a
    :class:`CloudRedisAgentMemoryEditor`; the client is closed when NAT exits
    the context manager.
    """
    try:
        from redis_agent_memory import AgentMemory
    except ImportError as exc:
        raise ImportError(
            "The 'redis-agent-memory' package is required for the cloud_redis_agent_memory backend. "
            "Install it with: pip install redis-agent-memory"
        ) from exc

    api_key = (
        config.api_key.get_secret_value() if config.api_key is not None else os.environ.get("AGENT_MEMORY_API_KEY")
    )
    store_id = config.store_id or os.environ.get("AGENT_MEMORY_STORE_ID")

    client = AgentMemory(
        config.base_url,
        api_key=api_key,
        store_id=store_id,
        timeout_ms=config.timeout_ms,
    )

    try:
        editor: MemoryEditor = CloudRedisAgentMemoryEditor(client)
        if config.do_auto_retry:
            editor = patch_with_retry(
                editor,
                retries=config.num_retries,
                retry_codes=config.retry_on_status_codes,
                retry_on_messages=config.retry_on_errors,
            )
        yield editor
    finally:
        # AgentMemory (Speakeasy BaseSDK) exposes lifecycle only via __aexit__,
        # not aclose()/close(); without this the httpx client leaks until GC.
        await client.__aexit__(None, None, None)
