# Stripe

Let an agent read and change your Stripe resources. `Stripe` gives the agent every tool Stripe's hosted MCP server offers, including tools that make changes. The credential you connect with decides what those tools can reach.

> While Pydantic AI Harness is on 0.x releases, the API may change between minor releases; when it does, deprecation warnings and release-note migration guidance tell you (or your agent) exactly how to upgrade. See the [version policy](https://github.com/pydantic/pydantic-ai-harness#version-policy).

## Install and connect

uv:

```bash
uv add "pydantic-ai-harness[stripe]" "pydantic-ai-slim[openai]"
```

pip:

```bash
pip install "pydantic-ai-harness[stripe]" "pydantic-ai-slim[openai]"
```

Set `STRIPE_API_KEY` to a Stripe restricted API key, or pass `auth=` a key. See the [provider setup](https://docs.stripe.com/mcp).

```python
from pydantic_ai import Agent
from pydantic_ai_harness.stripe import Stripe

agent = Agent('openai:gpt-5.6-sol', capabilities=[Stripe()])
result = agent.run_sync('Summarize the resources I can access')
print(result.output)
```

## Per-user credentials

`auth` decides which Stripe account each run uses:

| `auth` | Account used |
| --- | --- |
| Not set, `None`, or `''` | `STRIPE_API_KEY`. If that is not set either, creating the agent raises an error. |
| An API key | That key, for every run. |
| A function | Called at the start of each run. The key it returns is used for that run. If it returns `None` or `''`, that run has no Stripe tools. A function never uses `STRIPE_API_KEY`. |

A fixed key or `STRIPE_API_KEY` suits a script or an agent on your own machine, where every run is the same account.

In an app where each user connects their own Stripe account, one agent serves all of them, so the key cannot be fixed when the agent is created. Pass a function that reads the current user's key from the run's deps:

```python
from dataclasses import dataclass

from pydantic_ai import Agent, RunContext
from pydantic_ai_harness.stripe import Stripe


@dataclass
class Deps:
    stripe_key: str | None


def stripe_key(ctx: RunContext[Deps]) -> str | None:
    return ctx.deps.stripe_key


agent = Agent('openai:gpt-5.6-sol', deps_type=Deps, capabilities=[Stripe(auth=stripe_key)])
```

Each run connects as its own user, so concurrent runs never share an account. `connected_account` applies to every user.

Your app gets each user's key, stores it, and refreshes it. For example, a settings page where each user pastes their own restricted API key, or a "Connect Stripe" button that signs them in with OAuth and saves the token to their account. Before each run, load it (this can be async) and put it in the deps; the function only reads it.

When users differ in more than their credential, such as a platform acting on each user's Connect account, build the whole capability for each run with a [dynamic capability](https://pydantic.dev/docs/ai/capabilities/custom/#dynamically-building-a-capability):

```python
from dataclasses import dataclass

from pydantic_ai import Agent, RunContext
from pydantic_ai.capabilities import DynamicCapability
from pydantic_ai_harness.stripe import Stripe


@dataclass
class Deps:
    stripe_key: str | None
    stripe_account: str | None = None


def stripe(ctx: RunContext[Deps]) -> Stripe[Deps] | None:
    if ctx.deps.stripe_key is None or ctx.deps.stripe_account is None:
        return None
    return Stripe(auth=ctx.deps.stripe_key, connected_account=ctx.deps.stripe_account)


agent = Agent('openai:gpt-5.6-sol', deps_type=Deps, capabilities=[DynamicCapability(stripe, id='stripe')])
```

With durable execution such as Temporal, read the credential from the run's deps rather than from a global, since the function may run in another process. To add more than one `Stripe` to an agent, give each a distinct `id` and wrap them in [PrefixTools](https://pydantic.dev/docs/ai/capabilities/prefix-tools/), since their tool names are the same.

## Provider settings

The credential decides whether the agent works in a sandbox or in live mode, and which resources it can change. Give a restricted key only the permissions the agent needs. To act on a Connect account, set `connected_account='acct_...'` and use your platform's restricted API key.

## Tool selection and approval

To filter tools or require approval in your application, wrap the toolset with the existing [toolset wrappers](https://pydantic.dev/docs/ai/tools-toolsets/toolsets/). For example, this asks for approval before every tool call:

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

Handle the approval requests with the [deferred tools workflow](https://pydantic.dev/docs/ai/tools-toolsets/deferred-tools/). To cap the size of tool output, add [Tool Output Limits](https://pydantic.dev/docs/ai/harness/tool-output-limits/).

## Connection customization

Use `auth` in almost every case. Pass `client` only when you need control of the connection itself: your own FastMCP client or transport, for example one with a different authentication scheme, a proxy, or MCP handlers. The client then owns the URL and authentication, including the Connect account, so passing `client` together with `auth` or `connected_account` raises an error. `include_instructions=False` stops the server's instructions from reaching the model.

A `client` is one connection shared by every run; see [Per-user credentials](#per-user-credentials) to connect each user separately. To use two connections whose tool names overlap, give them distinct `id`s and add [PrefixTools](https://pydantic.dev/docs/ai/capabilities/prefix-tools/).

[Source](https://github.com/pydantic/pydantic-ai-harness/tree/main/pydantic_ai_harness/stripe/)
