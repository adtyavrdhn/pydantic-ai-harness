# Cloudflare

Let an agent use Cloudflare's API, product, and documentation tools. `Cloudflare` gives the agent every tool the chosen Cloudflare MCP server offers, including tools that make changes. The credential you connect with decides what those tools can reach.

> While Pydantic AI Harness is on 0.x releases, the API may change between minor releases; when it does, deprecation warnings and release-note migration guidance tell you (or your agent) exactly how to upgrade. See the [version policy](index.md#version-policy).

## Install and connect

```bash
pip/uv-add "pydantic-ai-harness[cloudflare]" "pydantic-ai-slim[openai]"
```

Set `CLOUDFLARE_API_TOKEN` to a Cloudflare API token, or pass `auth=` a token or an `httpx.Auth`. See the [provider setup](https://github.com/cloudflare/mcp).

```python
from pydantic_ai import Agent
from pydantic_ai_harness.cloudflare import Cloudflare

agent = Agent('openai:gpt-5.6-sol', capabilities=[Cloudflare()])
result = agent.run_sync('Summarize the resources I can access')
print(result.output)
```

## Per-user credentials

A token or `CLOUDFLARE_API_TOKEN` connects every run as the same account. When one agent serves several users, pass a function that returns the current user's credential instead:

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

The function is called at the start of each run, so each run connects as its own user. It can be async, and it can return a token or an `httpx.Auth`. If it returns `None`, that run has no Cloudflare tools, even on a public documentation server; it never falls back to `CLOUDFLARE_API_TOKEN`.

Your application is responsible for getting each user's token, storing it, and refreshing it, for example with a "Connect Cloudflare" button in your web app. The function only reads the current token.

`client` also accepts a function that returns a client or transport for each run. Cloudflare sets a user's accounts and permissions through the token, so `auth` covers most per-user setups.

With durable execution such as Temporal, read the credential from the run's deps rather than from a global, since the function may run in another process. To add more than one `Cloudflare` to an agent, give each a distinct `id` and wrap them in [PrefixTools](/ai/capabilities/prefix-tools/), since their tool names are the same.

## Provider settings

`Cloudflare()` uses the public documentation server, which needs no credential. Use `server=CloudflareServer.API` for the full API server, or another `CloudflareServer` member for a product server. A server that is not public needs a token. Set account and resource permissions on the API token.

The full API server currently does not mark its `docs`, `search`, and `execute` tools as read-only. So `read_only=True` hides all three, including reads done through `execute`. For that server, keep the default tools and use a token with limited permissions.

## Tool selection and approval

`read_only=True` keeps only the tools the server marks as read-only. If the server does not mark its read tools, this can leave none. The credential is still what controls access.

To filter tools or require approval in your application, wrap the toolset with the existing [toolset wrappers](/ai/tools-toolsets/toolsets/). For example, this asks for approval before every tool call:

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

Handle the approval requests with the [deferred tools workflow](/ai/tools-toolsets/deferred-tools/). To cap the size of tool output, add [Tool Output Limits](tool-output-limits.md).

## Connection customization

Pass `client` to use your own FastMCP client or transport, for example one with custom authentication or MCP handlers. The client then owns the URL, authentication, and server settings, so set those on it rather than on the capability. `read_only` still applies. `include_instructions=False` stops the server's instructions from reaching the model.

A fixed `client` is one connection shared by every run; see [Per-user credentials](#per-user-credentials) to connect each user separately. To use two connections whose tool names overlap, give them distinct `id`s and add [PrefixTools](/ai/capabilities/prefix-tools/).

[Source](https://github.com/pydantic/pydantic-ai-harness/tree/main/pydantic_ai_harness/cloudflare/)
