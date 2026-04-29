# SPDX-FileCopyrightText: Copyright (c) 2026, Redis
# SPDX-License-Identifier: Apache-2.0

"""Public exports for the Redis Agent Memory automatic wrapper surface."""

from .config import RedisAgentMemoryAutoMemoryConfig
from .service import RedisAgentMemoryAutoMemoryService

__all__ = [
    "RedisAgentMemoryAutoMemoryConfig",
    "RedisAgentMemoryAutoMemoryService",
]
