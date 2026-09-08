# Linear

Use `Linear` when an agent needs to read, create, and update Linear issues, projects, teams, and
comments. It connects to Linear's hosted MCP server with write access by default; `read_only=True`
switches to Linear's read-only endpoint, so the server hides the tools that change data.

> While Pydantic AI Harness is on 0.x releases, the API may change between minor releases; when it does, deprecation warnings and release-note migration guidance tell you (or your agent) exactly how to upgrade. See the [version policy](https://github.com/pydantic/pydantic-ai-harness#version-policy).

## Install

```bash
uv add "pydantic-ai-harness[linear]" "pydantic-ai-slim[openai]"
```

The second package installs the OpenAI provider used by the example. For another model, install its
matching provider extra instead.

## Connect

Create a Linear API key under **Settings > Account > Security & Access > Personal API keys**, then
set the Linear and model-provider credentials:

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

Pass `auth='oauth'` instead of a token for Linear's browser login. OAuth connections can stall on the
first tool call ([python-sdk #3209](https://github.com/modelcontextprotocol/python-sdk/issues/3209));
an API key avoids that.

## Approving writes

The default endpoint serves the tools that create and update issues, projects, and comments. Pair
it with [tool approval](https://pydantic.dev/docs/ai/tools-toolsets/toolsets/#requiring-tool-approval) so a person confirms each
change:

```python
import os

from pydantic_ai import Agent
from pydantic_ai.messages import DeferredToolRequests
from pydantic_ai_harness.linear import Linear

linear = Linear(auth=os.environ['LINEAR_ACCESS_TOKEN'])
agent = Agent(
    'openai:gpt-5.6-sol',
    toolsets=[linear.get_toolset().approval_required()],
    output_type=[str, DeferredToolRequests],
)
```

`Linear` only configures the connection. For tool filtering or a custom client, use Pydantic AI's
`MCPToolset` directly.

[Source](https://github.com/pydantic/pydantic-ai-harness/tree/main/pydantic_ai_harness/linear/)
