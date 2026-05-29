# SPDX-FileCopyrightText: Copyright (c) 2026, Redis
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-integration",
        action="store_true",
        default=False,
        help="Run integration tests that start real Redis and Redis Agent Memory containers.",
    )
    parser.addoption(
        "--run-api-tests",
        action="store_true",
        default=False,
        help="Run integration tests that require external API credentials.",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "integration: mark a test as requiring Docker-backed Redis and Redis Agent Memory services",
    )
    config.addinivalue_line(
        "markers",
        "requires_api_keys: mark a test as requiring external API credentials",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    run_integration = config.getoption("--run-integration")
    run_api_tests = config.getoption("--run-api-tests")

    skip_integration = pytest.mark.skip(reason="Skipping integration tests. Use --run-integration to run them.")
    skip_api = pytest.mark.skip(reason="Skipping API-backed tests. Use --run-api-tests to run them.")

    for item in items:
        if item.get_closest_marker("integration") and not run_integration:
            item.add_marker(skip_integration)
        if item.get_closest_marker("requires_api_keys") and not run_api_tests:
            item.add_marker(skip_api)
