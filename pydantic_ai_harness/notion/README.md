# Notion

Use `Notion` when an agent needs to search, read, and change pages, databases, and comments in a
Notion workspace. It connects to Notion's hosted MCP server with write access by default:
`read_only=True` keeps only the tools this package classifies as reads.

> While Pydantic AI Harness is on 0.x releases, the API may change between minor releases; when it does, deprecation warnings and release-note migration guidance tell you (or your agent) exactly how to upgrade. See the [version policy](https://github.com/pydantic/pydantic-ai-harness#version-policy).

## Install

```bash
uv add "pydantic-ai-harness[notion]" "pydantic-ai-slim[openai]"
```

The second package installs the OpenAI provider used by the example. For another model, install its
matching provider extra instead.

## Connect

Notion's MCP endpoint authenticates with OAuth and does not accept a static integration secret, so
there is no Notion environment variable to set. The first connection opens a browser and asks which
pages to share; see [Notion's MCP documentation](https://developers.notion.com/guides/mcp/get-started-with-mcp).
Set your model provider's credential:

```bash
export OPENAI_API_KEY="your-openai-api-key"
```

```python
from pydantic_ai import Agent
from pydantic_ai_harness.notion import Notion

agent = Agent('openai:gpt-5.6-sol', capabilities=[Notion()])
result = agent.run_sync('Summarize the launch plan page')
print(result.output)
```

- The pages you share during the OAuth grant bound what the agent can reach. Within them, the
  default exposes the tools that create, update, and move content.
- Notion publishes no read-only endpoint and does not classify its tools, so that read list is this
  package's own: a tool Notion adds later stays hidden until the list is updated here.
- To have a person confirm each write, call `.approval_required()` on the toolset returned by
  `Notion().get_toolset()` and pass that to the agent as a toolset; the
  [approval recipe](https://github.com/pydantic/pydantic-ai-harness/blob/main/pydantic_ai_harness/stackone/README.md#require-approval)
  shows how to handle the resulting requests.

Pass `auth=` with an OAuth access token your application already holds, or a custom `httpx.Auth`, to
skip the browser login. An agent spec cannot carry the token; set it in Python.

[Source](https://github.com/pydantic/pydantic-ai-harness/tree/main/pydantic_ai_harness/notion/)
