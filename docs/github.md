# GitHub

Let an agent read and change GitHub repositories, issues, pull requests, and other resources. `GitHub` gives the agent the tools in GitHub's default tool groups, including tools that make changes; `toolsets` picks other groups. The credential you connect with decides what those tools can reach.

> While Pydantic AI Harness is on 0.x releases, the API may change between minor releases; when it does, deprecation warnings and release-note migration guidance tell you (or your agent) exactly how to upgrade. See the [version policy](index.md#version-policy).

## Install and connect

```bash
pip/uv-add "pydantic-ai-harness[github]" "pydantic-ai-slim[openai]"
```

Set `GITHUB_TOKEN` to a GitHub personal access token, or pass `auth=` a token or an `httpx.Auth`. See the [provider setup](https://github.com/github/github-mcp-server/blob/main/docs/remote-server.md).

```python
from pydantic_ai import Agent
from pydantic_ai_harness.github import GitHub

agent = Agent('openai:gpt-5.6-sol', capabilities=[GitHub()])
result = agent.run_sync('Summarize the resources I can access')
print(result.output)
```

## Per-user credentials

A token or `GITHUB_TOKEN` connects every run as the same account. When one agent serves several users, pass a function that returns the current user's credential instead:

```python
from dataclasses import dataclass

from pydantic_ai import Agent, RunContext
from pydantic_ai_harness.github import GitHub


@dataclass
class Deps:
    github_token: str | None


def github_token(ctx: RunContext[Deps]) -> str | None:
    return ctx.deps.github_token


agent = Agent('openai:gpt-5.6-sol', deps_type=Deps, capabilities=[GitHub(auth=github_token)])
```

The function is called at the start of each run, so each run connects as its own user. It can return a token or an `httpx.Auth`. If it returns `None`, that run has no GitHub tools; it never falls back to `GITHUB_TOKEN`. `read_only`, `toolsets`, and `url` still apply to every run.

Your application is responsible for getting each user's token, storing it, and refreshing it, for example with a "Connect GitHub" button in your web app. Look the token up before the run, for example with `await`, and put it in the deps; the function only reads it.

When users differ in more than their credential, such as a user whose organization is on a GitHub Enterprise Cloud data-residency endpoint, build the whole capability for each run with a [dynamic capability](/ai/capabilities/custom/#dynamically-building-a-capability):

```python
from dataclasses import dataclass

from pydantic_ai import Agent, RunContext
from pydantic_ai.capabilities import DynamicCapability
from pydantic_ai_harness.github import GITHUB_MCP_URL, GitHub


@dataclass
class Deps:
    github_token: str | None
    github_mcp_url: str | None = None


def github(ctx: RunContext[Deps]) -> GitHub[Deps] | None:
    if ctx.deps.github_token is None:
        return None
    return GitHub(auth=ctx.deps.github_token, url=ctx.deps.github_mcp_url or GITHUB_MCP_URL)


agent = Agent('openai:gpt-5.6-sol', deps_type=Deps, capabilities=[DynamicCapability(github, id='github')])
```

With durable execution such as Temporal, read the credential from the run's deps rather than from a global, since the function may run in another process. To add more than one `GitHub` to an agent, give each a distinct `id` and wrap them in [PrefixTools](/ai/capabilities/prefix-tools/), since their tool names are the same.

## Provider settings

`toolsets=['repos', 'issues', 'actions']` picks which of GitHub's tool groups the server offers. Leave it unset to get the server's default groups. `read_only=True` asks the server for its read-only mode. Set `url` to use a GitHub Enterprise Cloud endpoint.

The capability does not limit which repositories the agent can reach. Set that with the token's or GitHub App's permissions. To send other headers, or to use custom authentication, pass a configured `client`.

## Tool selection and approval

To filter tools or require approval in your application, wrap the toolset with the existing [toolset wrappers](/ai/tools-toolsets/toolsets/). For example, this asks for approval before every tool call:

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

Handle the approval requests with the [deferred tools workflow](/ai/tools-toolsets/deferred-tools/). To cap the size of tool output, add [Tool Output Limits](tool-output-limits.md).

## Connection customization

Pass `client` to use your own FastMCP client or transport, for example one with custom authentication or MCP handlers. The client then owns the URL, authentication, and server settings, so set those on it rather than on the capability. `toolsets` does not apply. `read_only=True` keeps only the tools the server marks as read-only, instead of asking the server for read-only mode. `include_instructions=False` stops the server's instructions from reaching the model.

A `client` is one connection shared by every run; see [Per-user credentials](#per-user-credentials) to connect each user separately. To use two connections whose tool names overlap, give them distinct `id`s and add [PrefixTools](/ai/capabilities/prefix-tools/).

[Source](https://github.com/pydantic/pydantic-ai-harness/tree/main/pydantic_ai_harness/github/)
