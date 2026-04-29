# SPDX-FileCopyrightText: Copyright (c) 2026, Redis
# SPDX-License-Identifier: Apache-2.0

"""NAT function registration for the Redis Agent Memory automatic wrapper."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from nat.builder.builder import Builder
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.api_server import ChatRequest, ChatRequestOrMessage, ChatResponse
from nat.utils.exception_handlers.automatic_retries import patch_with_retry

from ..client_factory import create_agent_memory_client
from ..memory import RedisAgentMemoryBackendConfig
from .config import RedisAgentMemoryAutoMemoryConfig
from .service import RedisAgentMemoryAutoMemoryService


def _validate_inner_agent_input_schema(input_schema: type) -> None:
    """Ensure the wrapped NAT function can receive the hydrated chat request."""
    try:
        if issubclass(input_schema, (ChatRequest, ChatRequestOrMessage)):
            return
    except TypeError:
        pass

    raise ValueError(
        "Redis Agent Memory wrapper requires an inner_agent_name that accepts ChatRequest or ChatRequestOrMessage."
    )


@register_function(config_type=RedisAgentMemoryAutoMemoryConfig)
async def redis_agent_memory_auto_memory(
    config: RedisAgentMemoryAutoMemoryConfig,
    builder: Builder,
) -> AsyncGenerator[FunctionInfo, None]:
    """
    Yield a NAT function that wraps another chat function with Redis memory.

    NAT calls this factory for ``_type: redis_agent_memory_auto_memory``. The
    configured ``memory_name`` must reference a
    :class:`RedisAgentMemoryBackendConfig` so both integration surfaces share
    one Redis Agent Memory client configuration. The yielded function accepts
    ``ChatRequestOrMessage`` and returns either ``ChatResponse`` or ``str`` to
    match NAT's chat workflow conversion behavior.
    """
    memory_config = builder.get_memory_client_config(config.memory_name)
    if not isinstance(memory_config, RedisAgentMemoryBackendConfig):
        raise ValueError(
            f"memory_name '{config.memory_name}' must reference an '_type: redis_agent_memory_backend' config."
        )

    inner_agent = await builder.get_function(config.inner_agent_name)
    _validate_inner_agent_input_schema(inner_agent.input_schema)

    client = await create_agent_memory_client(memory_config)
    service_client = client
    if memory_config.do_auto_retry:
        service_client = patch_with_retry(
            client,
            retries=memory_config.num_retries,
            retry_codes=memory_config.retry_on_status_codes,
            retry_on_messages=memory_config.retry_on_errors,
        )
    service = RedisAgentMemoryAutoMemoryService(client=service_client, config=config)

    async def _response_fn(value: ChatRequestOrMessage) -> ChatResponse | str:
        return await service.run(inner_agent=inner_agent, value=value)

    _response_fn.__annotations__ = {"value": ChatRequestOrMessage, "return": ChatResponse | str}

    try:
        yield FunctionInfo.from_fn(_response_fn, description=config.description)
    finally:
        await client.close()
