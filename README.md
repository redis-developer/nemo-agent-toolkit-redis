# nvidia-nat-redis

Redis Agent Memory integrations for
[NVIDIA NeMo Agent Toolkit](https://github.com/NVIDIA/NeMo-Agent-Toolkit).

This standalone plugin now exposes two first-class NAT surfaces:

1. `_type: redis_agent_memory_backend`
   Redis Agent Memory as a NAT `MemoryEditor` long-term memory backend.
2. `_type: redis_agent_memory_auto_memory`
   A native Redis Agent Memory wrapper that uses working memory plus
   `memory_prompt` hydration on every turn.

Compatibility-sensitive identifiers stay unchanged:

- Python package: `nvidia-nat-redis`
- module namespace: `nvidia_nat_redis`
- plugin entry point namespace: `nat.components`

## Install

```bash
pip install "nvidia-nat-redis"
```

For local development in this repo:

```bash
git clone https://github.com/redis-developer/nvidia-nat-redis.git
cd nvidia-nat-redis
uv sync --group dev --extra test
```

If you want a dependency-resolved install without sibling source overrides:

```bash
uv sync --no-sources --group dev --extra test
```

Local development expects a sibling `../NeMo-Agent-Toolkit` checkout because
`tool.uv.sources` points at NAT source packages directly.

## Choose A Surface

- Use `_type: redis_agent_memory_backend` when your workflow already uses NAT memory tools and you want Redis Agent Memory behind the standard `MemoryEditor` contract.
- Use `_type: redis_agent_memory_auto_memory` when you want Redis Agent Memory to own working-memory continuity, prompt hydration, and turn capture on every request.

## Integration Modes

### Direct Long-Term Memory Backend

Use `_type: redis_agent_memory_backend` when you want Redis Agent Memory behind NAT's
generic memory tools such as `get_memory` and `add_memory`.

```yaml
memory:
  redis_memory:
    _type: redis_agent_memory_backend
    base_url: http://localhost:8000
    default_namespace: nat
```

### Native Redis Agent Memory Wrapper

Use `_type: redis_agent_memory_auto_memory` when you want:

- working-memory continuity by `conversation_id`
- automatic `memory_prompt` hydration
- completed turns appended back into Redis Agent Memory
- background promotion into long-term memory

```yaml
workflow:
  _type: redis_agent_memory_auto_memory
  inner_agent_name: assistant_chat
  memory_name: redis_memory

  memory_prompt:
    optimize_query: false
    long_term_search:
      limit: 5

  working_memory:
    namespace: nat
    ttl_seconds: 86400
    long_term_memory_strategy:
      strategy: discrete
```

The wrapper resolves runtime identity from NAT context:

- `user_id` -> Redis Agent Memory `user_id`
- `conversation_id` -> Redis Agent Memory `session_id`

## Examples

- [Agent auto-memory example](examples/agent_auto_memory/README.md)
- [Tool-based long-term memory example](examples/tool_based_memory/README.md)

Both example directories are self-contained and include their own `.env.example`,
`compose.yml`, NAT config, and usage README. Each example can be brought up and
down without touching the other.

## Configuration

- [Configuration reference](docs/configuration.md)
- [Redis Agent Memory quick start](https://redis.github.io/agent-memory-server/quick-start/)

## Local Compatibility

One caveat remains: this standalone package and NVIDIA's first-party Redis
package currently share the `nvidia-nat-redis` distribution name. For local
testing, prefer an environment that installs this editable repo plus the NAT
packages you need, rather than mixing it with NVIDIA's first-party Redis
package in the same environment.

## Development

```bash
make setup-local
make lint
make test
make validate
make build
make check
```

See [CONTRIBUTING.md](CONTRIBUTING.md).
