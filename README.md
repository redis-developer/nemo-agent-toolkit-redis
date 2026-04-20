# nvidia-nat-redis

`nvidia-nat-redis` is a standalone Redis-owned plugin for
[NVIDIA NeMo Agent Toolkit](https://github.com/NVIDIA/NeMo-Agent-Toolkit).
It registers a NAT memory backend for
[Redis Agent Memory Server](https://github.com/redis/agent-memory-server).

## Install

```bash
pip install "nvidia-nat[langchain]" "nvidia-nat-redis"
```

For local development:

```bash
uv sync --no-sources --group dev --extra test
```

If you are working next to a sibling `NeMo-Agent-Toolkit` checkout, `uv sync`
without `--no-sources` will use the local NAT packages defined in
`tool.uv.sources`.

## Usage

The plugin exposes `_type: agent_memory_server`.

```yaml
memory:
  redis_memory:
    _type: agent_memory_server
    base_url: http://localhost:8000
    default_namespace: nat
```

See [docs/configuration.md](docs/configuration.md) and
[examples/tool_memory/README.md](examples/tool_memory/README.md).

## Validate

```bash
uv run ruff check .
uv run python -m pytest
uv run nat validate --config_file examples/tool_memory/configs/config.yml
uv build --no-sources
```
