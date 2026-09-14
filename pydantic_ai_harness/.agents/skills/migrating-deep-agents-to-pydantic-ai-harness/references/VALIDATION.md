# Validation and Cutover

Use this reference when the source has state, streaming, approvals, sandboxed execution, subagents, persistence, or external side effects.

## Contract ledger

For each observed source contract, record:

- source owner and locked version;
- source behavior and evidence;
- target owner: core, Harness, adapter, application service, or retained source;
- outcome: `verified-equivalent`, `verified-adapter`, `intentional-change`, `external-owner`, `not-applicable`, `unverified`, or `blocked`;
- focused test and residual risk.

Do not mark a mapping equivalent from documentation or a matching class name alone.

## Focused checks

- **Prompt and tools:** compare effective instructions, ordering, tool names, descriptions, JSON schemas, hidden context, retries, errors, timeouts, approvals, and provider-native tools.
- **State and memory:** restart the process and test namespace isolation, retention, concurrent writes, deletion, corrupt data, and whether the next run observes a write.
- **Subagents:** test context isolation, explicit state transfer, model and tool selection, parent and child budgets, timeout, cancellation, partial failure, result shape, and streamed identity.
- **Plans:** test create or replace semantics, status transitions, persistence, concurrent writers, visibility on the next request, and UI events.
- **Files and sandboxes:** test traversal, absolute paths, symlinks, permissions, media and binary data, output bounds, environment leakage, timeout, reconnect, egress, and cleanup failure.
- **Approvals:** test approve, deny, changed arguments, stale decisions, authorization changes, crash before and after execution, replay, and side-effect idempotency.
- **Persistence:** test continuation from every promised stop point. Determine whether the source retains complete tool-call history or only selected user and final messages before choosing Pydantic AI `message_history`. Separate message snapshots from graph state, pending work, files, plans, and tool effects.
- **Streaming:** compare event types, lineage, order, redaction, backpressure, child and tool events, error termination, and when the final result becomes visible.

Use `TestModel(call_tools=[])` for construction-only checks. When a test claims tool behavior, use a `FunctionModel` callback that returns scripted `ModelResponse` tool calls, or `TestModel(call_tools=[...])` for simple coverage. Exercise public `Agent(..., capabilities=[...])` paths where possible, and probe the locked external runtime for contracts that deterministic models cannot establish.

## Example policy

Every example added during the migration must be executable in the target repository or name the exact executable test that backs it. Linting alone proves only syntax. Do not publish model output, network calls, sandbox code, shell commands, or partial fragments as examples unless a focused test exercises the claimed behavior. Prefer fewer complete examples over a catalog of unverified snippets.

## Cutover

Keep the old path reversible until representative contract tests and evaluations pass. Remove Deep Agents, LangChain, or LangGraph dependencies only after no retained path, adapter, deployment tool, or test needs them. Report which behavior was exercised against a real backend or runtime, which used a deterministic substitute, and every remaining unverified contract.
