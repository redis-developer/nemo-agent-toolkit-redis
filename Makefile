.PHONY: setup setup-local lint test validate build check clean

setup:
	uv sync --no-sources --group dev --extra test

setup-local:
	uv sync --group dev --extra test

lint:
	uv run ruff check .

test:
	uv run python -m pytest

validate:
	uv run nat validate --config_file examples/tool_memory/configs/config.yml

build:
	uv build --no-sources

check: lint test validate

clean:
	rm -rf build dist .pytest_cache .ruff_cache
	rm -rf tests/__pycache__ src/*.egg-info src/nvidia_nat_redis/__pycache__
	rm -rf src/nvidia_nat_redis/agent_memory_server/__pycache__
