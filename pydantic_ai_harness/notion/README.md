# Notion

Let an agent search and change content in a Notion workspace. `Notion` gives the agent every tool Notion's hosted MCP server offers, including tools that make changes. The agent acts with the permissions of the Notion user it connects as.

> While Pydantic AI Harness is on 0.x releases, the API may change between minor releases; when it does, deprecation warnings and release-note migration guidance tell you (or your agent) exactly how to upgrade. See the [version policy](https://github.com/pydantic/pydantic-ai-harness#version-policy).

## Install and connect

uv:

```bash
uv add "pydantic-ai-harness[notion]" "pydantic-ai-slim[openai]"
```

pip:

```bash
pip install "pydantic-ai-harness[notion]" "pydantic-ai-slim[openai]"
```

Set `NOTION_ACCESS_TOKEN` to a Notion OAuth access token, or pass `auth=` a token or an `httpx.Auth`. See the [provider setup](https://developers.notion.com/guides/mcp/build-mcp-client).

```python
from pydantic_ai import Agent
from pydantic_ai_harness.notion import Notion

agent = Agent('openai:gpt-5.6-sol', capabilities=[Notion()])
result = agent.run_sync('Summarize the resources I can access')
print(result.output)
```

## Per-user credentials

A token or `NOTION_ACCESS_TOKEN` connects every run as the same account. When one agent serves several users, pass a function that returns the current user's credential instead:

```python
from dataclasses import dataclass

from pydantic_ai import Agent, RunContext
from pydantic_ai_harness.notion import Notion


@dataclass
class Deps:
    notion_token: str | None


def notion_token(ctx: RunContext[Deps]) -> str | None:
    return ctx.deps.notion_token


agent = Agent('openai:gpt-5.6-sol', deps_type=Deps, capabilities=[Notion(auth=notion_token)])
```

The function is called at the start of each run, so each run connects as its own user. It can be async, and it can return a Notion OAuth access token or an `httpx.Auth`. If it returns `None`, that run has no Notion tools; it never falls back to `NOTION_ACCESS_TOKEN`. `read_only` applies to every user.

Your application is responsible for getting each user's token, storing it, and refreshing it, for example with a "Connect Notion" button in your web app. The function only reads the current token.

`client` also accepts a function. It returns the FastMCP client or transport for the current run, or `None` for no Notion tools.

With durable execution such as Temporal, read the credential from the run's deps rather than from a global, since the function may run in another process. To add more than one `Notion` to an agent, give each a distinct `id` and wrap them in [PrefixTools](https://pydantic.dev/docs/ai/capabilities/prefix-tools/), since their tool names are the same.

## Provider settings

Connect with a Notion OAuth access token. Notion integration tokens are a different kind of credential and do not work with the hosted MCP server. Notion decides which pages and tools the user can reach. Some search and connected-source tools need a matching Notion plan and permissions.

## Tool selection and approval

`read_only=True` keeps only the tools the server marks as read-only. If the server does not mark its read tools, this can leave none. The credential is still what controls access.

To filter tools or require approval in your application, wrap the toolset with the existing [toolset wrappers](https://pydantic.dev/docs/ai/tools-toolsets/toolsets/). For example, this asks for approval before every tool call:

```python
from pydantic_ai import Agent
from pydantic_ai.messages import DeferredToolRequests
from pydantic_ai_harness.notion import Notion

capability = Notion()
agent = Agent(
    'openai:gpt-5.6-sol',
    toolsets=[capability.get_toolset().approval_required()],
    output_type=[str, DeferredToolRequests],
)
```

Handle the approval requests with the [deferred tools workflow](https://pydantic.dev/docs/ai/tools-toolsets/deferred-tools/). To cap the size of tool output, add [Tool Output Limits](https://pydantic.dev/docs/ai/harness/tool-output-limits/).

## Connection customization

Pass `client` to use your own FastMCP client or transport, for example one with custom authentication or MCP handlers. The client then owns the URL, authentication, and server settings, so set those on it rather than on the capability. `read_only` still applies. `include_instructions=False` stops the server's instructions from reaching the model.

A fixed `client` is one connection shared by every run; see [Per-user credentials](#per-user-credentials) to connect each user separately. To use two connections whose tool names overlap, give them distinct `id`s and add [PrefixTools](https://pydantic.dev/docs/ai/capabilities/prefix-tools/).

[Source](https://github.com/pydantic/pydantic-ai-harness/tree/main/pydantic_ai_harness/notion/)
