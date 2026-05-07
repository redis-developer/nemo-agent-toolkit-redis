# SPDX-FileCopyrightText: Copyright (c) 2026, Redis
# SPDX-License-Identifier: Apache-2.0

"""NVIDIA NeMo Agent Toolkit integrations for Redis: Agent Memory Server and direct Redis plugins."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("nvidia-nat-redis")
except PackageNotFoundError:  # pragma: no cover - source-tree usage before installation
    __version__ = "0.0.0"
