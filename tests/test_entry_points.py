# SPDX-FileCopyrightText: Copyright (c) 2026, Redis
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from importlib.metadata import entry_points


def test_nat_redis_entry_point_loads_registered_components() -> None:
    """The package must expose Redis components through NAT's third-party plugin group."""
    from nat.cli.type_registry import GlobalTypeRegistry

    plugins = entry_points(group="nat.plugins")
    redis_entry_points = [
        plugin for plugin in plugins if plugin.name == "nat_redis" and plugin.value == "nat.plugins.redis.register"
    ]

    assert redis_entry_points, "Expected nat_redis to target nat.plugins.redis.register in the nat.plugins group"

    redis_entry_points[0].load()

    registry = GlobalTypeRegistry.get()
    memory_types = {registered.local_name for registered in registry.get_registered_memorys()}
    object_store_types = {registered.local_name for registered in registry.get_registered_object_stores()}

    assert "redis_memory" in memory_types
    assert "redis_agent_memory_backend" in memory_types
    assert "cloud_redis_agent_memory" in memory_types
    assert "redis" in object_store_types
