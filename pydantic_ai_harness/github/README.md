# GitHub

Read and change GitHub repositories, issues, pull requests, and other accessible resources. `GitHub` connects an agent to the provider's hosted MCP server. By default it exposes the tools the server offers, including write tools. Provider credentials and server settings determine what those tools may access.

> While Pydantic AI Harness is on 0.x releases, the API may change between minor releases; when it does, deprecation warnings and release-note migration guidance tell you (or your agent) exactly how to upgrade. See the [version policy](https://github.com/pydantic/pydantic-ai-harness#version-policy).

## Install and connect

uv:

```bash
uv add "pydantic-ai-harness[github]" "pydantic-ai-slim[openai]"
```

pip:

```bash
pip install "pydantic-ai-harness[github]" "pydantic-ai-slim[openai]"
```

Set `GITHUB_TOKEN` to a GitHub personal access token, or pass `auth=...`. `auth` accepts an `httpx.Auth` for caller-managed authentication, or a callable that returns a credential for each run (see [Per-user credentials](#per-user-credentials)). See the [provider setup](https://github.com/github/github-mcp-server/blob/main/docs/remote-server.md).

```python
from pydantic_ai import Agent
from pydantic_ai_harness.github import GitHub

agent = Agent('openai:gpt-5.6-sol', capabilities=[GitHub()])
result = agent.run_sync('Summarize the resources I can access')
print(result.output)
```

## Per-user credentials

A fixed `auth` and `GITHUB_TOKEN` give every run the same connection and the same identity. Use them for scripts, local tools, and single-user agents.

When one agent serves several users, pass a callable instead. It receives the run context at the start of each run and returns that user's credential, so each run opens its own connection:

```python
from dataclasses import dataclass

from pydantic_ai import Agent, RunContext
from pydantic_ai_harness.github import GitHub


@dataclass
class Deps:
    github_token: str | None
    github_mcp_url: str | None = None


def github_token(ctx: RunContext[Deps]) -> str | None:
    return ctx.deps.github_token


agent = Agent('openai:gpt-5.6-sol', deps_type=Deps, capabilities=[GitHub(auth=github_token)])
```

The callable can be async and can return a token or an `httpx.Auth`. When it returns `None`, the run has no GitHub tools; it does not fall back to `GITHUB_TOKEN`. Returning `'oauth'` raises an error, because it would open a browser on the server. Your application owns obtaining, storing, and refreshing each user's token, for example through a GitHub App user authorization flow in your web app, and the callable reads the current token from that store. `read_only` and `toolsets` apply to every run's connection.

`client` accepts a callable in the same way, for settings beyond the credential that differ per user, such as a user whose organization is on a GitHub Enterprise Cloud data-residency endpoint:

```python
from fastmcp.client.transports import StreamableHttpTransport
from pydantic_ai import RunContext
from pydantic_ai_harness.github import GITHUB_MCP_URL, GitHub


def github_client(ctx: RunContext[Deps]) -> StreamableHttpTransport | None:
    if ctx.deps.github_token is None:
        return None
    return StreamableHttpTransport(ctx.deps.github_mcp_url or GITHUB_MCP_URL, auth=ctx.deps.github_token)


capability = GitHub(client=github_client)
```

Each run connects and lists tools when it starts, and disconnects when it ends. Under durable execution such as Temporal, the callable runs in the worker, so it should derive the credential from serializable deps. Give each `GitHub` on one agent a distinct `id`.

## Provider settings

`toolsets=['repos', 'issues', 'actions']` sends GitHub's native `X-MCP-Toolsets` header. Omit it to keep server defaults. `read_only=True` sends `X-MCP-Readonly: true`. `url` can select a GitHub Enterprise Cloud endpoint. Configure repository access through the token or GitHub App permissions; this capability does not interpret search syntax or enforce a repository boundary. For additional headers or host-configured OAuth, pass a configured MCP client.

## Tool selection and approval

For application-level filtering or approval, compose the existing [toolset wrappers](https://pydantic.dev/docs/ai/tools-toolsets/toolsets/). For example, this requires approval before every tool call:

```python
from pydantic_ai import Agent
from pydantic_ai.messages import DeferredToolRequests
from pydantic_ai_harness.github import GitHub

capability = GitHub()
agent = Agent(
    'openai:gpt-5.6-sol',
    toolsets=[capability.get_toolset().approval_required()],
    output_type=[str, DeferredToolRequests],
)
```

Handle the resulting requests using the [deferred tools workflow](https://pydantic.dev/docs/ai/tools-toolsets/deferred-tools/). Output limits can be composed with [Tool Output Limits](https://pydantic.dev/docs/ai/harness/tool-output-limits/).

## Connection customization

Pass `client` to use a configured FastMCP client or transport, including custom OAuth token storage and MCP handlers. That client owns its URL, authentication, and server configuration; configure those on it instead of the capability. With a custom client, `read_only=True` filters annotations rather than configuring the remote server.

`include_instructions` controls whether server instructions reach the model. A fixed `client` is one connection shared by every run; see [Per-user credentials](#per-user-credentials) for per-run connections. To combine connections with overlapping tool names, give them distinct IDs and compose [PrefixTools](https://pydantic.dev/docs/ai/capabilities/prefix-tools/).

[Source](https://github.com/pydantic/pydantic-ai-harness/tree/main/pydantic_ai_harness/github/)
