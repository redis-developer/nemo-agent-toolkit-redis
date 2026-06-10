# SPDX-FileCopyrightText: Copyright (c) 2026, Redis
# SPDX-License-Identifier: Apache-2.0

"""
Load Redis Agent Memory NAT components for entry-point registration.

Imported by ``nat.plugins.redis.register``, the ``nat.plugins`` entry point
target (``nat_redis``). Importing this module runs the NAT registration
decorators for the Redis Agent Memory backend and auto-memory wrapper.
"""

from . import memory as _memory  # noqa: F401
from .auto_memory import register as _auto_memory_register  # noqa: F401
