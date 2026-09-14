# Context and Execution

Read only when the source uses coding-agent defaults, planning, files, backends, sandboxes, skills, memory, or context management. Confirm imports and extras against the locked Harness version.

| Deep Agents feature | Harness candidate | Important difference |
| --- | --- | --- |
| coding-agent defaults | `Coder(workspace)` | Compare its documented composition first. It is an opinionated local coding harness, not a semantic guarantee of Deep Agents parity. Use individual capabilities when a component must change. |
| todo planning | `Planning` | Deep Agents before 0.7, or an explicitly configured `TodoListMiddleware`, exposes `write_todos`; current 0.7.x defaults do not. Add `Planning` only when that behavior exists or is an accepted addition. Harness has its own schema, tools, reminder injection, events, and stores. |
| local workspace files | `FileSystem` | It is a rooted local filesystem toolset, not Deep Agents' virtual `BackendProtocol`. Compare the installed tool schemas and source limits: for example, Deep Agents file tools use `file_path`, while Harness uses `path`, and backend file-size limits need a separate guard. |
| host or local shell execution | `Shell` | It runs host subprocesses. Its command filtering and path checks cannot preserve a sandbox backend's OS isolation. |
| remote isolated execution | `ModalSandbox` or another sandbox capability | Match lease, filesystem, network, credential, reconnect, timeout, and cleanup contracts. |
| model-written tool orchestration | `CodeMode` | Its Monty sandbox constrains generated Python, not the authority of wrapped host tools. It is not a shell backend. |
| Agent Skills | `Skills` | Harness scans `<name>/SKILL.md` libraries on the process filesystem at construction and keeps that snapshot. For a static slice, transform flat catalogs before construction. Preserve flat or writable catalogs, tool names and results, bundled files, scripts, standard-root discovery, and behavioral frontmatter with a focused service when those contracts matter. |
| repository instructions and asset inventory | `RepoContext` | It discovers and loads repository context; it does not replace skill activation or writable memory. |
| writable long-term memory | `Memory` | Check namespace, store, injection role and bound, search, concurrency, retention, and deletion. Deep Agents `memory=[...]` injects backend-relative files; use explicit instructions for static content, `RepoContext` only for repository-owned context, or an adapter/application store for arbitrary files. If callers use file tools at paths such as `/memory/...`, preserve those names and paths with a filesystem/tool adapter, or intentionally change callers and prompts. `Memory` has different tools and namespace semantics. |
| context summarization | compaction capabilities, often `TieredCompaction` | Harness rewrites Pydantic AI message history. Match thresholds, summary prompt and role, tool-pair validity, receipts, usage, and recovery. |
| oversized tool-result offload | `ToolOutputLimits` | It reduces tool returns only. Deep Agents human-message eviction and backend artifact capture need separate handling. Match thresholds, read-back, store lifetime, and serialization. |

For pluggable or composite backends, expose the existing storage or sandbox service through a focused toolset or capability. Preserve runtime-injected backend factories, route prefixes, path, tenancy, transaction, sandbox ownership, and lifecycle contracts instead of forcing them through local `FileSystem`.
Preserve each route's storage lifetime and ownership; do not replace an ephemeral `StateBackend` route with persistent filesystem or store state.

Enforce authorization and isolation at the backing service. Treat prompt instructions, globs, path checks, and shell allowlists as defense in depth.

Primary sources: [Deep Agents changelog](https://github.com/langchain-ai/deepagents/blob/main/libs/deepagents/CHANGELOG.md), [backends](https://docs.langchain.com/oss/python/deepagents/backends), [sandboxes](https://docs.langchain.com/oss/python/deepagents/sandboxes), [skills](https://docs.langchain.com/oss/python/deepagents/skills), [memory](https://docs.langchain.com/oss/python/deepagents/memory), and [Pydantic AI Harness](https://github.com/pydantic/pydantic-ai-harness).
