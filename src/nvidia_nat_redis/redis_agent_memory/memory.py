# SPDX-FileCopyrightText: Copyright (c) 2026, Redis
# SPDX-License-Identifier: Apache-2.0

"""NAT memory backend registration for Redis Agent Memory long-term memory."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from agent_memory_client import create_memory_client

# RetryMixin and patch_with_retry are retry implementation helpers not exported
# by nat.plugin_api in the currently supported NAT versions.
from nat.data_models.retry_mixin import RetryMixin
from nat.utils.exception_handlers.automatic_retries import patch_with_retry
from pydantic import Field

from nvidia_nat_redis._nat_api import Builder, MemoryBaseConfig, MemoryEditor, register_memory

from .editor import RedisAgentMemoryEditor


class RedisAgentMemoryBackendConfig(MemoryBaseConfig, RetryMixin, name="redis_agent_memory_backend"):
    """
    Configure Redis Agent Memory as a NAT ``MemoryEditor`` backend.

    This is the config model behind ``_type: redis_agent_memory_backend`` in a
    NAT ``memory`` section. NAT uses it to create a Redis Agent Memory client and
    expose long-term memory operations through :class:`RedisAgentMemoryEditor`.
    The retry fields inherited from ``RetryMixin`` are applied to the editor
    methods when ``do_auto_retry`` is enabled.
    """

    base_url: str = Field(default="http://localhost:8000", description="Redis Agent Memory base URL.")
    default_namespace: str = Field(default="nat", description="Default namespace to use for memory operations.")
    timeout: float = Field(default=30.0, description="HTTP timeout for client operations in seconds.")
    default_model_name: str | None = Field(
        default=None, description="Default model name forwarded to the Redis Agent Memory client."
    )
    default_context_window_max: int | None = Field(
        default=None, description="Default maximum context window forwarded to the client when applicable."
    )


@register_memory(config_type=RedisAgentMemoryBackendConfig)
async def redis_agent_memory_backend_client(
    config: RedisAgentMemoryBackendConfig,
    _builder: Builder,
) -> AsyncGenerator[MemoryEditor, None]:
    """
    Yield a NAT ``MemoryEditor`` connected to Redis Agent Memory.

    The registered component owns the underlying HTTP client lifecycle for the
    duration of the NAT memory context. Consumers receive a
    :class:`RedisAgentMemoryEditor`; the client is closed when NAT exits the
    context manager.
    """
    client = await create_memory_client(
        base_url=config.base_url,
        timeout=config.timeout,
        default_namespace=config.default_namespace,
        default_model_name=config.default_model_name,
        default_context_window_max=config.default_context_window_max,
    )

    try:
        editor = RedisAgentMemoryEditor(client)
        if config.do_auto_retry:
            editor = patch_with_retry(
                editor,
                retries=config.num_retries,
                retry_codes=config.retry_on_status_codes,
                retry_on_messages=config.retry_on_errors,
            )
        yield editor
    finally:
        await client.close()
