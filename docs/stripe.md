# Stripe

Read and change Stripe resources through its hosted MCP tools. `Stripe` connects an agent to the provider's hosted MCP server. By default it exposes the tools the server offers, including write tools. Provider credentials and server settings determine what those tools may access.

> While Pydantic AI Harness is on 0.x releases, the API may change between minor releases; when it does, deprecation warnings and release-note migration guidance tell you (or your agent) exactly how to upgrade. See the [version policy](index.md#version-policy).

## Install and connect

```bash
pip/uv-add "pydantic-ai-harness[stripe]" "pydantic-ai-slim[openai]"
```

Set `STRIPE_API_KEY` to a Stripe restricted API key, or pass `auth=...`. When neither is supplied, the connection starts browser OAuth. `auth` accepts an `httpx.Auth` for caller-managed authentication, or a callable that returns a credential for each run (see [Per-user credentials](#per-user-credentials)). See the [provider setup](https://docs.stripe.com/mcp).

```python
from pydantic_ai import Agent
from pydantic_ai_harness.stripe import Stripe

agent = Agent('openai:gpt-5.6-sol', capabilities=[Stripe()])
result = agent.run_sync('Summarize the resources I can access')
print(result.output)
```

## Per-user credentials

A fixed `auth`, `STRIPE_API_KEY`, and `'oauth'` all give every run the same connection and the same identity. Use them for scripts, local tools, and single-user agents. `'oauth'` opens a browser on the machine running the agent and keeps tokens in memory, so it does not suit a server.

When one agent serves several users, pass a callable instead. It receives the run context at the start of each run and returns that user's credential, so each run opens its own connection:

```python
from dataclasses import dataclass

from pydantic_ai import Agent, RunContext
from pydantic_ai_harness.stripe import Stripe


@dataclass
class Deps:
    stripe_key: str | None
    stripe_account: str | None = None


def stripe_key(ctx: RunContext[Deps]) -> str | None:
    return ctx.deps.stripe_key


agent = Agent('openai:gpt-5.6-sol', deps_type=Deps, capabilities=[Stripe(auth=stripe_key)])
```

The callable can be async and can return a token or an `httpx.Auth`. When it returns `None`, the run has no Stripe tools; it does not fall back to `STRIPE_API_KEY` or browser OAuth. Returning `'oauth'` raises an error, because it would open a browser on the server. Your application owns obtaining, storing, and refreshing each user's key or token, for example through an OAuth flow in your web app, and the callable reads the current one from that store.

`client` accepts a callable in the same way, for settings beyond the credential that differ per user, such as a platform that acts on each user's Connect account:

```python
from fastmcp.client.transports import StreamableHttpTransport
from pydantic_ai import RunContext
from pydantic_ai_harness.stripe import Stripe


def stripe_client(ctx: RunContext[Deps]) -> StreamableHttpTransport | None:
    if ctx.deps.stripe_key is None or ctx.deps.stripe_account is None:
        return None
    return StreamableHttpTransport(
        'https://mcp.stripe.com',
        auth=ctx.deps.stripe_key,
        headers={'Stripe-Account': ctx.deps.stripe_account},
    )


capability = Stripe(client=stripe_client)
```

Each run connects and lists tools when it starts, and disconnects when it ends. Under durable execution such as Temporal, the callable runs in the worker, so it should derive the credential from serializable deps. Give each `Stripe` on one agent a distinct `id`.

## Provider settings

The credential determines sandbox or live mode and which resources can be changed. For a Connect account, set `connected_account='acct_...'` with a platform restricted API key; Stripe receives the native `Stripe-Account` header. Connected-account access does not support OAuth. Grant the restricted key only the resource permissions the agent needs.

## Tool selection and approval

For application-level filtering or approval, compose the existing [toolset wrappers](/ai/tools-toolsets/toolsets/). For example, this requires approval before every tool call:

```python
from pydantic_ai import Agent
from pydantic_ai.messages import DeferredToolRequests
from pydantic_ai_harness.stripe import Stripe

capability = Stripe()
agent = Agent(
    'openai:gpt-5.6-sol',
    toolsets=[capability.get_toolset().approval_required()],
    output_type=[str, DeferredToolRequests],
)
```

Handle the resulting requests using the [deferred tools workflow](/ai/tools-toolsets/deferred-tools/). Output limits can be composed with [Tool Output Limits](tool-output-limits.md).

## Connection customization

Pass `client` to use a configured FastMCP client or transport, including custom OAuth token storage and MCP handlers. That client owns its URL, authentication, and server configuration; configure those on it instead of the capability.

`include_instructions` controls whether server instructions reach the model. A fixed `client` is one connection shared by every run; see [Per-user credentials](#per-user-credentials) for per-run connections. To combine connections with overlapping tool names, give them distinct IDs and compose [PrefixTools](/ai/capabilities/prefix-tools/).

[Source](https://github.com/pydantic/pydantic-ai-harness/tree/main/pydantic_ai_harness/stripe/)
