# Redis Agent Memory Plan for `nvidia-nat-redis`

## Purpose

This document lays out the step-by-step plan to evolve `nvidia-nat-redis`
from a thin Redis Agent Memory long-term memory backend into a complete,
partner-grade NAT integration package with two clear surfaces:

1. A standard NAT `MemoryEditor` backend for direct long-term memory use.
2. A Redis Agent Memory native wrapper that uses working memory and
   `memory_prompt` to provide the stronger Redis Agent Memory story.

The package should stay installable as a standalone NAT plugin, remain
compatible with the ongoing NeMo integration effort, and present a coherent
README, docs surface, and example set.

## Naming

Use **Redis Agent Memory** as the user-facing product name throughout the
package wherever practical:

- README
- docs
- examples
- config reference
- CI descriptions

Compatibility-sensitive items can remain unchanged for now:

- Python package name: `nvidia-nat-redis`
- module namespace: `nvidia_nat_redis`
- current memory type: `_type: redis_agent_memory_backend`

That lets us improve the story without breaking the current integration shape.

## Current Package State

Today the standalone package already has a solid starting point:

- A standalone NAT plugin package with entry-point discovery.
- A `MemoryEditor` implementation backed by Redis Agent Memory long-term memory APIs.
- A memory config model with retry support.
- Two example workflows:
  - tool-based memory
  - generic NAT `auto_memory_agent`
- CI, build, and local developer tooling.

Current implementation files:

- `src/nvidia_nat_redis/redis_agent_memory/memory.py`
- `src/nvidia_nat_redis/redis_agent_memory/editor.py`
- `src/nvidia_nat_redis/redis_agent_memory/register.py`

Current package strengths:

- Good standalone packaging shape
- Good NAT plugin registration model
- Good parity story for simple long-term memory usage

Current gap:

- The package only exposes Redis Agent Memory as a long-term memory backend.
- It does not yet expose the product’s strongest features:
  - working memory
  - session-scoped turn continuity
  - `memory_prompt`
  - background summarization and promotion

## Product Story We Want

After this buildout, the package should tell one simple story:

### Surface 1: Direct Memory Backend

Use Redis Agent Memory as a NAT `MemoryEditor` when:

- the agent uses NAT memory tools such as `get_memory` and `add_memory`
- the user wants explicit, LLM-driven memory access
- the user wants Redis Agent Memory primarily as a long-term memory store

### Surface 2: Redis Agent Memory Native Wrapper

Use the Redis Agent Memory native wrapper when:

- the user wants memory applied automatically on every turn
- the user wants session-aware working memory
- the user wants `memory_prompt`-driven prompt hydration
- the user wants Redis Agent Memory to manage summarization and long-term promotion

This is the complete package story:

- **MemoryEditor** for basic NAT memory interoperability
- **Redis Agent Memory wrapper** for the full Redis-native experience

## NAT Touchpoints That Matter

The plan should stay aligned to how NAT actually works.

### NAT `MemoryEditor`

This is the storage/retrieval contract used by NAT memory backends.

Role for Redis Agent Memory:

- long-term memory CRUD
- compatibility with generic NAT memory tools
- compatibility with workflows that expect a standard NAT memory backend

### NAT Memory Tools

These are the generic tools like `get_memory`, `add_memory`, and delete/search
helpers that call `MemoryEditor`.

Role for Redis Agent Memory:

- keep this path working cleanly
- treat Redis Agent Memory as a durable long-term memory system here

### NAT Runtime Context

NAT runtime context already carries:

- `user_id`
- `conversation_id`

Role for Redis Agent Memory:

- `user_id` should map to Redis Agent Memory `user_id`
- `conversation_id` should map to Redis Agent Memory `session_id`

This is identity and correlation, not storage. We should consume it, not try to
turn NAT session context itself into working memory.

### NAT `auto_memory_agent`

The generic NAT wrapper is useful precedent, but it is not the right home for
the complete Redis Agent Memory experience.

Reason:

- it only understands `MemoryEditor`
- it performs generic add/search behavior
- it does not natively model working memory or `memory_prompt`

Plan:

- keep the generic wrapper example only as a compatibility bridge if useful
- build a separate Redis Agent Memory native wrapper in this package

## Target Architecture

### 1. Keep the Existing Long-Term Memory Backend

Keep the current backend as the package’s standard NAT memory provider.

Responsibilities:

- `add_items()` uses Redis Agent Memory long-term memory creation
- `search()` uses Redis Agent Memory long-term memory search
- `remove_items()` uses Redis Agent Memory long-term memory deletion

Expected positioning:

- “Redis Agent Memory as a NAT long-term memory backend”

### 2. Add a Shared Redis Agent Memory Client Layer

Add an internal client factory/helper layer so both the backend and the new
wrapper build the same Redis Agent Memory SDK client in one place.

Why:

- current client creation lives inside the memory registration function
- the native wrapper will need raw Redis Agent Memory SDK access, not just a
  `MemoryEditor`
- retry and default config behavior should stay consistent

Proposed file:

- `src/nvidia_nat_redis/redis_agent_memory/client_factory.py`

Responsibilities:

- create `MemoryAPIClient`
- apply standard defaults from memory config
- keep retry patching logic centralized where possible

### 3. Add a Redis Agent Memory Native Wrapper

Add a new workflow/function type in this repo dedicated to Redis Agent Memory.

Suggested type name:

- `_type: redis_agent_memory_auto_memory`

Alternative if we want shorter naming:

- `_type: redis_agent_memory_agent`

The wrapper should:

- resolve `user_id` and `session_id` from NAT runtime context
- call Redis Agent Memory `memory_prompt(...)`
- invoke an inner NAT agent with the hydrated prompt
- append the completed turn into Redis Agent Memory working memory

This should live in the standalone package, not in NAT core.

Proposed module tree:

- `src/nvidia_nat_redis/redis_agent_memory/auto_memory/config.py`
- `src/nvidia_nat_redis/redis_agent_memory/auto_memory/service.py`
- `src/nvidia_nat_redis/redis_agent_memory/auto_memory/register.py`

### 4. Keep Both Surfaces First-Class

The package should intentionally support both:

- explicit tool-driven long-term memory
- automatic Redis Agent Memory native orchestration

That is the right package boundary.

## Redis Agent Memory SDK Functions To Use

### Long-Term Memory Backend

Use these in the existing `MemoryEditor` integration:

- `create_long_term_memory(...)`
- `search_long_term_memory(...)`
- `delete_long_term_memories(...)`

Optional later additions:

- `get_long_term_memory(...)`
- `edit_long_term_memory(...)`

### Redis Agent Memory Native Wrapper

Use these for the new wrapper:

- `get_or_create_working_memory(...)`
- `memory_prompt(...)`
- `append_messages_to_working_memory(...)`
- `put_working_memory(...)` only when session initialization needs explicit
  fields such as `ttl_seconds`

Possible later additions, not required for v1:

- `delete_working_memory(...)`
- `hydrate_memory_prompt(...)`
- `set_working_memory_data(...)`
- `update_working_memory_data(...)`

## Wrapper Flow

The Redis Agent Memory native wrapper should follow this turn sequence:

1. Resolve runtime identity.
   - `user_id` from NAT `Context`
   - `session_id` from NAT `conversation_id`
2. Create or load working memory.
   - call `get_or_create_working_memory(...)`
3. Initialize optional session settings if needed.
   - use `put_working_memory(...)` only if we need to persist `ttl_seconds` or
     other initial working memory fields not handled on creation
4. Build retrieval context.
   - call `memory_prompt(...)`
5. Invoke the inner agent.
   - pass the hydrated messages plus the current user message
6. Store the completed turn.
   - call `append_messages_to_working_memory(...)` with the user and assistant
     turn messages

This gives Redis Agent Memory the right place to handle:

- working memory continuity
- summarization pressure
- background extraction
- long-term promotion

## Config Design

Keep config split by responsibility.

### Memory Config

This remains the connection and client-default config for the Redis Agent
Memory backend.

Current fields to keep:

- `base_url`
- `default_namespace`
- `timeout`
- `default_model_name`
- `default_context_window_max`
- retry settings from `RetryMixin`

Use this config for both:

- the `MemoryEditor` backend
- the native wrapper’s client creation

### Wrapper Config

The wrapper gets its own typed config model.

Suggested shape:

```yaml
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
    model_name: null
    context_window_max: null
    ttl_seconds: 86400
    long_term_memory_strategy:
      strategy: discrete
```

Suggested wrapper config fields:

- `inner_agent_name`
- `memory_name`
- `default_user_id`
- `default_session_id`

Nested `memory_prompt` fields:

- `optimize_query`
- `long_term_search`

Nested `working_memory` fields:

- `namespace`
- `model_name`
- `context_window_max`
- `ttl_seconds`
- `long_term_memory_strategy`

### Runtime Identity Rules

Do not require `user_id` or `session_id` in static YAML when NAT already
provides runtime identity.

Rules:

- prefer `Context.user_id`
- prefer `Context.conversation_id`
- only fall back to `default_user_id` and `default_session_id` if runtime
  values are absent

## Dependency Strategy

Keep the package dependency story simple and intentional.

### Base Package

The standalone plugin package should remain installable with the smallest
reasonable runtime dependency set:

- `nvidia-nat-core`
- `agent-memory-client`

This keeps the Redis Agent Memory integration lightweight and avoids forcing a
specific agent stack on all users.

### Examples

Examples can require additional NAT packages when needed, but those
requirements should live in the example READMEs, not in the root README as a
global install instruction.

Rules:

- root README explains base package installation
- example READMEs explain workflow-specific installs
- langchain-backed examples can require `nvidia-nat[langchain]`
- the package itself should not grow a hard dependency on `nvidia-nat-langchain`
  unless implementation forces it

### Wrapper Dependency Goal

Prefer implementing the Redis Agent Memory native wrapper so it can wrap a NAT
inner function without directly depending on `nvidia-nat-langchain`.

That keeps:

- the package runtime lean
- the wrapper usable with broader NAT function patterns
- example dependencies separate from core package dependencies

## Changes Needed to the Existing Integration

### Change 1: Keep the Current Backend LTM-Focused

Do not turn the current `MemoryEditor` into a working memory abstraction.

Adjustments to make:

- document clearly that it is a long-term memory backend
- optionally infer `session_id` from NAT context when none is provided
- keep runtime passthrough for topics, entities, namespace, and memory type

### Change 2: Extract Shared Client Creation

Move client construction out of `memory.py` into a dedicated helper so the
wrapper and backend share the same client creation path.

### Change 3: Register the New Wrapper

Update package registration so the new Redis Agent Memory wrapper is
discoverable as a NAT component alongside the memory backend.

### Change 4: Clarify User-Facing Language

Use “Redis Agent Memory” in docs, examples, and README rather than “Agent
Memory Server” except where compatibility or external API names require the
legacy wording.

## Proposed Repository Buildout

### Phase 0: Documentation and Naming Alignment

Goals:

- align package language around Redis Agent Memory
- clarify the package’s two planned integration surfaces
- make current docs match future direction without breaking current examples

Work:

- overhaul root `README.md`
- update `docs/configuration.md`
- update example READMEs
- add a short package architecture section to the README

README should clearly explain:

- what this package is
- that the base package is a standalone NAT plugin
- what it ships today
- what integration styles NAT users can choose
- which pieces are long-term memory only
- which pieces are Redis Agent Memory native
- where workflow-specific example dependencies live

### Phase 1: Internal Refactor for Shared Client Construction

Goals:

- introduce one Redis Agent Memory client creation path
- avoid duplicated connection/default logic

Work:

- add `client_factory.py`
- update `memory.py` to use it
- ensure retry behavior remains correct

Acceptance criteria:

- no behavior change in current backend
- existing tests still pass

### Phase 2: Harden the Existing Long-Term Memory Backend

Goals:

- preserve compatibility
- make the backend clearly and intentionally long-term memory only

Work:

- keep current `MemoryEditor` shape
- add context-derived `session_id` fallback if practical
- tighten validation and docstrings
- confirm deletion/search behavior against current SDK

Acceptance criteria:

- tool-based example continues to work
- no breaking changes to `_type: redis_agent_memory_backend`

### Phase 3: Add Native Wrapper Config Models

Goals:

- define a clean, typed configuration surface for Redis Agent Memory native
  orchestration

Work:

- add `config.py` for wrapper config models
- model nested `memory_prompt` settings
- model nested `working_memory` settings
- keep runtime identity separate from static config

Acceptance criteria:

- config validates cleanly with `nat validate`
- config fields reflect actual Redis Agent Memory SDK usage

### Phase 4: Implement the Wrapper Service Layer

Goals:

- isolate Redis Agent Memory orchestration logic from NAT registration code

Work:

- add `service.py`
- implement:
  - runtime identity resolution
  - working memory retrieval/creation
  - `memory_prompt` request building
  - working memory append logic
  - assistant response extraction

Acceptance criteria:

- service can be unit tested without a live server
- wrapper logic is readable and backend-specific behavior is centralized

### Phase 5: Register the Native Wrapper

Goals:

- expose a new NAT component for Redis Agent Memory automatic orchestration

Work:

- add `register.py` for the wrapper
- register the new workflow/function type
- resolve `memory_name` to the package’s memory config
- create a dedicated raw Redis Agent Memory SDK client from that config
- invoke inner NAT agent with enriched request payload

Acceptance criteria:

- wrapper can call a normal NAT inner agent
- wrapper does not require changes to NAT core

### Phase 6: Example Buildout

Goals:

- make the example story consistent and self-contained

Recommended examples after buildout:

1. `examples/tool_based_memory/`
   - explicit tool-driven long-term memory
2. `examples/agent_auto_memory/`
   - native Redis Agent Memory wrapper example

Work:

- keep `tool_based_memory`
- replace or rename the current generic auto-memory example to the new native
  wrapper example
- ensure each example has its own:
  - README
  - `.env.example`
  - `compose.yml`
  - config
  - runner, if needed

Acceptance criteria:

- each example can be followed without referring to another example directory
- examples reinforce the package’s two-surface story

### Phase 7: README and Docs Overhaul

Goals:

- make the package understandable in under two minutes
- make the docs reflect the actual integration choices

README target outline:

1. What `nvidia-nat-redis` is
2. What Redis Agent Memory provides in NAT
3. Integration modes
   - direct memory backend
   - native wrapper
4. Install
5. Quick config example
6. Example index
7. Development and validation commands

Docs target outline:

- `docs/configuration.md`
  - backend config
  - runtime kwargs for `MemoryEditor`
  - wrapper config
  - compatibility notes

Acceptance criteria:

- package story is coherent
- docs use Redis Agent Memory naming consistently
- users can see exactly what is supported today

### Phase 8: Test Buildout

Goals:

- validate correctness without requiring a live Redis Agent Memory server in CI

Unit tests to add:

- shared client factory tests
- wrapper config validation tests
- user/session resolution tests
- `memory_prompt` request construction tests
- working memory append tests
- first-session setup and TTL behavior tests
- response extraction tests
- error handling when `memory_name` does not point to the right config type

Existing tests to keep:

- long-term memory editor tests

Acceptance criteria:

- `pytest` covers both surfaces
- wrapper behavior is stable and explicit

### Phase 9: Example Validation and Manual Smoke Tests

Goals:

- verify the package story end to end

Static validation:

- `uv run ruff check .`
- `uv run pytest`
- `uv run nat validate --config_file ...` for each example
- `uv build --no-sources`

Manual live validation:

1. Start Redis Agent Memory locally with the example Compose file.
2. Run the tool-based example.
3. Confirm direct long-term memory add/search works.
4. Run the native wrapper example with a stable `conversation_id`.
5. Confirm:
   - working memory persists across turns
   - prompt hydration includes prior context
   - long-term extraction appears over time

Acceptance criteria:

- the basic backend works as a long-term store
- the native wrapper demonstrates the differentiated Redis Agent Memory story

### Phase 10: CI/CD Updates

Goals:

- keep the package releasable as the feature set expands

Work:

- expand CI to validate both examples
- keep lint, tests, validate, and build in CI
- consider adding an optional integration-test job later if secrets and service
  bootstrapping become practical

Acceptance criteria:

- CI reflects the package’s actual supported surfaces

## Implementation Order

Recommended order of execution:

1. README/docs naming pass and package story alignment
2. shared client factory extraction
3. backend hardening and documentation cleanup
4. wrapper config models
5. wrapper service implementation
6. wrapper registration
7. native wrapper example
8. test expansion
9. CI updates
10. live smoke validation

This order keeps the repo understandable while the implementation grows.

## What We Should Not Do Yet

- Do not modify NAT core first.
- Do not try to overload NAT session context into a storage system.
- Do not force Redis Agent Memory working memory into the generic
  `MemoryEditor` contract.
- Do not broaden the package into the full first-party Redis object store
  surface right now.

Those can be revisited later, but they are not required for the right Redis
Agent Memory story.

## Risks and Watchouts

### SDK Stability

`agent-memory-client` is still pre-1.0, so we should:

- pin within a narrow compatible range
- verify exact SDK method names in tests
- avoid overfitting to undocumented behavior

### Package Name Collision

This standalone package still shares the `nvidia-nat-redis` distribution name
with NVIDIA’s first-party package. That does not block development, but it is a
release and ownership issue that will need a coordinated PyPI handoff if this
becomes the canonical package.

### Naming Consistency

User-facing materials should say Redis Agent Memory, but compatibility-sensitive
type names and current file paths may still reference `redis_agent_memory`.
That is acceptable if the docs explain it clearly.

## Definition of Done

The package is in a strong state when all of the following are true:

- Redis Agent Memory long-term memory backend is stable and documented
- Redis Agent Memory native wrapper is implemented and registered
- the package README clearly explains both integration modes
- docs match the implemented config surfaces
- examples are self-contained and coherent
- tests cover both the backend and the wrapper
- CI validates lint, tests, config validation, and build
- one live end-to-end smoke test has been completed against a real Redis Agent
  Memory instance

## Immediate Next Build Tasks

The first concrete build slice should be:

1. Overhaul the README and `docs/configuration.md` around Redis Agent Memory
   naming and the two-surface package story.
2. Add `client_factory.py` and refactor `memory.py` to use it.
3. Add the wrapper config and service scaffolding.
4. Register the native wrapper type.
5. Build the new native wrapper example and validate it.

That is the shortest path to turning the current backend into a complete,
coherent Redis Agent Memory package for NAT.
