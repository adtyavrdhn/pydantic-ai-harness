# Notion

Search and change Notion workspace content. `Notion` connects an agent to the provider's hosted MCP server. By default it exposes the tools the server offers, including write tools. Provider credentials and server settings determine what those tools may access.

> While Pydantic AI Harness is on 0.x releases, the API may change between minor releases; when it does, deprecation warnings and release-note migration guidance tell you (or your agent) exactly how to upgrade. See the [version policy](index.md#version-policy).

## Install and connect

```bash
pip/uv-add "pydantic-ai-harness[notion]" "pydantic-ai-slim[openai]"
```

Set `NOTION_ACCESS_TOKEN` to a Notion OAuth access token, or pass `auth=...`. When neither is supplied, the connection starts browser OAuth with PKCE as a public client. `auth` accepts an `httpx.Auth` for caller-managed authentication, or a callable that returns a credential for each run (see [Per-user credentials](#per-user-credentials)). See the [provider setup](https://developers.notion.com/guides/mcp/build-mcp-client).

```python
from pydantic_ai import Agent
from pydantic_ai_harness.notion import Notion

agent = Agent('openai:gpt-5.6-sol', capabilities=[Notion()])
result = agent.run_sync('Summarize the resources I can access')
print(result.output)
```

## Per-user credentials

A fixed `auth`, `NOTION_ACCESS_TOKEN`, and `'oauth'` all give every run the same connection and the same identity. Use them for scripts, local tools, and single-user agents. `'oauth'` opens a browser on the machine running the agent and keeps tokens in memory, so it does not suit a server.

When one agent serves several users, pass a callable instead. It receives the run context at the start of each run and returns that user's credential, so each run opens its own connection:

```python
from dataclasses import dataclass

from pydantic_ai import Agent, RunContext
from pydantic_ai_harness.notion import Notion


@dataclass
class Deps:
    notion_token: str | None


def notion_token(ctx: RunContext[Deps]) -> str | None:
    return ctx.deps.notion_token


agent = Agent('openai:gpt-5.6-sol', deps_type=Deps, capabilities=[Notion(auth=notion_token)])
```

The callable can be async and can return a Notion OAuth access token or an `httpx.Auth`. When it returns `None`, the run has no Notion tools; it does not fall back to `NOTION_ACCESS_TOKEN` or browser OAuth. Returning `'oauth'` raises an error, because it would open a browser on the server. Your application owns obtaining, storing, and refreshing each user's token, for example through an OAuth flow in your web app, and the callable reads the current token from that store. `read_only=True` still filters each run's tools.

`client` accepts a callable in the same way, returning the MCP client or transport for each run, or `None` to omit the tools.

Each run connects and lists tools when it starts, and disconnects when it ends. Under durable execution such as Temporal, the callable runs in the worker, so it should derive the credential from serializable deps. Give each `Notion` on one agent a distinct `id`.

## Provider settings

Use browser OAuth or a Notion OAuth access token. Notion integration tokens are a different credential and do not authenticate the hosted MCP server. Notion owns workspace access, tool availability, and MCP session state. Some search and connected-source tools require the corresponding Notion plan and permissions.

## Tool selection and approval

`read_only=True` keeps only tools explicitly marked `readOnlyHint: true`; unmarked tools are omitted. This can leave no tools when a server does not annotate its read operations. Credentials remain the access-control boundary.

For application-level filtering or approval, compose the existing [toolset wrappers](/ai/tools-toolsets/toolsets/). For example, this requires approval before every tool call:

```python
from pydantic_ai import Agent
from pydantic_ai.messages import DeferredToolRequests
from pydantic_ai_harness.notion import Notion

capability = Notion()
agent = Agent(
    'openai:gpt-5.6-sol',
    toolsets=[capability.get_toolset().approval_required()],
    output_type=[str, DeferredToolRequests],
)
```

Handle the resulting requests using the [deferred tools workflow](/ai/tools-toolsets/deferred-tools/). Output limits can be composed with [Tool Output Limits](tool-output-limits.md).

## Connection customization

Pass `client` to use a configured FastMCP client or transport, including custom OAuth token storage and MCP handlers. That client owns its URL, authentication, and server configuration; configure those on it instead of the capability. `read_only=True` applies the same annotation filter to custom clients.

`include_instructions` controls whether server instructions reach the model. A fixed `client` is one connection shared by every run; see [Per-user credentials](#per-user-credentials) for per-run connections. To combine connections with overlapping tool names, give them distinct IDs and compose [PrefixTools](/ai/capabilities/prefix-tools/).

[Source](https://github.com/pydantic/pydantic-ai-harness/tree/main/pydantic_ai_harness/notion/)
