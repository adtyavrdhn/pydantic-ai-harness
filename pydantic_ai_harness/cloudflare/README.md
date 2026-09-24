# Cloudflare

Use Cloudflare API, product, and documentation tools. `Cloudflare` connects an agent to the provider's hosted MCP server. By default it exposes the tools the server offers, including write tools. Provider credentials and server settings determine what those tools may access.

> While Pydantic AI Harness is on 0.x releases, the API may change between minor releases; when it does, deprecation warnings and release-note migration guidance tell you (or your agent) exactly how to upgrade. See the [version policy](https://github.com/pydantic/pydantic-ai-harness#version-policy).

## Install and connect

uv:

```bash
uv add "pydantic-ai-harness[cloudflare]" "pydantic-ai-slim[openai]"
```

pip:

```bash
pip install "pydantic-ai-harness[cloudflare]" "pydantic-ai-slim[openai]"
```

Set `CLOUDFLARE_API_TOKEN` to a Cloudflare API token, or pass `auth=...`. `auth` accepts an `httpx.Auth` for caller-managed authentication, or a callable that returns a credential for each run (see [Per-user credentials](#per-user-credentials)). See the [provider setup](https://github.com/cloudflare/mcp).

```python
from pydantic_ai import Agent
from pydantic_ai_harness.cloudflare import Cloudflare

agent = Agent('openai:gpt-5.6-sol', capabilities=[Cloudflare()])
result = agent.run_sync('Summarize the resources I can access')
print(result.output)
```

## Per-user credentials

A fixed `auth`, `CLOUDFLARE_API_TOKEN`, and `'oauth'` all give every run the same connection and the same identity. Use them for scripts, local tools, and single-user agents. `'oauth'` opens a browser on the machine running the agent and keeps tokens in memory, so it does not suit a server.

When one agent serves several users, pass a callable instead. It receives the run context at the start of each run and returns that user's credential, so each run opens its own connection:

```python
from dataclasses import dataclass

from pydantic_ai import Agent, RunContext
from pydantic_ai_harness.cloudflare import Cloudflare, CloudflareServer


@dataclass
class Deps:
    cloudflare_token: str | None


def cloudflare_token(ctx: RunContext[Deps]) -> str | None:
    return ctx.deps.cloudflare_token


agent = Agent(
    'openai:gpt-5.6-sol',
    deps_type=Deps,
    capabilities=[Cloudflare(server=CloudflareServer.API, auth=cloudflare_token)],
)
```

The callable can be async and can return a token or an `httpx.Auth`. When it returns `None`, the run has no Cloudflare tools; it does not fall back to `CLOUDFLARE_API_TOKEN` or browser OAuth, and this also applies to the public documentation servers, which need no callable. Returning `'oauth'` raises an error, because it would open a browser on the server. Your application owns obtaining, storing, and refreshing each user's token, for example through an OAuth flow in your web app, and the callable reads the current token from that store.

`client` accepts a callable in the same way, returning a client or transport for each run. Cloudflare scopes accounts and permissions through the token, so `auth` covers most per-user setups.

Each run connects and lists tools when it starts, and disconnects when it ends. Under durable execution such as Temporal, the callable runs in the worker, so it should derive the credential from serializable deps. Give each `Cloudflare` on one agent a distinct `id`.

## Provider settings

`Cloudflare()` selects the public documentation server. Use `server=CloudflareServer.API` for the full API server, or another `CloudflareServer` member for a product server. Private servers use OAuth when no token is supplied. Configure account and resource permissions in OAuth or the API token.

The full API server currently marks its `docs`, `search`, and `execute` tools as not read-only, so `read_only=True` hides all three, including reads performed through `execute`. Use appropriately restricted credentials with the default tool selection for that server.

## Tool selection and approval

`read_only=True` keeps only tools explicitly marked `readOnlyHint: true`; unmarked tools are omitted. This can leave no tools when a server does not annotate its read operations. Credentials remain the access-control boundary.

For application-level filtering or approval, compose the existing [toolset wrappers](https://pydantic.dev/docs/ai/tools-toolsets/toolsets/). For example, this requires approval before every tool call:

```python
from pydantic_ai import Agent
from pydantic_ai.messages import DeferredToolRequests
from pydantic_ai_harness.cloudflare import Cloudflare

capability = Cloudflare()
agent = Agent(
    'openai:gpt-5.6-sol',
    toolsets=[capability.get_toolset().approval_required()],
    output_type=[str, DeferredToolRequests],
)
```

Handle the resulting requests using the [deferred tools workflow](https://pydantic.dev/docs/ai/tools-toolsets/deferred-tools/). Output limits can be composed with [Tool Output Limits](https://pydantic.dev/docs/ai/harness/tool-output-limits/).

## Connection customization

Pass `client` to use a configured FastMCP client or transport, including custom OAuth token storage and MCP handlers. That client owns its URL, authentication, and server configuration; configure those on it instead of the capability. `read_only=True` applies the same annotation filter to custom clients.

`include_instructions` controls whether server instructions reach the model. A fixed `client` is one connection shared by every run; see [Per-user credentials](#per-user-credentials) for per-run connections. To combine connections with overlapping tool names, give them distinct IDs and compose [PrefixTools](https://pydantic.dev/docs/ai/capabilities/prefix-tools/).

[Source](https://github.com/pydantic/pydantic-ai-harness/tree/main/pydantic_ai_harness/cloudflare/)
