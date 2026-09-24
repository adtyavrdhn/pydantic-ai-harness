# AWS

Let an agent use AWS knowledge and account tools. `AWS` gives the agent every tool AWS's managed MCP server offers, including tools that make changes. Your IAM permissions decide what those tools can reach.

> While Pydantic AI Harness is on 0.x releases, the API may change between minor releases; when it does, deprecation warnings and release-note migration guidance tell you (or your agent) exactly how to upgrade. See the [version policy](https://github.com/pydantic/pydantic-ai-harness#version-policy).

## Install and connect

uv:

```bash
uv add "pydantic-ai-harness[aws]" "pydantic-ai-slim[openai]"
```

pip:

```bash
pip install "pydantic-ai-harness[aws]" "pydantic-ai-slim[openai]"
```

Pass `auth=` an access token or an `httpx.Auth`, or pass `client` an MCP transport, such as the [AWS MCP proxy](#provider-settings). See the [provider setup](https://docs.aws.amazon.com/agent-toolkit/latest/userguide/getting-started-aws-mcp-server.html).

```python
from pydantic_ai import Agent
from pydantic_ai_harness.aws import AWS

agent = Agent('openai:gpt-5.6-sol', capabilities=[AWS(auth='your-access-token')])
result = agent.run_sync('Summarize the resources I can access')
print(result.output)
```

## Per-user credentials

An access token connects every run as the same account. When one agent serves several users, pass a function that returns the current user's credential instead:

```python
from dataclasses import dataclass
from typing import Literal

from pydantic_ai import Agent, RunContext
from pydantic_ai_harness.aws import AWS


@dataclass
class Deps:
    aws_token: str | None
    aws_region: Literal['us-east-1', 'eu-central-1'] = 'us-east-1'


def aws_token(ctx: RunContext[Deps]) -> str | None:
    return ctx.deps.aws_token


agent = Agent('openai:gpt-5.6-sol', deps_type=Deps, capabilities=[AWS(auth=aws_token)])
```

The function is called at the start of each run, so each run connects as its own user. It can return an access token or an `httpx.Auth`, such as one that signs each request with SigV4 using that user's AWS credentials. If it returns `None`, that run has no AWS tools.

Your application is responsible for getting each user's credentials, storing them, and refreshing them, for example with a "Connect AWS" button in your web app. Look the credential up before the run, for example with `await`, and put it in the deps; the function only reads it.

When users differ in more than their credential, such as a user who should connect through the Frankfurt endpoint, build the whole capability for each run with a [dynamic capability](https://pydantic.dev/docs/ai/capabilities/custom/#dynamically-building-a-capability):

```python
from dataclasses import dataclass
from typing import Literal

from pydantic_ai import Agent, RunContext
from pydantic_ai.capabilities import DynamicCapability
from pydantic_ai_harness.aws import AWS


@dataclass
class Deps:
    aws_token: str | None
    aws_region: Literal['us-east-1', 'eu-central-1'] = 'us-east-1'


def aws(ctx: RunContext[Deps]) -> AWS[Deps] | None:
    if ctx.deps.aws_token is None:
        return None
    return AWS(auth=ctx.deps.aws_token, region=ctx.deps.aws_region)


agent = Agent('openai:gpt-5.6-sol', deps_type=Deps, capabilities=[DynamicCapability(aws, id='aws')])
```

With durable execution such as Temporal, read the credential from the run's deps rather than from a global, since the function may run in another process. To add more than one `AWS` to an agent, give each a distinct `id` and wrap them in [PrefixTools](https://pydantic.dev/docs/ai/capabilities/prefix-tools/), since their tool names are the same.

## Provider settings

`AWS()` connects to the Virginia endpoint. `region='eu-central-1'` connects to the Frankfurt endpoint instead. It does not limit which regions the tools can act on. Your IAM permissions decide what is allowed.

To use SigV4 or a named AWS profile, set up the [official AWS MCP proxy](https://github.com/aws/mcp-proxy-for-aws) and pass its MCP transport as `client`. The proxy finds your AWS credentials and signs requests.

## Tool selection and approval

`read_only=True` keeps only the tools the server marks as read-only. If the server does not mark its read tools, this can leave none. The credential is still what controls access.

To filter tools or require approval in your application, wrap the toolset with the existing [toolset wrappers](https://pydantic.dev/docs/ai/tools-toolsets/toolsets/). For example, this asks for approval before every tool call:

```python
from pydantic_ai import Agent
from pydantic_ai.messages import DeferredToolRequests
from pydantic_ai_harness.aws import AWS

capability = AWS(auth='your-access-token')
agent = Agent(
    'openai:gpt-5.6-sol',
    toolsets=[capability.get_toolset().approval_required()],
    output_type=[str, DeferredToolRequests],
)
```

Handle the approval requests with the [deferred tools workflow](https://pydantic.dev/docs/ai/tools-toolsets/deferred-tools/). To cap the size of tool output, add [Tool Output Limits](https://pydantic.dev/docs/ai/harness/tool-output-limits/).

## Connection customization

Pass `client` to use your own FastMCP client or transport, for example one with custom authentication or MCP handlers. The client then owns the URL, authentication, and server settings, so set those on it rather than on the capability. `read_only` still applies. `include_instructions=False` stops the server's instructions from reaching the model.

A `client` is one connection shared by every run; see [Per-user credentials](#per-user-credentials) to connect each user separately. To use two connections whose tool names overlap, give them distinct `id`s and add [PrefixTools](https://pydantic.dev/docs/ai/capabilities/prefix-tools/).

[Source](https://github.com/pydantic/pydantic-ai-harness/tree/main/pydantic_ai_harness/aws/)
