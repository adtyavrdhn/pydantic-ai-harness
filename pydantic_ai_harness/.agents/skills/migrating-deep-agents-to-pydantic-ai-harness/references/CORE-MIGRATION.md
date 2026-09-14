# Core Migration

Read this reference when the source depends on Deep Agents construction, middleware, tools, state, outputs, or tracing. Confirm behavior against the locked source and target versions.

Deep Agents is an opinionated harness over LangChain's agent loop and the LangGraph runtime. `create_deep_agent` assembles the final prompt, middleware, tools, state, and runtime configuration, so translating its arguments one by one does not preserve that contract.

| Deep Agents contract | Pydantic AI candidate | Required check |
| --- | --- | --- |
| `create_deep_agent` | `Agent(...)` with explicit tools, toolsets, capabilities, outputs, and settings | Snapshot the effective prompt, tool schemas, profile behavior, result, errors, and limits. |
| `context_schema` | `deps_type` and `RunContext` | Dependencies are runtime resources and identity, not checkpointed state. |
| custom `state_schema` and reducers | capability-owned run state, an application repository, or `pydantic_graph` | Classify each field's lifecycle and merge semantics. |
| custom middleware | a core capability, `Hooks`, a wrapper toolset, or a focused Harness capability | Match hook timing, ordering, mutation, retry, and failure behavior. |
| structured response | Pydantic `output_type` and an explicit output mode | Test invalid output, final-tool behavior, and streaming. |
| LangSmith callbacks | OpenTelemetry or Logfire plus application correlation | Preserve conversation, run, child, tool-call, and external-job identities separately. |

For plain chains, LCEL, or direct LangGraph code in a mixed project, use `$migrating-langchain-to-pydantic-ai` when it is available.

Primary sources: [Deep Agents architecture](https://github.com/langchain-ai/deepagents/blob/main/libs/ARCHITECTURE.md), [`create_deep_agent` source](https://github.com/langchain-ai/deepagents/blob/main/libs/deepagents/deepagents/graph.py), [Pydantic AI capabilities](https://pydantic.dev/docs/ai/capabilities/overview/), and [hooks](https://pydantic.dev/docs/ai/core-concepts/hooks/).
