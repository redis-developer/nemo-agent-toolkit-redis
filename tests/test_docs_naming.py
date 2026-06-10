# SPDX-FileCopyrightText: Copyright (c) 2026, Redis
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path


def test_readme_uses_current_repository_and_distribution_name() -> None:
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")

    assert "# nemo-agent-toolkit-redis" in readme
    assert "git clone https://github.com/redis-developer/nemo-agent-toolkit-redis.git" in readme
    assert "cd nemo-agent-toolkit-redis" in readme

    stale_active_names = [
        "# Nvidia-NAT-Redis",
        "git clone https://github.com/redis-developer/nvidia-nat-redis.git",
        "cd nvidia-nat-redis",
    ]

    missing_cleanup = [name for name in stale_active_names if name in readme]

    assert not missing_cleanup, f"README.md still contains stale active naming: {missing_cleanup}"
