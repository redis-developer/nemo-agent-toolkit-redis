# nvidia-nat-redis

`nvidia-nat-redis` is a standalone Redis-owned plugin for
[NVIDIA NeMo Agent Toolkit](https://github.com/NVIDIA/NeMo-Agent-Toolkit).
It registers a NAT memory backend for
[Redis Agent Memory Server](https://github.com/redis/agent-memory-server).

## Prerequisites

- Python 3.11-3.13
- `uv`
- A running Redis Agent Memory Server instance
- A NAT-compatible LLM provider and credentials for your chosen example

The included example expects Redis Agent Memory Server to be reachable at
`http://localhost:8000`.

## Install

```bash
pip install "nvidia-nat[langchain]" "nvidia-nat-redis"
```

Install from source:

```bash
git clone https://github.com/redis-developer/nvidia-nat-redis.git
cd nvidia-nat-redis
uv sync --no-sources --group dev --extra test
```

If you are developing this plugin next to a sibling `NeMo-Agent-Toolkit`
checkout, you can use the local editable NAT packages defined in
`tool.uv.sources`:

```bash
uv sync --group dev --extra test
```

You can also use the provided `Makefile`:

```bash
make setup
```

## Usage

The plugin exposes `_type: agent_memory_server`.

```yaml
memory:
  redis_memory:
    _type: agent_memory_server
    base_url: http://localhost:8000
    default_namespace: nat
```

For the example workflow:

```bash
export NVIDIA_API_KEY=<YOUR_NVIDIA_API_KEY>
uv run nat run --config_file examples/tool_memory/configs/config.yml
```

See [docs/configuration.md](docs/configuration.md) and
[examples/tool_memory/README.md](examples/tool_memory/README.md).

## Development

```bash
make lint
make test
make validate
make build
make check
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the basic development workflow.
