# Supabase

Let an agent work with your Supabase projects and account. `Supabase` gives the agent the tools in Supabase's default feature groups, including tools that make changes; `features` picks other groups. The credential you connect with and the settings below decide what those tools can reach.

> While Pydantic AI Harness is on 0.x releases, the API may change between minor releases; when it does, deprecation warnings and release-note migration guidance tell you (or your agent) exactly how to upgrade. See the [version policy](index.md#version-policy).

## Install and connect

```bash
pip/uv-add "pydantic-ai-harness[supabase]" "pydantic-ai-slim[openai]"
```

Set `SUPABASE_ACCESS_TOKEN` to a Supabase personal access token, or pass `auth=` a token or an `httpx.Auth`. With neither, the agent opens a browser so you can log in to Supabase, which only works when you run it on your own machine. See the [provider setup](https://supabase.com/docs/guides/ai-tools/mcp).

```python
from pydantic_ai import Agent
from pydantic_ai_harness.supabase import Supabase

agent = Agent('openai:gpt-5.6-sol', capabilities=[Supabase(project_ref='your-project-ref')])
result = agent.run_sync('Summarize the resources I can access')
print(result.output)
```

## Per-user credentials

A token, `SUPABASE_ACCESS_TOKEN`, and browser login all connect every run as the same account. When one agent serves several users, pass a function that returns the current user's credential instead:

```python
from dataclasses import dataclass

from pydantic_ai import Agent, RunContext
from pydantic_ai_harness.supabase import Supabase


@dataclass
class Deps:
    supabase_token: str | None
    supabase_project_ref: str | None = None


def supabase_token(ctx: RunContext[Deps]) -> str | None:
    return ctx.deps.supabase_token


agent = Agent('openai:gpt-5.6-sol', deps_type=Deps, capabilities=[Supabase(auth=supabase_token)])
```

The function is called at the start of each run, so each run connects as its own user. It can be async, and it can return a token or an `httpx.Auth`. If it returns `None`, that run has no Supabase tools; it never falls back to `SUPABASE_ACCESS_TOKEN` or browser login. `project_ref`, `features`, and `read_only` apply to every user.

Your application is responsible for getting each user's token, storing it, and refreshing it, for example with a "Connect Supabase" OAuth flow in your web app. The function only reads the current token. Returning `'oauth'` from it raises an error, because browser login would open on the server rather than for the user.

`client` also accepts a function, for when users differ in more than their credential, such as each user working in their own project:

```python
from dataclasses import dataclass

from fastmcp.client.transports import StreamableHttpTransport
from pydantic_ai import RunContext
from pydantic_ai_harness.supabase import Supabase


@dataclass
class Deps:
    supabase_token: str | None
    supabase_project_ref: str | None = None


def supabase_client(ctx: RunContext[Deps]) -> StreamableHttpTransport | None:
    if ctx.deps.supabase_token is None or ctx.deps.supabase_project_ref is None:
        return None
    url = f'https://mcp.supabase.com/mcp?project_ref={ctx.deps.supabase_project_ref}'
    return StreamableHttpTransport(url, auth=ctx.deps.supabase_token)


capability = Supabase(client=supabase_client)
```

With durable execution such as Temporal, read the credential from the run's deps rather than from a global, since the function may run in another process. To add more than one `Supabase` to an agent, give each a distinct `id` and wrap them in [PrefixTools](/ai/capabilities/prefix-tools/), since their tool names are the same.

## Provider settings

`project_ref` limits the agent to one project. Leave it out to keep the account-level tools. `features` picks Supabase's tool groups, for example `features=['database', 'docs']`; leave it out for Supabase's defaults. `read_only=True` turns on Supabase's read-only mode, which also runs SQL as read-only. Follow Supabase's guidance on whether to connect to a development or a production project.

## Tool selection and approval

To filter tools or require approval in your application, wrap the toolset with the existing [toolset wrappers](/ai/tools-toolsets/toolsets/). For example, this asks for approval before every tool call:

```python
from pydantic_ai import Agent
from pydantic_ai.messages import DeferredToolRequests
from pydantic_ai_harness.supabase import Supabase

capability = Supabase(project_ref='your-project-ref')
agent = Agent(
    'openai:gpt-5.6-sol',
    toolsets=[capability.get_toolset().approval_required()],
    output_type=[str, DeferredToolRequests],
)
```

Handle the approval requests with the [deferred tools workflow](/ai/tools-toolsets/deferred-tools/). To cap the size of tool output, add [Tool Output Limits](tool-output-limits.md).

## Connection customization

Pass `client` to use your own FastMCP client or transport, for example one with custom OAuth token storage. The client then owns the URL, authentication, and server settings, so set those on it rather than on the capability. With a custom client, `read_only=True` keeps only the tools the server marks as read-only, instead of turning on Supabase's read-only mode. `include_instructions=False` stops the server's instructions from reaching the model.

A fixed `client` is one connection shared by every run; see [Per-user credentials](#per-user-credentials) to connect each user separately. To use two connections whose tool names overlap, give them distinct `id`s and add [PrefixTools](/ai/capabilities/prefix-tools/).

[Source](https://github.com/pydantic/pydantic-ai-harness/tree/main/pydantic_ai_harness/supabase/)
