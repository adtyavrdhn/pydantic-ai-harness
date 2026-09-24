# Linear

Let an agent read and change Linear issues, projects, teams, and comments. `Linear` gives the agent every tool Linear's hosted MCP server offers, including tools that make changes. The credential you connect with decides what those tools can reach.

> While Pydantic AI Harness is on 0.x releases, the API may change between minor releases; when it does, deprecation warnings and release-note migration guidance tell you (or your agent) exactly how to upgrade. See the [version policy](index.md#version-policy).

## Install and connect

```bash
pip/uv-add "pydantic-ai-harness[linear]" "pydantic-ai-slim[openai]"
```

Set `LINEAR_ACCESS_TOKEN` to a Linear API key or OAuth access token, or pass `auth=` a token or an `httpx.Auth`. See the [provider setup](https://linear.app/docs/mcp).

```python
from pydantic_ai import Agent
from pydantic_ai_harness.linear import Linear

agent = Agent('openai:gpt-5.6-sol', capabilities=[Linear()])
result = agent.run_sync('Summarize the resources I can access')
print(result.output)
```

## Per-user credentials

An API key or `LINEAR_ACCESS_TOKEN` connects every run as the same account. When one agent serves several users, pass a function that returns the current user's credential instead:

```python
from dataclasses import dataclass

from pydantic_ai import Agent, RunContext
from pydantic_ai_harness.linear import Linear


@dataclass
class Deps:
    linear_token: str | None


def linear_token(ctx: RunContext[Deps]) -> str | None:
    return ctx.deps.linear_token


agent = Agent('openai:gpt-5.6-sol', deps_type=Deps, capabilities=[Linear(auth=linear_token)])
```

The function is called at the start of each run, so each run connects as its own user. It can return a token or an `httpx.Auth`. If it returns `None`, that run has no Linear tools; it never falls back to `LINEAR_ACCESS_TOKEN`. `read_only=True` still applies to every run.

Your application is responsible for getting each user's token, storing it, and refreshing it, for example with a "Connect Linear" button in your web app. Look the token up before the run, for example with `await`, and put it in the deps; the function only reads it.

With durable execution such as Temporal, read the credential from the run's deps rather than from a global, since the function may run in another process. To add more than one `Linear` to an agent, give each a distinct `id` and wrap them in [PrefixTools](/ai/capabilities/prefix-tools/), since their tool names are the same.

## Provider settings

`read_only=True` connects to Linear's read-only endpoint instead of the full one. OAuth token scopes can limit access further.

## Tool selection and approval

To filter tools or require approval in your application, wrap the toolset with the existing [toolset wrappers](/ai/tools-toolsets/toolsets/). For example, this asks for approval before every tool call:

```python
from pydantic_ai import Agent
from pydantic_ai.messages import DeferredToolRequests
from pydantic_ai_harness.linear import Linear

capability = Linear()
agent = Agent(
    'openai:gpt-5.6-sol',
    toolsets=[capability.get_toolset().approval_required()],
    output_type=[str, DeferredToolRequests],
)
```

Handle the approval requests with the [deferred tools workflow](/ai/tools-toolsets/deferred-tools/). To cap the size of tool output, add [Tool Output Limits](tool-output-limits.md).

## Connection customization

Pass `client` to use your own FastMCP client or transport, for example one with custom authentication or MCP handlers. The client then owns the URL, authentication, and server settings, so set those on it rather than on the capability. With a `client`, `read_only=True` keeps only the tools the server marks as read-only. `include_instructions=False` stops the server's own instructions from reaching the agent.

A `client` is one connection shared by every run; see [Per-user credentials](#per-user-credentials) to connect each user separately. To use two connections whose tool names overlap, give them distinct `id`s and add [PrefixTools](/ai/capabilities/prefix-tools/).

[Source](https://github.com/pydantic/pydantic-ai-harness/tree/main/pydantic_ai_harness/linear/)
