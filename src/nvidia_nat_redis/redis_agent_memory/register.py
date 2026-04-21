# SPDX-FileCopyrightText: Copyright (c) 2026, Redis
# SPDX-License-Identifier: Apache-2.0

"""Import plugin modules so NAT registration decorators execute."""

from . import memory as _memory  # noqa: F401
from .auto_memory import register as _auto_memory_register  # noqa: F401
