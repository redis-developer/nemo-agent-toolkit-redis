# SPDX-FileCopyrightText: Copyright (c) 2026, Redis
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import ast
import builtins
import importlib
from pathlib import Path

PROHIBITED_DIRECT_IMPORTS = {
    "nat.builder.builder",
    "nat.builder.framework_enum",
    "nat.builder.function",
    "nat.builder.function_info",
    "nat.cli.register_workflow",
    "nat.data_models.common",
    "nat.data_models.component_ref",
    "nat.data_models.function",
    "nat.data_models.memory",
    "nat.data_models.object_store",
    "nat.memory.interfaces",
    "nat.memory.models",
    "nat.object_store.interfaces",
    "nat.object_store.models",
}


def test_runtime_plugin_code_uses_nat_api_facade_for_public_symbols() -> None:
    """Public plugin-authoring imports should flow through nvidia_nat_redis._nat_api."""
    root = Path(__file__).resolve().parents[1]
    source_files = sorted((root / "src").rglob("*.py"))
    violations: list[str] = []

    for source_file in source_files:
        if source_file.name == "_nat_api.py":
            continue

        tree = ast.parse(source_file.read_text(encoding="utf-8"), filename=str(source_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in PROHIBITED_DIRECT_IMPORTS:
                violations.append(f"{source_file.relative_to(root)}:{node.lineno} imports {node.module}")

    assert not violations, "\n".join(violations)


def test_nat_api_facade_exposes_plugin_authoring_symbols() -> None:
    from nvidia_nat_redis import _nat_api

    for name in (
        "Builder",
        "FunctionBaseConfig",
        "FunctionInfo",
        "MemoryBaseConfig",
        "MemoryEditor",
        "ObjectStoreBaseConfig",
        "register_function",
        "register_memory",
        "register_object_store",
    ):
        assert hasattr(_nat_api, name)


def test_nat_api_facade_falls_back_when_plugin_api_is_partial(monkeypatch) -> None:
    import nvidia_nat_redis._nat_api as nat_api

    real_import = builtins.__import__

    def import_with_partial_plugin_api(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "nat.plugin_api":
            raise ImportError("cannot import name 'MemoryRef' from 'nat.plugin_api'")
        return real_import(name, globals, locals, fromlist, level)

    with monkeypatch.context() as context:
        context.setattr(builtins, "__import__", import_with_partial_plugin_api)
        reloaded_api = importlib.reload(nat_api)

    try:
        assert reloaded_api.USING_PLUGIN_API is False
        assert hasattr(reloaded_api, "Builder")
        assert hasattr(reloaded_api, "register_memory")
    finally:
        importlib.reload(nat_api)
