# SPDX-FileCopyrightText: Copyright (c) 2026, Redis
# SPDX-License-Identifier: Apache-2.0

"""
Load Redis Agent Memory NAT components for entry-point registration.

The ``nat.components`` entry point targets this module. Importing the backend
and wrapper modules runs their NAT registration decorators.
"""

from . import memory as _memory  # noqa: F401
from .auto_memory import register as _auto_memory_register  # noqa: F401
