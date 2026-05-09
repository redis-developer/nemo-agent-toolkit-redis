# Configuration

This package ships Redis Agent Memory surfaces and direct Redis plugins (migrated
from the NeMo Agent Toolkit `nvidia_nat_redis` package).

Redis Agent Memory is a production-ready agent memory layer that extracts and stores
relevant information and learns over time.

Direct Redis memory provides a more standard semantic memory layer for LLM applications.

**Full Redis Agent Memory auto wrapper**

1. Long-term memory backend: `_type: redis_agent_memory_backend`
2. Native automatic wrapper: `_type: redis_agent_memory_auto_memory`

**Direct Redis (entry point `nat_redis` → `nat.plugins.redis.register`)**

3. In-Redis vector memory: `_type: redis_memory`
4. Object store: `_type: redis`

For Python code, use **`nat.plugins.redis.*`** — the same module layout as NeMo
Agent Toolkit’s in-tree package.

## Long-Term Memory Backend

Use `_type: redis_agent_memory_backend` when you want Redis Agent Memory behind NAT's
standard `MemoryEditor` contract.

```yaml
memory:
  redis_memory:
    _type: redis_agent_memory_backend
    base_url: http://localhost:8000
    default_namespace: nat
    timeout: 30.0
    default_model_name: null
    default_context_window_max: null
```

Supported config fields:

- `base_url`
- `default_namespace`
- `timeout`
- `default_model_name`
- `default_context_window_max`
- NAT retry settings from `RetryMixin`

Runtime behavior:

- `add_items()` creates long-term memories with optional AMS fields such as `session_id`, `namespace`, `entities`, `event_date`, and `memory_type`
- `add_items()` falls back to NAT `conversation_id` for `session_id` when no explicit value is provided
- `search()` requires `user_id` and forwards AMS filters such as `session_id`, `namespace`, `topics`, `entities`, `memory_type`, `distance_threshold`, and `recency`
- `search()` falls back to NAT `user_id` when the current runtime context provides one
- `remove_items()` supports direct deletion by `memory_id` / `memory_ids` or filtered deletion by search

## Native Wrapper

Use `_type: redis_agent_memory_auto_memory` when you want Redis Agent
Memory to manage working memory plus `memory_prompt` hydration on every turn.

```yaml
memory:
  redis_memory:
    _type: redis_agent_memory_backend
    base_url: http://localhost:8000
    default_namespace: nat

functions:
  assistant_chat:
    _type: chat_completion
    llm_name: openai_llm

workflow:
  _type: redis_agent_memory_auto_memory
  inner_agent_name: assistant_chat
  memory_name: redis_memory
  default_user_id: default_user
  default_session_id: default_session

  memory_prompt:
    optimize_query: false
    long_term_search:
      limit: 5

  working_memory:
    namespace: nat
    model_name: gpt-4o-mini
    context_window_max: null
    ttl_seconds: 86400
    long_term_memory_strategy:
      strategy: discrete
```

Wrapper fields:

- `inner_agent_name`: NAT function that receives the hydrated `ChatRequest`
- `memory_name`: name of an `_type: redis_agent_memory_backend` memory config
- `default_user_id`: fallback when NAT runtime context has no `user_id`
- `default_session_id`: fallback when NAT runtime context has no `conversation_id`
- `memory_prompt.optimize_query`
- `memory_prompt.long_term_search.limit`
- `memory_prompt.long_term_search.offset`
- `memory_prompt.long_term_search.namespace`
- `memory_prompt.long_term_search.topics`
- `memory_prompt.long_term_search.entities`
- `memory_prompt.long_term_search.memory_type`
- `memory_prompt.long_term_search.distance_threshold`
- `memory_prompt.long_term_search.recency.*`
- `working_memory.namespace`
- `working_memory.model_name`
- `working_memory.context_window_max`
- `working_memory.ttl_seconds`
- `working_memory.long_term_memory_strategy.strategy`
- `working_memory.long_term_memory_strategy.config`

Runtime identity mapping:

- NAT `user_id` -> Redis Agent Memory `user_id`
- NAT `conversation_id` -> Redis Agent Memory `session_id`
- `default_user_id` and `default_session_id` are only fallbacks

Wrapper constraints:

- `memory_name` must point to an `_type: redis_agent_memory_backend` memory config
- `inner_agent_name` must accept `ChatRequest` or `ChatRequestOrMessage`
- the wrapper stores the completed user/assistant turn back into working memory after the inner agent responds

Wrapper flow:

1. Resolve NAT `user_id` and `conversation_id`.
2. Create or load Redis Agent Memory working memory for that identity.
3. Call `memory_prompt(...)`, invoke the inner chat function with the hydrated request, and append the finished turn back into working memory.

## Direct Redis memory (`redis_memory`)

Use `_type: redis_memory` for the lightweight Redis-backed `MemoryEditor` that
stores JSON documents and uses RediSearch vector queries. This matches the
behavior of the standalone
[`nvidia_nat_redis` package in NeMo Agent Toolkit](https://github.com/NVIDIA/NeMo-Agent-Toolkit/tree/develop/packages/nvidia_nat_redis).

Requirements:

- Redis with **JSON** and **search** (for example Redis Stack), because the implementation creates a JSON index and runs vector KNN queries.
- A workflow **embedder** instance; `embedder` must reference its name in the workflow configuration.

```yaml
memory:
  redis_ltm:
    _type: redis_memory
    host: localhost
    port: 6379
    db: 0
    key_prefix: nat
    embedder: my_openai_embedder
```

Supported config fields:

- `host`, `port`, `db`, `password` (optional secret), `key_prefix`
- `embedder`: `EmbedderRef` to a configured embedder (LangChain wrapper)

## Direct Redis object store (`redis`)

Use `_type: redis` for the NAT object store that persists `ObjectStoreItem`
JSON at keys under `nat/object_store/{bucket_name}/...`, with optional TTL.

```yaml
object_store:
  artifacts:
    _type: redis
    host: localhost
    port: 6379
    db: 0
    bucket_name: my_bucket
    ttl: 3600
```

## Examples

- [Redis Agent Memory native wrapper](../examples/agent_auto_memory/README.md)
- [Tool-based long-term memory](../examples/tool_based_memory/README.md)
