# Tool-Based Memory Example

This example keeps memory explicit. A NAT `react_agent` decides when to call
`get_memory` and `add_memory`; Redis Agent Memory stores and searches the
long-term memories behind those tools.

## Install

This workflow uses NAT's `react_agent`, so it needs the langchain-backed NAT
agent package in addition to this plugin.

For a published install:

```bash
pip install "nvidia-nat[langchain]" "nvidia-nat-redis"
```

For local development from this repository:

```bash
uv sync --group dev --extra test
```

## Configure

```bash
cp examples/tool_based_memory/.env.example examples/tool_based_memory/.env
```

Edit `examples/tool_based_memory/.env`:

- `OPENAI_API_KEY` for NAT's OpenAI LLM and Redis Agent Memory extraction
- `REDIS_AGENT_MEMORY_URL` and `REDIS_AGENT_MEMORY_NAMESPACE` for the memory server
- `HOST_REDIS_PORT` and `HOST_REDIS_AGENT_MEMORY_PORT` if the default local ports are already occupied
- `REDIS_STACK_IMAGE` and `AGENT_MEMORY_SERVER_IMAGE` if you need to override the tested image tags

## Start Services

Compose starts Redis Stack and Agent Memory Server for local development. Both
ports bind to `127.0.0.1`, and AMS auth is disabled.

```bash
docker compose \
  --env-file examples/tool_based_memory/.env \
  -f examples/tool_based_memory/compose.yml \
  up -d
```

## Validate And Run

The runner loads `examples/tool_based_memory/.env` automatically and passes a
stable `user_id` plus `conversation_id` into NAT so the same user can retrieve
what was stored earlier.

```bash
uv run nat validate --config_file examples/tool_based_memory/configs/config.yml
uv run python examples/tool_based_memory/run_agent.py
```

Default identity:

- `user_id=demo-user`
- `conversation_id=demo-session`

Example custom run:

```bash
uv run python examples/tool_based_memory/run_agent.py \
  --user-id alice \
  --conversation-id alice-session \
  --input "Remember that I prefer concise answers." \
  --input "How should you answer me?"
```

If you prefer the raw NAT CLI, you can still run `nat run` directly against the
same config. The runner is just the easiest way to provide stable session
context locally.

## Stop Services

```bash
docker compose \
  --env-file examples/tool_based_memory/.env \
  -f examples/tool_based_memory/compose.yml \
  down -v
```

## Notes

- Tool-based memory depends on the LLM choosing the memory tools.
- Use `agent_auto_memory` when every turn should be captured and hydrated automatically.
