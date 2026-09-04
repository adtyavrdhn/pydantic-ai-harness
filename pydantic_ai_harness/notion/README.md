# Notion

Use `Notion` when an agent needs to search and read one user's Notion workspace through [Notion's official hosted MCP
server](https://developers.notion.com/guides/mcp/overview).
The default exposes only discovery and read tools. Page, database, comment, view, attachment, and agent-session
mutations must be selected by exact tool name.

[Source](https://github.com/pydantic/pydantic-ai-harness/tree/main/pydantic_ai_harness/notion/)

> While Pydantic AI Harness is on 0.x releases, the API may change between minor releases; when it does, deprecation warnings and release-note migration guidance tell you (or your agent) exactly how to upgrade. See the [version policy](https://github.com/pydantic/pydantic-ai-harness#version-policy).

## Install

Install the Notion extra and your model provider:

```bash
uv add "pydantic-ai-harness[notion]" "pydantic-ai-slim[openai]"
```

## Search and read

```python
from fastmcp import Client
from pydantic_ai import Agent
from pydantic_ai_harness import Notion
from pydantic_ai_harness.notion import NOTION_MCP_URL

client = Client(NOTION_MCP_URL, auth='oauth')
agent = Agent('openai:gpt-5.6-sol', capabilities=[Notion(client=client)])
result = agent.run_sync('Find the launch plan and summarize its open decisions')
print(result.output)
```

The first connection opens Notion's user OAuth flow. Harness does not accept or store a Notion token. The required
FastMCP client owns OAuth and its token storage. Configure persistent or application-specific storage on that client.
Harness uses a prebuilt client as-is, including its transport, authentication, and lifecycle configuration.

One `Notion` or `NotionToolset` instance is one authenticated MCP session. Do not share it across users. Construct the
client and agent per connected user, or return a per-user toolset through Pydantic AI's dynamic toolset support.

Before exposing workspace tools, the toolset calls `notion-fetch` with `id="self"`. That response identifies the
authenticated workspace and user and reports whether `notion-ai-search` is available. The toolset validates that
shape, puts only stable IDs and search availability in its instructions, and exposes a bounded attribution record as
`notion_attribution` tool metadata. Approval handlers can also read that record from `notion.attribution`, as the
runnable example does. If the workspace or user ID changes, the toolset refuses to expose tools or execute a mutation;
construct a new toolset after an intentional account change.

## Select mutations and require approval

Mutation tools are unavailable until named in `mutations`. Use the public toolset when a mutation also needs Pydantic
AI's approval flow:

```python
from fastmcp import Client
from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai_harness.notion import NotionToolset
from pydantic_ai_harness.notion import NOTION_MCP_URL

client = Client(NOTION_MCP_URL, auth='oauth')
notion = NotionToolset(client=client, mutations='notion-update-page')
approved = notion.approval_required(
    lambda _ctx, tool, _args: (tool.metadata or {}).get('notion_mutation') is True
)
agent = Agent(
    'openai:gpt-5.6-sol',
    toolsets=[approved],
    output_type=[str, DeferredToolRequests],
)
```

Selecting a mutation makes it callable; it does not approve it. The wrapper above returns a
`DeferredToolRequests` result before execution. Your application decides whether to resume with approval. Approval is
a human-in-the-loop control, not a replacement for authenticating and authorizing the application endpoint.

Treat the supplied client as a trust boundary. [Notion's security guidance](https://developers.notion.com/guides/mcp/mcp-security-best-practices)
recommends verifying the official endpoint. Production clients should target `NOTION_MCP_URL` or a trusted proxy that
implements the same contract. The allowlist classifies exact tool names, but cannot prove that a different server
implements those names safely. Treat Notion and connected-app content as untrusted data. Require approval for selected
mutations so content cannot authorize a write or redirect it to another destination.

Run [`examples/notion_page_update.py`](../../examples/notion_page_update.py) for a complete search/update flow that
prints the exact `notion-update-page` arguments before asking for terminal approval.

## Caller-owned clients

```python
from fastmcp import Client

from pydantic_ai_harness import Notion
from pydantic_ai_harness.notion import NOTION_MCP_URL

client = Client(NOTION_MCP_URL, auth='oauth')
notion = Notion(client=client)
```

Configure OAuth token storage on the FastMCP client for your deployment. Passing an in-process FastMCP server through
the same `client=` seam keeps tests credential-free.

Notion's hosted endpoint currently requires [interactive user OAuth and does not accept bearer
tokens](https://developers.notion.com/guides/mcp/get-started-with-mcp#faqs). The default
read allowlist ignores new server tools until this integration classifies them, so a newly advertised mutation does
not become available automatically.

## API reference

::: pydantic_ai_harness.notion.Notion

::: pydantic_ai_harness.notion.NotionToolset
