---
title: Linear
description: Connect a Pydantic AI agent to Linear's hosted MCP server.
---

# Linear

`Linear` connects an agent to Linear's hosted MCP server. It uses Linear's
server-enforced read-only endpoint by default.

> While Pydantic AI Harness is on 0.x releases, the API may change between minor releases; when it does, deprecation warnings and release-note migration guidance tell you (or your agent) exactly how to upgrade. See the [version policy](index.md#version-policy).

## Install

```bash
uv add "pydantic-ai-harness[linear]" "pydantic-ai-slim[openai]"
```

The second package installs the OpenAI provider used by the example. For
another model, install its matching provider extra instead.

## Connect

Create a Linear API key under **Settings > Account > Security & Access >
Personal API keys**, then set the Linear and model-provider credentials:

```bash
export LINEAR_ACCESS_TOKEN="your-linear-api-key"
export OPENAI_API_KEY="your-openai-api-key"
```

```python
import os

from pydantic_ai import Agent
from pydantic_ai_harness.linear import Linear

agent = Agent(
    'openai:gpt-5.6-sol',
    capabilities=[Linear(auth=os.environ['LINEAR_ACCESS_TOKEN'])],
)
result = agent.run_sync('Summarize my assigned issues that were updated this week')
print(result.output)
```

Pass `auth='oauth'` instead of a token to use Linear's interactive OAuth flow.
If an OAuth connection stalls or fails, use a bearer token to avoid the Python
MCP SDK's OAuth code path.

## Read and write access

`read_only=True` uses Linear's `/mcp/readonly` endpoint. To connect to the
read-write endpoint, opt in explicitly:

```python
linear = Linear(auth=os.environ['LINEAR_ACCESS_TOKEN'], read_only=False)
```

`Linear` only configures the connection. For custom clients, tool filtering,
or approval policy, use Pydantic AI's generic `MCP` capability or `MCPToolset`
directly.

[Source](https://github.com/pydantic/pydantic-ai-harness/tree/main/pydantic_ai_harness/linear/)
