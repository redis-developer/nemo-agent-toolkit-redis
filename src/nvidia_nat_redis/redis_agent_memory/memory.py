# SPDX-FileCopyrightText: Copyright (c) 2026, Redis
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from nat.builder.builder import Builder
from nat.cli.register_workflow import register_memory
from nat.data_models.memory import MemoryBaseConfig
from nat.data_models.retry_mixin import RetryMixin
from nat.utils.exception_handlers.automatic_retries import patch_with_retry
from pydantic import Field

from .client_factory import create_agent_memory_client
from .editor import RedisAgentMemoryEditor


class RedisAgentMemoryBackendConfig(MemoryBaseConfig, RetryMixin, name="redis_agent_memory_backend"):
    """Configuration for Redis Agent Memory as a NAT memory backend."""

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
async def redis_agent_memory_backend_client(config: RedisAgentMemoryBackendConfig, _builder: Builder):
    client = await create_agent_memory_client(config)

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
