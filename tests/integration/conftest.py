# SPDX-FileCopyrightText: Copyright (c) 2026, Redis
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import pytest


def _docker_ready() -> tuple[bool, str]:
    if shutil.which("docker") is None:
        return False, "Docker CLI is not installed."

    result = subprocess.run(
        ["docker", "info"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return False, "Docker daemon is not available."

    return True, ""


def _wait_for_http(url: str, timeout_seconds: float = 90.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:  # noqa: S310
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            time.sleep(1.0)

    raise RuntimeError(f"Timed out waiting for service readiness at {url}")


async def _prime_long_term_search_index(ams_url: str) -> None:
    from agent_memory_client import create_memory_client

    from nvidia_nat_redis.redis_agent_memory.memory import RedisAgentMemoryBackendConfig

    config = RedisAgentMemoryBackendConfig(
        base_url=ams_url,
        default_namespace="__bootstrap__",
        timeout=30.0,
    )
    client = await create_memory_client(
        base_url=config.base_url,
        timeout=config.timeout,
        default_namespace=config.default_namespace,
        default_model_name=config.default_model_name,
        default_context_window_max=config.default_context_window_max,
    )
    try:
        await client.search_long_term_memory(
            text="bootstrap long term search index",
            user_id={"eq": "__bootstrap__"},
            namespace={"eq": "__bootstrap__"},
            limit=1,
            optimize_query=False,
        )
    finally:
        await client.close()


@pytest.fixture(scope="session")
def local_redis_url() -> str:
    return os.environ.get("REDIS_URL", "redis://localhost:6379")


@pytest.fixture(scope="session")
def local_redis_params(local_redis_url: str) -> tuple[str, int]:
    from urllib.parse import urlparse

    parsed = urlparse(local_redis_url)
    return parsed.hostname or "127.0.0.1", parsed.port or 6379


@pytest.fixture(scope="session")
def local_ams_url() -> str:
    return os.environ.get("AMS_URL", "http://localhost:8000")


@pytest.fixture(scope="session")
def local_ams_stack(local_ams_url: str, local_redis_url: str) -> dict[str, str]:
    """Session-scoped fixture connecting to a locally running AMS. Skips if not reachable."""
    try:
        with urllib.request.urlopen(f"{local_ams_url}/openapi.json", timeout=5) as resp:  # noqa: S310
            if resp.status != 200:
                pytest.skip(f"AMS not reachable at {local_ams_url}")
    except (OSError, urllib.error.URLError) as exc:
        pytest.skip(f"AMS not reachable at {local_ams_url}: {exc}")
    return {"ams_url": local_ams_url, "redis_url": local_redis_url}


@pytest.fixture(scope="session")
def local_ams_stack_with_api(local_ams_stack: dict[str, str], openai_api_key: str) -> dict[str, str]:
    """Session-scoped AMS fixture that also requires OPENAI_API_KEY and primes the LTM index."""
    try:
        asyncio.run(_prime_long_term_search_index(local_ams_stack["ams_url"]))
    except Exception:
        pass  # Priming is best-effort; tests will still run
    return local_ams_stack


@pytest.fixture(scope="session")
def openai_api_key() -> str:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        pytest.skip("OPENAI_API_KEY is required for API-backed integration tests.")
    return api_key


@pytest.fixture(scope="session")
def openai_model_name() -> str:
    return os.environ.get("NAT_OPENAI_MODEL", "gpt-4o-mini")


@contextmanager
def _running_stack(
    *,
    enable_discrete_extraction: bool,
    openai_api_key: str | None,
) -> Iterator[dict[str, str]]:
    ready, reason = _docker_ready()
    if not ready:
        pytest.skip(reason)

    compose_dir = Path(__file__).resolve().parent
    project_name = f"nvidia_nat_redis_it_{uuid4().hex[:8]}"
    compose_env = {
        "COMPOSE_PROJECT_NAME": project_name,
        "REDIS_STACK_IMAGE": os.environ.get("REDIS_STACK_IMAGE", "redis/redis-stack:7.4.0-v8"),
        "AGENT_MEMORY_SERVER_IMAGE": os.environ.get(
            "AGENT_MEMORY_SERVER_IMAGE",
            "redislabs/agent-memory-server:0.14.0",
        ),
        "ENABLE_DISCRETE_MEMORY_EXTRACTION": "true" if enable_discrete_extraction else "false",
    }
    resolved_openai_api_key = openai_api_key if openai_api_key is not None else os.environ.get("OPENAI_API_KEY")
    if resolved_openai_api_key:
        compose_env["OPENAI_API_KEY"] = resolved_openai_api_key
    if openai_base_url := os.environ.get("OPENAI_BASE_URL"):
        compose_env["OPENAI_BASE_URL"] = openai_base_url

    original_env = {key: os.environ.get(key) for key in compose_env}
    os.environ.update(compose_env)

    from testcontainers.compose import DockerCompose

    compose = DockerCompose(
        context=str(compose_dir),
        compose_file_name="docker-compose.yml",
        pull=False,
    )
    compose.start()

    try:
        redis_host, redis_port = compose.get_service_host_and_port("redis", 6379)
        ams_host, ams_port = compose.get_service_host_and_port("agent-memory-server", 8000)
        ams_url = f"http://{ams_host}:{ams_port}"
        _wait_for_http(f"{ams_url}/openapi.json")

        yield {
            "redis_url": f"redis://{redis_host}:{redis_port}",
            "ams_url": ams_url,
        }
    finally:
        compose.stop()
        for key, value in original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@pytest.fixture(scope="session")
def ams_stack() -> dict[str, str]:
    with _running_stack(enable_discrete_extraction=False, openai_api_key=None) as stack:
        yield stack


@pytest.fixture(scope="session")
def ams_stack_with_api(openai_api_key: str) -> dict[str, str]:
    with _running_stack(enable_discrete_extraction=False, openai_api_key=openai_api_key) as stack:
        asyncio.run(_prime_long_term_search_index(stack["ams_url"]))
        yield stack


@pytest.fixture
def unique_suffix() -> str:
    return uuid4().hex[:10]


@pytest.fixture(scope="session")
def cloud_ams_stack() -> dict[str, str]:
    """Session-scoped fixture for the live Redis Agent Memory cloud service.

    Reads credentials from environment variables (load your .env before running):
      AGENT_MEMORY_ENDPOINT    – cloud service base URL
      AGENT_MEMORY_API_KEY     – Bearer token
      AGENT_MEMORY_STORE_ID    – cloud store ID

    Skips automatically when any variable is absent.
    """
    endpoint = os.environ.get("AGENT_MEMORY_ENDPOINT")
    api_key = os.environ.get("AGENT_MEMORY_API_KEY")
    store_id = os.environ.get("AGENT_MEMORY_STORE_ID")

    missing = [
        name
        for name, val in [
            ("AGENT_MEMORY_ENDPOINT", endpoint),
            ("AGENT_MEMORY_API_KEY", api_key),
            ("AGENT_MEMORY_STORE_ID", store_id),
        ]
        if not val
    ]

    if missing:
        pytest.skip(f"Cloud AMS credentials not set: {', '.join(missing)}")

    return {"base_url": endpoint, "api_key": api_key, "store_id": store_id}
