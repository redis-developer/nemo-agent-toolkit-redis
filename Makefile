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
	uv run nat validate --config_file examples/tool_based_memory/configs/config.yml
	uv run nat validate --config_file examples/agent_auto_memory/configs/config.yml
	uv run python examples/tool_based_memory/run_agent.py --help
	uv run python examples/agent_auto_memory/run_agent.py --help

build:
	uv build --no-sources

check: lint test validate

clean:
	rm -rf build dist .pytest_cache .ruff_cache
	rm -rf tests/__pycache__ src/*.egg-info src/nvidia_nat_redis/__pycache__
	rm -rf src/nvidia_nat_redis/redis_agent_memory/__pycache__
	rm -rf src/nvidia_nat_redis/redis_agent_memory/auto_memory/__pycache__
	rm -rf examples/agent_auto_memory/__pycache__ examples/tool_based_memory/__pycache__
