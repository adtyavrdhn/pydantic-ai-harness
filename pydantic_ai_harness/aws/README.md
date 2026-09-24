# AWS

Use AWS knowledge and account tools through its managed MCP server. `AWS` connects an agent to the provider's hosted MCP server. By default it exposes the tools the server offers, including write tools. Provider credentials and server settings determine what those tools may access.

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

`auth` accepts an `httpx.Auth` for caller-managed authentication, or a callable that returns a credential for each run (see [Per-user credentials](#per-user-credentials)). See the [provider setup](https://docs.aws.amazon.com/agent-toolkit/latest/userguide/getting-started-aws-mcp-server.html).

```python
from pydantic_ai import Agent
from pydantic_ai_harness.aws import AWS

agent = Agent('openai:gpt-5.6-sol', capabilities=[AWS()])
result = agent.run_sync('Summarize the resources I can access')
print(result.output)
```

## Per-user credentials

A fixed `auth` and `'oauth'` both give every run the same connection and the same identity. Use them for scripts, local tools, and single-user agents. `'oauth'` opens a browser on the machine running the agent and keeps tokens in memory, so it does not suit a server.

When one agent serves several users, pass a callable instead. It receives the run context at the start of each run and returns that user's credential, so each run opens its own connection:

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

The callable can be async and can return an access token or an `httpx.Auth`, such as one that signs each request with SigV4 using that user's AWS credentials. When it returns `None`, the run has no AWS tools; it does not fall back to browser OAuth. Returning `'oauth'` raises an error, because it would open a browser on the server. Your application owns obtaining, storing, and refreshing each user's credentials, for example through an OAuth flow in your web app, and the callable reads the current credential from that store.

`client` accepts a callable in the same way, for settings beyond the credential that differ per user, such as a user who should connect through the Frankfurt endpoint:

```python
from fastmcp.client.transports import StreamableHttpTransport
from pydantic_ai import RunContext
from pydantic_ai_harness.aws import AWS


def aws_client(ctx: RunContext[Deps]) -> StreamableHttpTransport | None:
    if ctx.deps.aws_token is None:
        return None
    return StreamableHttpTransport(f'https://aws-mcp.{ctx.deps.aws_region}.api.aws/mcp', auth=ctx.deps.aws_token)


capability = AWS(client=aws_client)
```

Each run connects and lists tools when it starts, and disconnects when it ends. Under durable execution such as Temporal, the callable runs in the worker, so it should derive the credential from serializable deps. Give each `AWS` on one agent a distinct `id`.

## Provider settings

`AWS()` uses browser OAuth with the Virginia endpoint. `region='eu-central-1'` selects the Frankfurt MCP endpoint; it does not constrain the regions used by tool calls. Existing IAM permissions determine access. OAuth requires the AWS sign-in permissions described in the [OAuth guide](https://docs.aws.amazon.com/agent-toolkit/latest/userguide/oauth-authentication.html).

For SigV4 or a named AWS profile, configure the [official AWS MCP proxy](https://github.com/aws/mcp-proxy-for-aws) and pass its MCP transport through `client`. The proxy owns credential discovery and signing.

## Tool selection and approval

`read_only=True` keeps only tools explicitly marked `readOnlyHint: true`; unmarked tools are omitted. This can leave no tools when a server does not annotate its read operations. Credentials remain the access-control boundary.

For application-level filtering or approval, compose the existing [toolset wrappers](https://pydantic.dev/docs/ai/tools-toolsets/toolsets/). For example, this requires approval before every tool call:

```python
from pydantic_ai import Agent
from pydantic_ai.messages import DeferredToolRequests
from pydantic_ai_harness.aws import AWS

capability = AWS()
agent = Agent(
    'openai:gpt-5.6-sol',
    toolsets=[capability.get_toolset().approval_required()],
    output_type=[str, DeferredToolRequests],
)
```

Handle the resulting requests using the [deferred tools workflow](https://pydantic.dev/docs/ai/tools-toolsets/deferred-tools/). Output limits can be composed with [Tool Output Limits](https://pydantic.dev/docs/ai/harness/tool-output-limits/).

## Connection customization

Pass `client` to use a configured FastMCP client or transport, including custom OAuth token storage and MCP handlers. That client owns its URL, authentication, and server configuration; configure those on it instead of the capability. `read_only=True` applies the same annotation filter to custom clients.

`include_instructions` controls whether server instructions reach the model. A fixed `client` is one connection shared by every run; see [Per-user credentials](#per-user-credentials) for per-run connections. To combine connections with overlapping tool names, give them distinct IDs and compose [PrefixTools](https://pydantic.dev/docs/ai/capabilities/prefix-tools/).

[Source](https://github.com/pydantic/pydantic-ai-harness/tree/main/pydantic_ai_harness/aws/)
