# Stripe

Let an agent read and change your Stripe resources. `Stripe` gives the agent every tool Stripe's hosted MCP server offers, including tools that make changes. The credential you connect with decides what those tools can reach.

> While Pydantic AI Harness is on 0.x releases, the API may change between minor releases; when it does, deprecation warnings and release-note migration guidance tell you (or your agent) exactly how to upgrade. See the [version policy](index.md#version-policy).

## Install and connect

```bash
pip/uv-add "pydantic-ai-harness[stripe]" "pydantic-ai-slim[openai]"
```

Set `STRIPE_API_KEY` to a Stripe restricted API key, or pass `auth=` a key or an `httpx.Auth`. See the [provider setup](https://docs.stripe.com/mcp).

```python
from pydantic_ai import Agent
from pydantic_ai_harness.stripe import Stripe

agent = Agent('openai:gpt-5.6-sol', capabilities=[Stripe()])
result = agent.run_sync('Summarize the resources I can access')
print(result.output)
```

## Per-user credentials

An API key or `STRIPE_API_KEY` connects every run as the same account. When one agent serves several users, pass a function that returns the current user's credential instead:

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

The function is called at the start of each run, so each run connects as its own user. It can be async, and it can return a key, a token, or an `httpx.Auth`. If it returns `None`, that run has no Stripe tools; it never falls back to `STRIPE_API_KEY`. `connected_account` applies to every user.

Your application is responsible for getting each user's key or token, storing it, and refreshing it, for example with a "Connect Stripe" button in your web app. The function only reads the current credential.

`client` also accepts a function, for when users differ in more than their credential, such as a platform acting on each user's Connect account:

```python
from dataclasses import dataclass

from fastmcp.client.transports import StreamableHttpTransport
from pydantic_ai import RunContext
from pydantic_ai_harness.stripe import Stripe


@dataclass
class Deps:
    stripe_key: str | None
    stripe_account: str | None = None


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

With durable execution such as Temporal, read the credential from the run's deps rather than from a global, since the function may run in another process. To add more than one `Stripe` to an agent, give each a distinct `id` and wrap them in [PrefixTools](/ai/capabilities/prefix-tools/), since their tool names are the same.

## Provider settings

The credential decides whether the agent works in a sandbox or in live mode, and which resources it can change. Give a restricted key only the permissions the agent needs. To act on a Connect account, set `connected_account='acct_...'` and use your platform's restricted API key.

## Tool selection and approval

To filter tools or require approval in your application, wrap the toolset with the existing [toolset wrappers](/ai/tools-toolsets/toolsets/). For example, this asks for approval before every tool call:

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

Handle the approval requests with the [deferred tools workflow](/ai/tools-toolsets/deferred-tools/). To cap the size of tool output, add [Tool Output Limits](tool-output-limits.md).

## Connection customization

Pass `client` to use your own FastMCP client or transport, for example one with custom authentication or MCP handlers. The client then owns the URL, authentication, and server settings, including the Connect account, so set those on it rather than on the capability. `include_instructions=False` stops the server's instructions from reaching the model.

A fixed `client` is one connection shared by every run; see [Per-user credentials](#per-user-credentials) to connect each user separately. To use two connections whose tool names overlap, give them distinct `id`s and add [PrefixTools](/ai/capabilities/prefix-tools/).

[Source](https://github.com/pydantic/pydantic-ai-harness/tree/main/pydantic_ai_harness/stripe/)
