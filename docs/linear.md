---
title: Linear
description: Let a Pydantic AI agent read or update Linear work through Linear's hosted MCP server.
---

# Linear

Use `Linear` when an agent needs to read or update issues, projects, and teams through Linear's hosted MCP server. The
default connection uses Linear's server-enforced read-only endpoint.

[Source](https://github.com/pydantic/pydantic-ai-harness/tree/main/pydantic_ai_harness/linear/)

> While Pydantic AI Harness is on 0.x releases, the API may change between minor releases; when it does, deprecation warnings and release-note migration guidance tell you (or your agent) exactly how to upgrade. See the [version policy](index.md#version-policy).

## Installation

The generic MCP extra is tracked in [#788](https://github.com/pydantic/pydantic-ai-harness/issues/788). Until it lands,
install Harness and Pydantic AI's MCP support explicitly:

```bash
uv add pydantic-ai-harness "pydantic-ai-slim[mcp,openai]"
```

## Read Linear data

Linear accepts OAuth, OAuth access tokens, and Linear API keys. This example uses a caller-owned bearer token:

```python
import os

from pydantic_ai import Agent
from pydantic_ai_harness import Linear

agent = Agent(
    'openai:gpt-5.6-sol',
    capabilities=[Linear(auth=os.environ['LINEAR_ACCESS_TOKEN'])],
)
result = agent.run_sync('Summarize ENG-123 and its latest comments')
print(result.output)
```

`read_only=True` selects `https://mcp.linear.app/mcp/readonly`. This is an endpoint-level boundary, not only a prompt
instruction or local tool filter. Create a Linear API key with only the `Read` permission for an additional credential
boundary.

For interactive OAuth, pass `auth='oauth'`:

```python
from pydantic_ai_harness import Linear

linear = Linear(auth='oauth')
```

FastMCP owns the browser flow and token storage. Its default OAuth helper warns when it uses in-memory token storage.
For persistent or per-user storage, inject a preconfigured FastMCP client as shown below.

## Enable updates

Select Linear's read-write endpoint explicitly:

```python
import os

from pydantic_ai_harness import Linear

linear = Linear(
    read_only=False,
    auth=os.environ['LINEAR_ACCESS_TOKEN'],
    allowed_tools=['create_issue', 'update_issue'],
)
```

`allowed_tools` matches complete MCP tool names exactly. `None` exposes every tool returned by the selected endpoint;
an empty sequence exposes none. It is useful for narrowing the model's tool surface, while the endpoint and token
permissions remain the access boundary.

Mutation tools do not require approval automatically. Wrap the toolset with Pydantic AI's
[tool approval](/ai/tools-toolsets/toolsets/#requiring-tool-approval) when a person must confirm calls:

```python
import os

from pydantic_ai import Agent
from pydantic_ai_harness import Linear

linear = Linear(
    read_only=False,
    auth=os.environ['LINEAR_ACCESS_TOKEN'],
    allowed_tools=['create_issue'],
)
agent = Agent(
    'openai:gpt-5.6-sol',
    instructions=linear.get_instructions(),
    toolsets=[linear.get_toolset().approval_required()],
)
```

Handle the deferred approval requests as described in the linked guide.

## Inject a client or toolset

Pass a prebuilt FastMCP client when the host owns HTTP settings or per-user credentials:

```python
import os

from fastmcp import Client
from pydantic_ai_harness import LINEAR_READ_ONLY_MCP_URL, Linear

client = Client(LINEAR_READ_ONLY_MCP_URL, auth=os.environ['LINEAR_ACCESS_TOKEN'])
linear = Linear(client=client)
```

You can also pass a prebuilt `pydantic_ai.mcp.MCPToolset`. Injected clients and toolsets keep their own endpoint and
authentication, so `read_only` does not constrain them. Do not also pass `auth`; configure authentication on the
injected connection. Set `read_only=False` for an injected read-write connection so the provider instructions include
mutation guidance. `allowed_tools` still applies. Build one client or toolset per user and authentication context rather
than sharing credentials between users.

## API reference

::: pydantic_ai_harness.linear.Linear
