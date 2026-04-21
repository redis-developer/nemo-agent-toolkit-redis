# Redis Agent Memory Native Wrapper Example

This example uses the package's native Redis Agent Memory workflow:
`_type: redis_agent_memory_auto_memory`.

Instead of routing memory through NAT's generic `MemoryEditor` wrapper, this
workflow lets Redis Agent Memory manage:

- session-scoped working memory
- `memory_prompt` hydration before each turn
- turn appends back into working memory
- background promotion into long-term memory

## Install

The inner agent in this example is NAT's `chat_completion` function, so the
base plugin install is enough:

```bash
pip install "nvidia-nat-redis"
```

For local development from this repository:

```bash
uv sync --group dev --extra test
```

## Configure

```bash
cp examples/agent_auto_memory/.env.example examples/agent_auto_memory/.env
```

The example expects:

- `OPENAI_API_KEY` for NAT's OpenAI LLM and Redis Agent Memory extraction
- `REDIS_AGENT_MEMORY_URL`
- `REDIS_AGENT_MEMORY_NAMESPACE`
- `HOST_REDIS_PORT` and `HOST_REDIS_AGENT_MEMORY_PORT` if the default local ports are already occupied

## Start Services

```bash
docker compose \
  --env-file examples/agent_auto_memory/.env \
  -f examples/agent_auto_memory/compose.yml \
  up -d
```

## Validate And Run

```bash
uv run nat validate --config_file examples/agent_auto_memory/configs/config.yml
uv run python examples/agent_auto_memory/run_agent.py
```

The runner loads `examples/agent_auto_memory/.env` automatically.
By default it uses:

- `user_id=demo-user`
- `conversation_id=demo-session`

That stable `conversation_id` is what the wrapper maps to Redis Agent Memory
`session_id`.

Example custom run:

```bash
uv run python examples/agent_auto_memory/run_agent.py \
  --user-id alice \
  --conversation-id alice-session \
  --input "Remember that I prefer concise answers." \
  --input "How should you answer me?"
```

## Stop Services

```bash
docker compose \
  --env-file examples/agent_auto_memory/.env \
  -f examples/agent_auto_memory/compose.yml \
  down -v
```

## Notes

- This example is self-contained: its own `.env`, Compose file, config, README, and runner live in `examples/agent_auto_memory/`.
- The memory backend config still uses `_type: redis_agent_memory_backend`; that is the
  long-term memory surface shared by both examples.
- The workflow wrapper is the differentiated path when you want automatic
  prompt hydration and working-memory continuity on every turn.
- The Compose file runs Redis Stack plus `agent-memory api --task-backend=asyncio`
  for a single-process local setup.
