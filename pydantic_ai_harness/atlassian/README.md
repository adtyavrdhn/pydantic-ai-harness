# Atlassian

Let an agent use Jira, Confluence, and other Atlassian products across the sites it can access. `Atlassian` gives the agent every tool Atlassian's hosted MCP server offers, including tools that make changes. The credential you connect with decides what those tools can reach.

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

Set `ATLASSIAN_API_KEY` to an Atlassian service-account API key, or pass `auth=` a token or an `httpx.Auth`. See the [provider setup](https://support.atlassian.com/atlassian-ai-gateway/docs/get-started-with-the-atlassian-remote-mcp-server/).

```python
from pydantic_ai import Agent
from pydantic_ai_harness.atlassian import Atlassian

agent = Agent('openai:gpt-5.6-sol', capabilities=[Atlassian()])
result = agent.run_sync('Summarize the resources I can access')
print(result.output)
```

## Per-user credentials

An API key or `ATLASSIAN_API_KEY` connects every run as the same account. When one agent serves several users, pass a function that returns the current user's credential instead:

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

The function is called at the start of each run, so each run connects as its own user. It can be async, and it can return a token or an `httpx.Auth`, such as `httpx.BasicAuth(email, token)` for a user's personal API token. If it returns `None`, that run has no Atlassian tools; it never falls back to `ATLASSIAN_API_KEY`.

Your application is responsible for getting each user's token, storing it, and refreshing it, for example with a "Connect Atlassian" button in your web app. The function only reads the current token.

With durable execution such as Temporal, read the credential from the run's deps rather than from a global, since the function may run in another process. To add more than one `Atlassian` to an agent, give each a distinct `id` and wrap them in [PrefixTools](https://pydantic.dev/docs/ai/capabilities/prefix-tools/), since their tool names are the same.

## Provider settings

The capability connects to Atlassian's v2 MCP endpoint with every tool enabled. The user's existing permissions and the organization's settings decide which sites and products the agent can reach.

`ATLASSIAN_API_KEY` is for a service-account key. For a personal API token, pass `auth=httpx.BasicAuth(email, token)`. See [token authentication](https://support.atlassian.com/atlassian-ai-gateway/docs/configure-authentication-via-api-token/).

## Tool selection and approval

To filter tools or require approval in your application, wrap the toolset with the existing [toolset wrappers](https://pydantic.dev/docs/ai/tools-toolsets/toolsets/). For example, this asks for approval before every tool call:

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

Handle the approval requests with the [deferred tools workflow](https://pydantic.dev/docs/ai/tools-toolsets/deferred-tools/). To cap the size of tool output, add [Tool Output Limits](https://pydantic.dev/docs/ai/harness/tool-output-limits/).

## Connection customization

Pass `client` to use your own FastMCP client or transport, for example one with custom authentication or MCP handlers. The client then owns the URL, authentication, and server settings, so set those on it rather than on the capability. `include_instructions=False` stops the server's own instructions from reaching the agent.

A `client` is one connection shared by every run; see [Per-user credentials](#per-user-credentials) to connect each user separately. To use two connections whose tool names overlap, give them distinct `id`s and add [PrefixTools](https://pydantic.dev/docs/ai/capabilities/prefix-tools/).

[Source](https://github.com/pydantic/pydantic-ai-harness/tree/main/pydantic_ai_harness/atlassian/)
