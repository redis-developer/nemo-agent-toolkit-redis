# SPDX-FileCopyrightText: Copyright (c) 2026, Redis
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path


def test_readme_covers_nat_third_party_plugin_requirements() -> None:
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")

    required_snippets = [
        "uv add nemo-agent-toolkit-redis",
        "pip install nemo-agent-toolkit-redis",
        "uv run nat info components",
        "NeMo Agent Toolkit `>=1.6.0,<2.0.0`",
        "https://github.com/redis-developer/nemo-agent-toolkit-redis/issues",
        "https://github.com/NVIDIA/NeMo-Agent-Toolkit/issues",
        "Apache License 2.0",
    ]

    missing = [snippet for snippet in required_snippets if snippet not in readme]

    assert not missing, f"README.md is missing required third-party plugin guidance: {missing}"
