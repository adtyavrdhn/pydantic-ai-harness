# Supabase

Use Supabase project and account tools. `Supabase` connects an agent to the provider's hosted MCP server. By default it exposes the tools the server offers, including write tools. Provider credentials and server settings determine what those tools may access.

> While Pydantic AI Harness is on 0.x releases, the API may change between minor releases; when it does, deprecation warnings and release-note migration guidance tell you (or your agent) exactly how to upgrade. See the [version policy](index.md#version-policy).

## Install and connect

```bash
pip/uv-add "pydantic-ai-harness[supabase]" "pydantic-ai-slim[openai]"
```

Set `SUPABASE_ACCESS_TOKEN` to a Supabase personal access token, or pass `auth=...`. When neither is supplied, the connection starts browser OAuth. `auth` accepts an `httpx.Auth` for caller-managed authentication, or a callable that returns a credential for each run (see [Per-user credentials](#per-user-credentials)). See the [provider setup](https://supabase.com/docs/guides/ai-tools/mcp).

```python
from pydantic_ai import Agent
from pydantic_ai_harness.supabase import Supabase

agent = Agent('openai:gpt-5.6-sol', capabilities=[Supabase(project_ref='your-project-ref')])
result = agent.run_sync('Summarize the resources I can access')
print(result.output)
```

## Per-user credentials

A fixed `auth`, `SUPABASE_ACCESS_TOKEN`, and `'oauth'` all give every run the same connection and the same identity. Use them for scripts, local tools, and single-user agents. `'oauth'` opens a browser on the machine running the agent and keeps tokens in memory, so it does not suit a server.

When one agent serves several users, pass a callable instead. It receives the run context at the start of each run and returns that user's credential, so each run opens its own connection:

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

The callable can be async and can return a token or an `httpx.Auth`. When it returns `None`, the run has no Supabase tools; it does not fall back to `SUPABASE_ACCESS_TOKEN` or browser OAuth. Returning `'oauth'` raises an error, because it would open a browser on the server. Your application owns obtaining, storing, and refreshing each user's token, for example through an OAuth flow in your web app, and the callable reads the current token from that store. `project_ref`, `features`, and `read_only` apply to every run's connection.

`client` accepts a callable in the same way, for settings beyond the credential that differ per user, such as each user's own Supabase project:

```python
from fastmcp.client.transports import StreamableHttpTransport
from pydantic_ai import RunContext
from pydantic_ai_harness.supabase import Supabase


def supabase_client(ctx: RunContext[Deps]) -> StreamableHttpTransport | None:
    if ctx.deps.supabase_token is None or ctx.deps.supabase_project_ref is None:
        return None
    url = f'https://mcp.supabase.com/mcp?project_ref={ctx.deps.supabase_project_ref}'
    return StreamableHttpTransport(url, auth=ctx.deps.supabase_token)


capability = Supabase(client=supabase_client)
```

Each run connects and lists tools when it starts, and disconnects when it ends. Under durable execution such as Temporal, the callable runs in the worker, so it should derive the credential from serializable deps. Give each `Supabase` on one agent a distinct `id`.

## Provider settings

`project_ref` selects a project through Supabase's native URL parameter; omit it to retain account-level tools. `features=['database', 'docs']` selects native feature groups; omitting it keeps server defaults. `read_only=True` sends `read_only=true`, including Supabase's read-only SQL execution mode. These settings belong to Supabase and need no local tool catalog. Follow Supabase's current guidance when selecting a development or production project.

## Tool selection and approval

For application-level filtering or approval, compose the existing [toolset wrappers](/ai/tools-toolsets/toolsets/). For example, this requires approval before every tool call:

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

Handle the resulting requests using the [deferred tools workflow](/ai/tools-toolsets/deferred-tools/). Output limits can be composed with [Tool Output Limits](tool-output-limits.md).

## Connection customization

Pass `client` to use a configured FastMCP client or transport, including custom OAuth token storage and MCP handlers. That client owns its URL, authentication, and server configuration; configure those on it instead of the capability. With a custom client, `read_only=True` filters annotations rather than configuring the remote server.

`include_instructions` controls whether server instructions reach the model. A fixed `client` is one connection shared by every run; see [Per-user credentials](#per-user-credentials) for per-run connections. To combine connections with overlapping tool names, give them distinct IDs and compose [PrefixTools](/ai/capabilities/prefix-tools/).

[Source](https://github.com/pydantic/pydantic-ai-harness/tree/main/pydantic_ai_harness/supabase/)
