---
name: migrating-deep-agents-to-pydantic-ai-harness
description: Migrate Python LangChain Deep Agents applications to Pydantic AI and Pydantic AI Harness. Use when the source uses the upstream `deepagents` package or demonstrably reproduces its middleware, backends, skills, memory, subagents, or sandbox contracts. Use `migrating-langchain-to-pydantic-ai` for plain LangChain, LangGraph, or LCEL migrations and `pydantic-ai-harness` for greenfield Harness usage.
---

# Migrate Deep Agents to Pydantic AI Harness

Preserve the application's observed contracts, not the shape of `create_deep_agent`. Compose Pydantic AI primitives and Harness capabilities; keep infrastructure with the application that already owns it.

## Establish the source contract

1. Resolve the `deepagents` import origin before applying these mappings. Route a local module or project-owned `create_deep_agent` to the ordinary LangChain migration only when its implementation has ordinary LangChain or LangGraph semantics; vendored implementations that reproduce Deep Agents contracts remain in scope. Read repository instructions, manifests, lockfiles, tests, runtime entrypoints, and the resolved source. Record exact Deep Agents, LangChain, LangGraph, Pydantic AI, and Harness versions.
   If the locked source is unavailable, reproduce its environment without changing the target lockfile. If that is not possible, mark affected behavior unverified and do not claim parity.
2. Trace one representative request through the effective prompt and profile, tools, middleware, backend routes, skills and memory, subagents, state, checkpointer, interrupts, events, limits, side effects, and public result. Inspect every caller of the migrated boundary.
3. Run the cheapest deterministic baseline. Record evidence separately from the outcome: `source-inspected`, `probe-observed`, or `regression-tested`.
4. Load only the mapping needed for the detected source features:
   - [Core Migration](references/CORE-MIGRATION.md) for agent construction, tools, middleware, state, outputs, or tracing.
   - [Context and Execution](references/CONTEXT-AND-EXECUTION.md) for coding defaults, planning, files, backends, sandboxes, skills, memory, or context limits.
   - [Orchestration and Recovery](references/ORCHESTRATION-AND-RECOVERY.md) for subagents, background work, approvals, checkpoints, durable execution, or streaming.

## Choose the owner

- Use Pydantic AI `Agent`, typed dependencies, outputs, tools, toolsets, deferred tools, history processors, hooks, and durable-execution primitives for core runtime behavior.
- Use Harness capabilities for optional compositions whose documented lifecycle matches the source behavior.
- Inspect the locked target version's public exports and capability docs. Add only the optional extras required by the selected capabilities.
- Keep queues, tenancy, artifact storage, remote sandbox lifecycle, durable domain state, deployment, and external side-effect recovery in application services.
- If public Pydantic AI primitives cannot implement required generic runtime semantics correctly, propose the core primitive instead of recreating the runtime in Harness.

When there is no direct equivalent, do not stop at "unsupported." State the source contract and impact, then recommend either an existing composition, a narrow adapter or application service, a new Harness capability built from public primitives, or a Pydantic AI core change. Ask before choosing when the options materially change behavior, architecture, public API, or scope.

## Migrate and verify

1. Port one vertical slice behind the existing application boundary: typed dependencies, one tool family, output, and one representative request.
2. Preserve source request, result, error, event, and continuation shapes with a small adapter while callers migrate.
3. Add characterization and parity tests before expanding to the next capability. Exercise the supported `Agent` boundary; inspect installed public APIs rather than copying remembered examples. Read [Tested Composition](references/TESTED-COMPOSITION.md) only when a local coding-agent composition is useful.
4. Read [Validation and Cutover](references/VALIDATION.md) before publishing examples or claiming cutover; for a simple slice, use only the applicable checks.
5. Cut over only when each in-scope contract is regression-tested, intentionally changed with agreement, or explicitly not applicable. Report anything else as unverified.
