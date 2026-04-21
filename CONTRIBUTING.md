# Contributing

## Setup

For a clean standalone environment:

```bash
make setup
```

If you are developing next to a sibling `NeMo-Agent-Toolkit` checkout and want
to use local editable NAT packages:

```bash
make setup-local
```

## Development Workflow

1. Create a branch for your change.
2. Make the code or documentation update.
3. Run the local checks:

```bash
make check
make build
```

4. Open a pull request with a short description of the change and any testing
   notes.

For release or handoff candidates, also run the example smoke tests against a
local Redis Agent Memory stack. Both example directories are self-contained and
document their Docker and runner commands.

## Style

- Keep changes focused and small.
- Follow the existing `src/` package layout.
- Use `uv` for local development and packaging.
- Prefer adding or updating tests when behavior changes.
