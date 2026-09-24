# Supabase

Let an agent work with your Supabase projects and account. `Supabase` gives the agent the tools in Supabase's default feature groups, including tools that make changes; `features` picks other groups. The credential you connect with and the settings below decide what those tools can reach.

> While Pydantic AI Harness is on 0.x releases, the API may change between minor releases; when it does, deprecation warnings and release-note migration guidance tell you (or your agent) exactly how to upgrade. See the [version policy](https://github.com/pydantic/pydantic-ai-harness#version-policy).

## Install and connect

uv:

```bash
uv add "pydantic-ai-harness[supabase]" "pydantic-ai-slim[openai]"
```

pip:

```bash
pip install "pydantic-ai-harness[supabase]" "pydantic-ai-slim[openai]"
```

Set `SUPABASE_ACCESS_TOKEN` to a Supabase personal access token, or pass `auth=` a token. See the [provider setup](https://supabase.com/docs/guides/ai-tools/mcp).

```python
from pydantic_ai import Agent
from pydantic_ai_harness.supabase import Supabase

agent = Agent('openai:gpt-5.6-sol', capabilities=[Supabase(project_ref='your-project-ref')])
result = agent.run_sync('Summarize the resources I can access')
print(result.output)
```

## Per-user credentials

`auth` decides which Supabase account each run uses:

| `auth` | Account used |
| --- | --- |
| Not set, `None`, or `''` | `SUPABASE_ACCESS_TOKEN`. If that is not set either, creating the agent raises an error. |
| An access token | That token, for every run. |
| A function | Called at the start of each run. The token it returns is used for that run. If it returns `None` or `''`, that run has no Supabase tools. A function never uses `SUPABASE_ACCESS_TOKEN`. |

A fixed token or `SUPABASE_ACCESS_TOKEN` suits a script or an agent on your own machine, where every run is the same account.

In an app where each user connects their own Supabase account, one agent serves all of them, so the token cannot be fixed when the agent is created. Pass a function that reads the current user's token from the run's deps:

```python
from dataclasses import dataclass

from pydantic_ai import Agent, RunContext
from pydantic_ai_harness.supabase import Supabase


@dataclass
class Deps:
    supabase_token: str | None


def supabase_token(ctx: RunContext[Deps]) -> str | None:
    return ctx.deps.supabase_token


agent = Agent('openai:gpt-5.6-sol', deps_type=Deps, capabilities=[Supabase(auth=supabase_token)])
```

Each run connects as its own user, so concurrent runs never share an account. `project_ref`, `features`, and `read_only` apply to every user.

Your app gets each user's token, stores it, and refreshes it. For example, a settings page where each user pastes their own Supabase personal access token, or a "Connect Supabase" button that signs them in through your Supabase OAuth app and saves the token to their account. Before each run, load it (this can be async) and put it in the deps; the function only reads it.

When users differ in more than their credential, such as each user working in their own project, build the whole capability for each run with a [dynamic capability](https://pydantic.dev/docs/ai/capabilities/custom/#dynamically-building-a-capability):

```python
from dataclasses import dataclass

from pydantic_ai import Agent, RunContext
from pydantic_ai.capabilities import DynamicCapability
from pydantic_ai_harness.supabase import Supabase


@dataclass
class Deps:
    supabase_token: str | None
    supabase_project_ref: str | None = None


def supabase(ctx: RunContext[Deps]) -> Supabase[Deps] | None:
    if ctx.deps.supabase_token is None or ctx.deps.supabase_project_ref is None:
        return None
    return Supabase(auth=ctx.deps.supabase_token, project_ref=ctx.deps.supabase_project_ref)


agent = Agent('openai:gpt-5.6-sol', deps_type=Deps, capabilities=[DynamicCapability(supabase, id='supabase')])
```

With durable execution such as Temporal, read the credential from the run's deps rather than from a global, since the function may run in another process. To add more than one `Supabase` to an agent, give each a distinct `id` and wrap them in [PrefixTools](https://pydantic.dev/docs/ai/capabilities/prefix-tools/), since their tool names are the same.

## Provider settings

`project_ref` limits the agent to one project. Leave it out to keep the account-level tools. `features` picks Supabase's tool groups, for example `features=['database', 'docs']`; leave it out for Supabase's defaults. `read_only=True` turns on Supabase's read-only mode, which also runs SQL as read-only. Follow Supabase's guidance on whether to connect to a development or a production project.

## Tool selection and approval

To filter tools or require approval in your application, wrap the toolset with the existing [toolset wrappers](https://pydantic.dev/docs/ai/tools-toolsets/toolsets/). For example, this asks for approval before every tool call:

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

Handle the approval requests with the [deferred tools workflow](https://pydantic.dev/docs/ai/tools-toolsets/deferred-tools/). To cap the size of tool output, add [Tool Output Limits](https://pydantic.dev/docs/ai/harness/tool-output-limits/).

## Connection customization

Pass `client` to use your own FastMCP client or transport, for example one with custom authentication or MCP handlers. The client then owns the URL, authentication, and server settings, so set those on it rather than on the capability. With a custom client, `read_only=True` keeps only the tools the server marks as read-only, instead of turning on Supabase's read-only mode. `include_instructions=False` stops the server's instructions from reaching the model.

A `client` is one connection shared by every run; see [Per-user credentials](#per-user-credentials) to connect each user separately. To use two connections whose tool names overlap, give them distinct `id`s and add [PrefixTools](https://pydantic.dev/docs/ai/capabilities/prefix-tools/).

[Source](https://github.com/pydantic/pydantic-ai-harness/tree/main/pydantic_ai_harness/supabase/)
