# Atlassian

Let an agent use Jira, Confluence, and other Atlassian products across the sites it can access. `Atlassian` gives the agent every tool Atlassian's hosted MCP server offers, including tools that make changes. The credential you connect with decides what those tools can reach.

> While Pydantic AI Harness is on 0.x releases, the API may change between minor releases; when it does, deprecation warnings and release-note migration guidance tell you (or your agent) exactly how to upgrade. See the [version policy](index.md#version-policy).

## Install and connect

```bash
pip/uv-add "pydantic-ai-harness[atlassian]" "pydantic-ai-slim[openai]"
```

Set `ATLASSIAN_API_KEY` to an Atlassian service-account API key, or pass `auth=` a token or an `httpx.Auth`. With neither, the agent opens a browser so you can log in to Atlassian, which only works when you run it on your own machine. See the [provider setup](https://support.atlassian.com/atlassian-ai-gateway/docs/get-started-with-the-atlassian-remote-mcp-server/).

```python
from pydantic_ai import Agent
from pydantic_ai_harness.atlassian import Atlassian

agent = Agent('openai:gpt-5.6-sol', capabilities=[Atlassian()])
result = agent.run_sync('Summarize the resources I can access')
print(result.output)
```

## Per-user credentials

A fixed token or `httpx.Auth`, `ATLASSIAN_API_KEY`, and browser login all connect every run as the same account. When one agent serves several users, pass a function that returns the current user's credential instead:

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

The function is called at the start of each run, so each run connects as its own user. It can be async, and it can return a token or an `httpx.Auth`, such as `httpx.BasicAuth(email, token)` for a user's personal API token. If it returns `None`, that run has no Atlassian tools; it never falls back to `ATLASSIAN_API_KEY` or browser login.

Your application is responsible for getting each user's token, storing it, and refreshing it, for example with a "Connect Atlassian" OAuth flow in your web app. The function only reads the current token. Returning `'oauth'` from it raises an error, because browser login would open on the server rather than for the user.

`client` also accepts a function. It returns the MCP client or transport for the current run, or `None` for no Atlassian tools.

With durable execution such as Temporal, read the credential from the run's deps rather than from a global, since the function may run in another process. To add more than one `Atlassian` to an agent, give each a distinct `id` and wrap them in [PrefixTools](/ai/capabilities/prefix-tools/), since their tool names are the same.

## Provider settings

The capability connects to Atlassian's v2 MCP endpoint with every tool enabled. The user's existing permissions and the organization's settings decide which sites and products the agent can reach.

`ATLASSIAN_API_KEY` is for a service-account key. For a personal API token, pass `auth=httpx.BasicAuth(email, token)`. If you used browser login with Atlassian's older v1 endpoint, you need to log in again. See [token authentication](https://support.atlassian.com/atlassian-ai-gateway/docs/configure-authentication-via-api-token/).

## Tool selection and approval

To filter tools or require approval in your application, wrap the toolset with the existing [toolset wrappers](/ai/tools-toolsets/toolsets/). For example, this asks for approval before every tool call:

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

Handle the approval requests with the [deferred tools workflow](/ai/tools-toolsets/deferred-tools/). To cap the size of tool output, add [Tool Output Limits](tool-output-limits.md).

## Connection customization

Pass `client` to use your own FastMCP client or transport, for example one with custom OAuth token storage. The client then owns the URL, authentication, and server settings, so set those on it rather than on the capability. `include_instructions=False` stops the server's own instructions from reaching the agent.

A fixed `client` is one connection shared by every run; see [Per-user credentials](#per-user-credentials) to connect each user separately. To use two connections whose tool names overlap, give them distinct `id`s and add [PrefixTools](/ai/capabilities/prefix-tools/).

[Source](https://github.com/pydantic/pydantic-ai-harness/tree/main/pydantic_ai_harness/atlassian/)
