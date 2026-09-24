# Cloudflare

Let an agent use Cloudflare's API, product, and documentation tools. `Cloudflare` gives the agent every tool the chosen Cloudflare MCP server offers, including tools that make changes. The credential you connect with decides what those tools can reach.

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

Set `CLOUDFLARE_API_TOKEN` to a Cloudflare API token, or pass `auth=` a token. On your own machine, `auth='oauth'` signs you in through the browser instead. See the [provider setup](https://github.com/cloudflare/mcp).

```python
from pydantic_ai import Agent
from pydantic_ai_harness.cloudflare import Cloudflare

agent = Agent('openai:gpt-5.6-sol', capabilities=[Cloudflare()])
result = agent.run_sync('Summarize the resources I can access')
print(result.output)
```

## Per-user credentials

`auth` decides which Cloudflare account each run uses:

| `auth` | Account used |
| --- | --- |
| Not set, `None`, or `''` | `CLOUDFLARE_API_TOKEN`. If that is not set either, public servers such as the documentation server connect without a credential, and any other server raises an error when the agent is created. |
| A token | That token, for every run. |
| `'oauth'` | The account you sign in to through the browser. This only works on your own machine. |
| A function | Called at the start of each run. The token it returns is used for that run. If it returns `None` or `''`, that run has no Cloudflare tools, even on a public server. A function never uses `CLOUDFLARE_API_TOKEN`, and must not return `'oauth'`. |

A fixed token or `CLOUDFLARE_API_TOKEN` suits a script or an agent on your own machine, where every run is the same account.

In an app where each user connects their own Cloudflare account, one agent serves all of them, so the token cannot be fixed when the agent is created. Pass a function that reads the current user's token from the run's deps:

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

Each run connects as its own user, so concurrent runs never share an account.

Your app gets each user's token, stores it, and refreshes it. For example, a settings page where each user pastes a Cloudflare API token they created with only the permissions they want the agent to have. Before each run, load it (this can be async) and put it in the deps; the function only reads it.

With durable execution such as Temporal, read the credential from the run's deps rather than from a global, since the function may run in another process. To add more than one `Cloudflare` to an agent, give each a distinct `id` and wrap them in [PrefixTools](https://pydantic.dev/docs/ai/capabilities/prefix-tools/), since their tool names are the same.

## Provider settings

`Cloudflare()` uses the public documentation server, which needs no credential. Use `server=CloudflareServer.API` for the full API server, or another `CloudflareServer` member for a product server. A server that is not public needs a token. Set account and resource permissions on the API token.

The full API server currently does not mark its `docs`, `search`, and `execute` tools as read-only. So `read_only=True` hides all three, including reads done through `execute`. For that server, keep the default tools and use a token with limited permissions.

## Tool selection and approval

`read_only=True` keeps only the tools the server marks as read-only. If the server does not mark its read tools, this can leave none. The credential is still what controls access.

To filter tools or require approval in your application, wrap the toolset with the existing [toolset wrappers](https://pydantic.dev/docs/ai/tools-toolsets/toolsets/). For example, this asks for approval before every tool call:

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

Handle the approval requests with the [deferred tools workflow](https://pydantic.dev/docs/ai/tools-toolsets/deferred-tools/). To cap the size of tool output, add [Tool Output Limits](https://pydantic.dev/docs/ai/harness/tool-output-limits/).

## Connection customization

Use `auth` in almost every case. Pass `client` only when you need control of the connection itself: your own FastMCP client or transport, for example one with a different authentication scheme, a proxy, or MCP handlers. The client then owns the URL and authentication, so passing `client` together with `auth` or `server` raises an error. `read_only` still applies. `include_instructions=False` stops the server's instructions from reaching the model.

A `client` is one connection shared by every run; see [Per-user credentials](#per-user-credentials) to connect each user separately. To use two connections whose tool names overlap, give them distinct `id`s and add [PrefixTools](https://pydantic.dev/docs/ai/capabilities/prefix-tools/).

[Source](https://github.com/pydantic/pydantic-ai-harness/tree/main/pydantic_ai_harness/cloudflare/)
