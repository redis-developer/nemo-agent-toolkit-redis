# Configuration

After installation, NAT discovers the plugin automatically and makes the memory
type available as `_type: agent_memory_server`.

```yaml
memory:
  redis_memory:
    _type: agent_memory_server
    base_url: http://localhost:8000
    default_namespace: nat
    timeout: 30.0
```

Supported config fields:

- `base_url`
- `default_namespace`
- `timeout`
- `default_model_name`
- `default_context_window_max`
- NAT retry settings from `RetryMixin`

Runtime behavior stays close to NAT's `MemoryEditor` surface:

- `search()` requires `user_id` and forwards common AMS filters such as `session_id`, `namespace`, `topics`, `entities`, and `memory_type`
- `add_items()` accepts optional AMS fields such as `session_id`, `namespace`, `entities`, `event_date`, and `memory_type`
- `remove_items()` supports direct deletion by `memory_id` / `memory_ids` or filtered deletion by search
