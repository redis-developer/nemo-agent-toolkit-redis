# Tool-Based Memory Example

This example keeps memory explicit. The agent uses NAT's `get_memory` and
`add_memory` tools, while Redis Agent Memory provides the backend long-term
memory storage.

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

Edit `examples/tool_based_memory/.env` with the credentials you want to use:

- `OPENAI_API_KEY` for NAT's OpenAI LLM and Redis Agent Memory extraction
- `REDIS_AGENT_MEMORY_URL` and `REDIS_AGENT_MEMORY_NAMESPACE` for the memory server
- `HOST_REDIS_PORT` and `HOST_REDIS_AGENT_MEMORY_PORT` if the default local ports are already occupied

## Start Services

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

- This example is self-contained: its own `.env`, Compose file, config, README, and runner live in `examples/tool_based_memory/`.
- Tool-based memory depends on the LLM deciding to call memory tools. The automatic wrapper example is the stronger fit if you want guaranteed capture and retrieval on every turn.
