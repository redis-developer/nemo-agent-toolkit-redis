# SPDX-FileCopyrightText: Copyright (c) 2026, Redis
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from nat.utils import run_workflow

EXAMPLE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_FILE = EXAMPLE_DIR / "configs" / "config.yml"
DEFAULT_PROMPTS = [
    "Remember that I prefer concise answers.",
    "How should you answer me?",
]


def _load_env_file(env_file: Path) -> None:
    """Load simple KEY=VALUE pairs from a local .env file if it exists."""
    if not env_file.exists():
        return

    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the Redis Agent Memory tool-based memory example."
    )
    parser.add_argument(
        "--config-file",
        type=Path,
        default=DEFAULT_CONFIG_FILE,
        help="Path to the NAT config file.",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=EXAMPLE_DIR / ".env",
        help="Optional .env file to load before running.",
    )
    parser.add_argument(
        "--user-id",
        default="demo-user",
        help="User ID NAT should use for memory isolation.",
    )
    parser.add_argument(
        "--conversation-id",
        default="demo-session",
        help="Conversation ID NAT should use for session-scoped metadata.",
    )
    parser.add_argument(
        "--input",
        dest="inputs",
        action="append",
        default=None,
        help="Prompt to send to the agent. Repeat to run multiple turns in order.",
    )
    return parser


async def _run_inputs(config_file: Path, user_id: str, conversation_id: str, inputs: list[str]) -> None:
    for index, prompt in enumerate(inputs, start=1):
        result = await run_workflow(
            config_file=config_file,
            prompt=prompt,
            to_type=str,
            session_kwargs={"conversation_id": conversation_id, "user_id": user_id},
        )

        print(f"[{index}] User ({user_id}/{conversation_id}): {prompt}")
        print(f"[{index}] Assistant: {result}\n")


async def _main() -> int:
    args = _build_parser().parse_args()

    _load_env_file(args.env_file)

    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit(
            "OPENAI_API_KEY is required. Copy .env.example to .env or export it in your shell."
        )

    inputs = args.inputs or DEFAULT_PROMPTS
    await _run_inputs(
        config_file=args.config_file,
        user_id=args.user_id,
        conversation_id=args.conversation_id,
        inputs=inputs,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
