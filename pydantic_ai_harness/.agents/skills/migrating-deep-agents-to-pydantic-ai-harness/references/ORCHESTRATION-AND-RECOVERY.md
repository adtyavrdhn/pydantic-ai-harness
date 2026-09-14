# Orchestration and Recovery

Read only when the source uses subagents, background work, approvals, checkpoints, durable execution, or streaming.

| Deep Agents feature | Pydantic AI and Harness candidate | Important difference |
| --- | --- | --- |
| synchronous named children | `SubAgents` | Match child history, tools, dependencies, model selection, budgets, errors, events, and result serialization. Mutable parent capability state is not transferred implicitly; use explicit dependencies or state transfer, `shared_capabilities`, or an application repository, then test child write visibility. |
| model-written fan-out or chaining | `DynamicWorkflow` | This is a redesign, not an API port: Deep Agents generates JavaScript for QuickJS and calls `task(...)`; Harness generates Python for Monty and calls child agents from `run_workflow`. Verify structured results, interpreter and thread state, nesting, and tool boundaries. Neither is a durable background task service. |
| approval or `interrupt_on` | approval-required or deferred tools | The source resumes through LangGraph `Command(resume=...)`; Harness does not reproduce that protocol. Persist and reauthorize pending requests, then continue with Pydantic AI deferred results and message history. |
| provider-valid run snapshots | `StepPersistence` | It records step events, continuable snapshots, lineage, and tool effects; it is not arbitrary LangGraph graph-state checkpointing. |
| durable model and tool execution | a Pydantic AI durable-execution integration | Match crash and redeploy recovery, replay, timers, signals, and side-effect idempotency in the selected runtime. |
| LangGraph event streaming | Pydantic AI streaming APIs plus Harness event handlers | Translate events into the application's versioned schema; namespaced graph streams are not preserved automatically. |

## Explicit designs for gaps

- **Forked child context or compiled LangGraph children:** pass selected context explicitly, rebuild the child with Pydantic AI primitives, or retain it behind a typed tool boundary during migration.
- **Background subagents:** keep the queue and worker in the host. Give the agent narrow start, list, inspect, steer, and cancel tools with tenant checks, idempotency keys, bounded retries, and durable results.
- **Approvals:** test approve, deny, changed arguments, stale decisions, authorization changes, crash before and after execution, replay, and side-effect idempotency.
- **Checkpoints:** separate message continuation, capability state, plans, files, approvals, domain state, and side effects. Do not replace a LangGraph checkpointer keyed by `thread_id` with `StepPersistence` without designing thread identity, `message_history` continuation, and ownership of the remaining state. Use `StepPersistence` only for its documented snapshot contract.
- **Streaming:** verify lineage, ordering, backpressure, redaction, child and tool events, error termination, and final-output timing.

Primary sources: [Deep Agents subagents](https://docs.langchain.com/oss/python/deepagents/subagents), [human-in-the-loop](https://docs.langchain.com/oss/python/deepagents/human-in-the-loop), [event streaming](https://docs.langchain.com/oss/python/deepagents/event-streaming), [Pydantic AI deferred tools](https://pydantic.dev/docs/ai/tools-toolsets/deferred-tools/), and [durable execution](https://pydantic.dev/docs/ai/capabilities/durable_execution/overview/).
