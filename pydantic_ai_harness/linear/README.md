# Linear

Use `Linear` when an agent needs to work with Linear issues, projects, and teams through Linear's
hosted MCP server. The default endpoint serves Linear's write tools as well as its read tools, so the
token's scopes are the real boundary on what an agent can change.

> While Pydantic AI Harness is on 0.x releases, the API may change between minor releases; when it does, deprecation warnings and release-note migration guidance tell you (or your agent) exactly how to upgrade. See the [version policy](https://github.com/pydantic/pydantic-ai-harness#version-policy).

## Install

```bash
uv add "pydantic-ai-harness[linear]" "pydantic-ai-slim[openai]"
```

The second package installs the OpenAI provider used by the example. For another model, install its
matching provider extra instead.

## Connect

Linear authenticates with an API key or an OAuth token; see
[Linear's MCP documentation](https://linear.app/docs/mcp). Set the key alongside your model
provider's credential:

```bash
export LINEAR_ACCESS_TOKEN="your-linear-api-key"
export OPENAI_API_KEY="your-openai-api-key"
```

```python
from pydantic_ai import Agent
from pydantic_ai_harness.linear import Linear

agent = Agent('openai:gpt-5.6-sol', capabilities=[Linear()])
result = agent.run_sync('Summarize my assigned issues that were updated this week')
print(result.output)
```

- `read_only=True` connects to Linear's read-only endpoint, which only ever exposes read tools.
- `auth=` overrides `LINEAR_ACCESS_TOKEN`, and `auth='oauth'` uses Linear's browser login instead of
  a token. An agent spec cannot carry the token; set the environment variable.
- To have a person confirm each write, wrap the toolset with Pydantic AI's
  [tool approval](https://pydantic.dev/docs/ai/tools-toolsets/toolsets/#requiring-tool-approval).

[Source](https://github.com/pydantic/pydantic-ai-harness/tree/main/pydantic_ai_harness/linear/)
