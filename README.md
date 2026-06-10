<div align="center">
<img src="assets/redis-logo.svg" alt="Redis" width="175px">

# Nvidia-NAT-Redis
</div>

**Redis-backed memory for [NVIDIA NeMo Agent Toolkit](https://github.com/NVIDIA/NeMo-Agent-Toolkit)** — production [Redis Agent Memory](https://redis.github.io/agent-memory-server/quick-start/) integrations plus direct Redis plugins (the historical `nat.plugins.redis` stack).

In NAT, long-term recall is usually wired through memory tools such as `get_memory` and `add_memory`, which delegate to a **`MemoryEditor`** implementation. This package adds Redis-backed options in **two families**: **Redis Agent Memory** (AMS over HTTP — richer lifecycle, optional auto-memory workflow) and **direct Redis** (JSON + vector search or plain KV inside `nat.plugins.redis`, no AMS).

This repo is the standalone home for integrations that used to ship under
[`packages/nvidia_nat_redis`](https://github.com/NVIDIA/NeMo-Agent-Toolkit/tree/develop/packages/nvidia_nat_redis) in the NeMo Agent Toolkit monorepo.

### Redis Agent Memory (full stack)
1. `_type: redis_agent_memory_backend` — Redis Agent Memory as a NAT `MemoryEditor` long-term memory backend.
2. `_type: redis_agent_memory_auto_memory` — A native Redis Agent Memory wrapper that uses working memory plus
`memory_prompt` hydration on every turn.
AMS runs as a separate service; these surfaces talk to it via the agent-memory client.

1. `_type: redis_agent_memory_backend` — Redis Agent Memory behind NAT’s standard **`MemoryEditor`** contract (fits tool-driven long-term memory).
2. `_type: redis_agent_memory_auto_memory` — A **`workflow` wrapper** (not just another editor): working memory, `memory_prompt` hydration every turn, turn capture, and promotion — the fullest AMS-shaped integration.

### Direct Redis (simple in-Redis memory)

Loaded via the `nat.plugins` setuptools entry point `nat_redis` (same name as
the historical monorepo package):

- `_type: redis_memory` — Vector search over JSON documents in Redis (RediSearch); requires a workflow `embedder` and Redis Stack (or Redis with search + JSON support). Implements **`MemoryEditor`**-style semantic memory without AMS.
- `_type: redis` — NAT **object store** on plain Redis key–value storage (not semantic long-term memory).

| | **Redis Agent Memory** | **Direct Redis** |
| --- | --- | --- |
| **Runs** | AMS service + Redis | Redis only (your NAT process uses the client) |
| **Best for** | Learning-style memory, working memory, AMS filters and APIs | Lightweight Redis-native memory or KV object store |
| **Tradeoff** | Operate AMS; HTTP path | Simpler ops for `redis` / `redis_memory`; vector path needs embedder + search-capable Redis |

Use **Redis Agent Memory** when you want the Redis Agent Memory feature set.
Use **`redis_memory`** when you only need a lightweight Redis-native `MemoryEditor`
without AMS.

### Python imports (same as NeMo)

Direct Redis support is implemented only under **`nat.plugins.redis`** (for example
`from nat.plugins.redis.redis_editor import RedisEditor`), matching NeMo Agent
Toolkit. The `nat.plugins` setuptools entry point **`nat_redis`** loads
`nat.plugins.redis.register`. Redis Agent Memory code lives under
**`nvidia_nat_redis.redis_agent_memory`**.


## Install

```bash
pip install nemo-agent-toolkit-redis
```

For local development in this repo:

```bash
git clone https://github.com/redis-developer/nvidia-nat-redis.git
cd nvidia-nat-redis
uv sync --group dev --extra test
```

## Choose A Surface

- Use `_type: redis_agent_memory_backend` when your workflow already uses NAT memory tools and you want Redis Agent Memory behind the standard `MemoryEditor` contract.
- Use `_type: redis_agent_memory_auto_memory` when you want Redis Agent Memory to own working-memory continuity, prompt hydration, and turn capture on every request. This exposes the richness of Redis Agent Memory in its fullest form.
- Use `_type: redis_memory` when you want the simpler direct-Redis memory from NeMo Agent Toolkit (Redis JSON + vector index, no Redis Agent Memory). You must configure an `embedder` reference and run a Redis deployment that supports the search commands used by the plugin.
- Use `_type: redis` when you need NAT’s Redis-backed **object store** (KV), not vector or AMS-backed semantic memory.

## Integration Modes

These examples cover the two **Redis Agent Memory** integration shapes (`MemoryEditor` backend vs `workflow` wrapper). For **`redis_memory`** / **`redis`**, see [Configuration reference](docs/configuration.md).

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

Both example directories are self-contained. Each includes a `.env.example`,
localhost-bound Docker Compose services, a NAT config, a runner, and a short
README.

## Configuration

- [Configuration reference](docs/configuration.md)
- [Redis Agent Memory quick start](https://redis.github.io/agent-memory-server/quick-start/)


## Development

```bash
make setup
make lint
make test
make validate
make build
make check
```

Integration tests are opt-in because they start real Redis and Agent Memory
Server containers:

```bash
make test-integration
```

The example Compose files are intended for local development. They bind Redis
and AMS to `127.0.0.1` and run AMS with auth disabled.

See [CONTRIBUTING.md](CONTRIBUTING.md).
