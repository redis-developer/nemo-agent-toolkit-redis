# Configuration

This package ships two Redis Agent Memory surfaces for NAT:

1. A long-term memory backend: `_type: redis_agent_memory_backend`
2. A native automatic wrapper: `_type: redis_agent_memory_auto_memory`

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

## Examples

- [Redis Agent Memory native wrapper](../examples/agent_auto_memory/README.md)
- [Tool-based long-term memory](../examples/tool_based_memory/README.md)
