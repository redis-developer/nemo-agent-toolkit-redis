# SPDX-FileCopyrightText: Copyright (c) 2026, Redis
# SPDX-License-Identifier: Apache-2.0

"""Text normalization helpers shared by Redis Agent Memory integration code."""

from __future__ import annotations

from typing import Any


def normalize_optional_string(value: str | None) -> str | None:
    """Strip optional strings and collapse empty strings to ``None``."""
    if value is None:
        return None

    value = value.strip()
    return value or None


def message_content_to_text(content: Any) -> str:
    """
    Convert NAT/OpenAI-style message content into plain text.

    Chat content may arrive as a string, a dict content part, a list of content
    parts, or a Pydantic model with ``model_dump``. Text parts are unwrapped;
    other structured content is stringified so callers still get a deterministic
    representation.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if hasattr(content, "model_dump"):
        content = content.model_dump(mode="json")

    if isinstance(content, list):
        parts = [message_content_to_text(item) for item in content]
        return "\n".join(part for part in parts if part).strip()

    if isinstance(content, dict) and content.get("type") == "text" and content.get("text") is not None:
        return str(content["text"]).strip()

    return str(content).strip()
