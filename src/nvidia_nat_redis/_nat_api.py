# SPDX-FileCopyrightText: Copyright (c) 2026, Redis
# SPDX-License-Identifier: Apache-2.0

"""NAT public plugin API imports with compatibility for older supported NAT releases.

NAT's third-party plugin guidance asks runtime plugin code to import plugin-authoring
symbols from ``nat.plugin_api``. The current supported range also includes older NAT
releases that do not ship that facade, so this module is the only compatibility
fallback to implementation imports.
"""

from __future__ import annotations

try:
    from nat.plugin_api import (
        Builder,
        EmbedderRef,
        Function,
        FunctionBaseConfig,
        FunctionInfo,
        FunctionRef,
        KeyAlreadyExistsError,
        LLMFrameworkEnum,
        MemoryBaseConfig,
        MemoryEditor,
        MemoryItem,
        MemoryRef,
        NoSuchKeyError,
        ObjectStore,
        ObjectStoreBaseConfig,
        ObjectStoreItem,
        OptionalSecretStr,
        get_secret_value,
        register_function,
        register_memory,
        register_object_store,
    )

    USING_PLUGIN_API = True
except ImportError:  # pragma: no cover - exercised only on older or partial NAT releases
    from nat.builder.builder import Builder
    from nat.builder.framework_enum import LLMFrameworkEnum
    from nat.builder.function import Function
    from nat.builder.function_info import FunctionInfo
    from nat.cli.register_workflow import register_function, register_memory, register_object_store
    from nat.data_models.common import OptionalSecretStr, get_secret_value
    from nat.data_models.component_ref import EmbedderRef, FunctionRef, MemoryRef
    from nat.data_models.function import FunctionBaseConfig
    from nat.data_models.memory import MemoryBaseConfig
    from nat.data_models.object_store import KeyAlreadyExistsError, NoSuchKeyError, ObjectStoreBaseConfig
    from nat.memory.interfaces import MemoryEditor
    from nat.memory.models import MemoryItem
    from nat.object_store.interfaces import ObjectStore
    from nat.object_store.models import ObjectStoreItem

    USING_PLUGIN_API = False


__all__ = [
    "Builder",
    "EmbedderRef",
    "Function",
    "FunctionBaseConfig",
    "FunctionInfo",
    "FunctionRef",
    "KeyAlreadyExistsError",
    "LLMFrameworkEnum",
    "MemoryBaseConfig",
    "MemoryEditor",
    "MemoryItem",
    "MemoryRef",
    "NoSuchKeyError",
    "ObjectStore",
    "ObjectStoreBaseConfig",
    "ObjectStoreItem",
    "OptionalSecretStr",
    "USING_PLUGIN_API",
    "get_secret_value",
    "register_function",
    "register_memory",
    "register_object_store",
]
