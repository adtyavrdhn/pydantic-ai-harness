# Atlassian

Use Jira, Confluence, and other Atlassian tools across your accessible sites. `Atlassian` connects an agent to the provider's hosted MCP server. By default it exposes the tools the server offers, including write tools. Provider credentials and server settings determine what those tools may access.

> While Pydantic AI Harness is on 0.x releases, the API may change between minor releases; when it does, deprecation warnings and release-note migration guidance tell you (or your agent) exactly how to upgrade. See the [version policy](https://github.com/pydantic/pydantic-ai-harness#version-policy).

## Install and connect

uv:

```bash
uv add "pydantic-ai-harness[atlassian]" "pydantic-ai-slim[openai]"
```

pip:

```bash
pip install "pydantic-ai-harness[atlassian]" "pydantic-ai-slim[openai]"
```

Set `ATLASSIAN_API_KEY` to an Atlassian service-account API key, or pass `auth=...`. When neither is supplied, the connection starts browser OAuth. `auth` accepts an `httpx.Auth` for caller-managed authentication, or a callable that returns a credential for each run (see [Per-user credentials](#per-user-credentials)). See the [provider setup](https://support.atlassian.com/atlassian-ai-gateway/docs/get-started-with-the-atlassian-remote-mcp-server/).

```python
from pydantic_ai import Agent
from pydantic_ai_harness.atlassian import Atlassian

agent = Agent('openai:gpt-5.6-sol', capabilities=[Atlassian()])
result = agent.run_sync('Summarize the resources I can access')
print(result.output)
```

## Per-user credentials

A fixed `auth`, `ATLASSIAN_API_KEY`, and `'oauth'` all give every run the same connection and the same identity. Use them for scripts, local tools, and single-user agents. `'oauth'` opens a browser on the machine running the agent and keeps tokens in memory, so it does not suit a server.

When one agent serves several users, pass a callable instead. It receives the run context at the start of each run and returns that user's credential, so each run opens its own connection:

```python
from dataclasses import dataclass

from pydantic_ai import Agent, RunContext
from pydantic_ai_harness.atlassian import Atlassian


@dataclass
class Deps:
    atlassian_token: str | None


def atlassian_token(ctx: RunContext[Deps]) -> str | None:
    return ctx.deps.atlassian_token


agent = Agent('openai:gpt-5.6-sol', deps_type=Deps, capabilities=[Atlassian(auth=atlassian_token)])
```

The callable can be async and can return a token or an `httpx.Auth`, such as `httpx.BasicAuth(email, token)` for a user's personal API token. When it returns `None`, the run has no Atlassian tools; it does not fall back to `ATLASSIAN_API_KEY` or browser OAuth. Returning `'oauth'` raises an error, because it would open a browser on the server. Your application owns obtaining, storing, and refreshing each user's token, for example through an OAuth flow in your web app, and the callable reads the current token from that store.

`client` accepts a callable in the same way, returning the MCP client or transport for each run, or `None` to omit the tools.

Each run connects and lists tools when it starts, and disconnects when it ends. Under durable execution such as Temporal, the callable runs in the worker, so it should derive the credential from serializable deps. Give each `Atlassian` on one agent a distinct `id`.

## Provider settings

The connection uses `https://mcp.atlassian.com/v2/mcp?tools=all`, Atlassian's flat tool catalog. Existing user permissions and organization settings determine access to sites and products. A personal API token uses `auth=httpx.BasicAuth(email, token)`; the environment variable is for a service-account bearer key. V2 OAuth requires a new sign-in when migrating from V1. See [token authentication](https://support.atlassian.com/atlassian-ai-gateway/docs/configure-authentication-via-api-token/).

## Tool selection and approval

For application-level filtering or approval, compose the existing [toolset wrappers](https://pydantic.dev/docs/ai/tools-toolsets/toolsets/). For example, this requires approval before every tool call:

```python
from pydantic_ai import Agent
from pydantic_ai.messages import DeferredToolRequests
from pydantic_ai_harness.atlassian import Atlassian

capability = Atlassian()
agent = Agent(
    'openai:gpt-5.6-sol',
    toolsets=[capability.get_toolset().approval_required()],
    output_type=[str, DeferredToolRequests],
)
```

Handle the resulting requests using the [deferred tools workflow](https://pydantic.dev/docs/ai/tools-toolsets/deferred-tools/). Output limits can be composed with [Tool Output Limits](https://pydantic.dev/docs/ai/harness/tool-output-limits/).

## Connection customization

Pass `client` to use a configured FastMCP client or transport, including custom OAuth token storage and MCP handlers. That client owns its URL, authentication, and server configuration; configure those on it instead of the capability.

`include_instructions` controls whether server instructions reach the model. A fixed `client` is one connection shared by every run; see [Per-user credentials](#per-user-credentials) for per-run connections. To combine connections with overlapping tool names, give them distinct IDs and compose [PrefixTools](https://pydantic.dev/docs/ai/capabilities/prefix-tools/).

[Source](https://github.com/pydantic/pydantic-ai-harness/tree/main/pydantic_ai_harness/atlassian/)
