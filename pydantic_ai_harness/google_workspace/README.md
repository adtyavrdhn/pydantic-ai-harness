# Google Workspace

Google Workspace gives an agent selected Gmail, Calendar, Drive, Docs, Sheets, Slides, Chat, and People tools through Google's official remote MCP servers. Gmail and Calendar are selected by default.

[Source](https://github.com/pydantic/pydantic-ai-harness/tree/main/pydantic_ai_harness/google_workspace/)

> While Pydantic AI Harness is on 0.x releases, the API may change between minor releases; when it does, the release notes explain the migration. See the [version policy](../../docs/index.md#version-policy).

## Install

The capability uses Pydantic AI's MCP support. Install it alongside Harness:

```bash
uv add pydantic-ai-harness "pydantic-ai-slim[mcp,openai,spec]"
```

Google's servers are in the Workspace Developer Preview Program. Enable the selected Workspace APIs and MCP services in a Google Cloud project before connecting.

## Use Gmail and Calendar

Create a Web application OAuth client in Google Cloud. Register `http://localhost:3000/callback` as an authorized redirect URI, including the scheme, host, port, and path exactly. Set `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, and `OPENAI_API_KEY`, then run:

```python
import asyncio
import os

from pydantic_ai import Agent
from pydantic_ai_harness import GoogleWorkspace

workspace = GoogleWorkspace(
    oauth_client_id=os.environ['GOOGLE_OAUTH_CLIENT_ID'],
    oauth_client_secret=os.environ['GOOGLE_OAUTH_CLIENT_SECRET'],
)
agent = Agent('openai:gpt-5.6-sol', capabilities=[workspace])


async def main() -> None:
    result = await agent.run('Summarize unread project mail and list my meetings today.')
    print(result.output)


asyncio.run(main())
```

The first connection opens Google's OAuth flow. Set `oauth_callback_port` and register the matching `http://localhost:<port>/callback` URI when port 3000 is unavailable. `GOOGLE_ACCESS_TOKEN` or `access_token=` can supply a caller-managed bearer token instead.

## Select products and tools

Pass `services` to replace the Gmail and Calendar default. Every tool is prefixed by service, such as `gmail_search_threads`, `calendar_list_events`, or `drive_search_files`.

```python
from pydantic_ai_harness import GoogleWorkspace

workspace = GoogleWorkspace(
    services=('drive', 'docs', 'sheets'),
    allowed_tools=('drive_search_files', 'docs_read_doc', 'sheets_get_values'),
)
```

`allowed_tools` is an exact allowlist of prefixed names. It intersects with `read_only`, so a mutating name remains unavailable while `read_only=True`.

The default read-only policy exposes these documented operations:

| Product | Tools |
|---|---|
| Gmail | `get_message`, `get_thread`, `list_drafts`, `list_labels`, `search_threads` |
| Calendar | `get_event`, `list_calendars`, `list_events`, `search_events`, `suggest_time` |
| Drive | `download_file_content`, `get_file_metadata`, `get_file_permissions`, `list_recent_files`, `read_file_content`, `search_files` |
| Docs | `read_doc` |
| Sheets | `get_spreadsheet`, `get_values` |
| Slides | `read_presentation` |
| Chat | `list_memberships`, `list_messages`, `search_conversations`, `search_messages` |
| People | `get_user_profile`, `search_contacts`, `search_directory_people` |

Set `read_only=False` to expose other tools returned by the selected servers. This is explicit because those tools can create drafts and files, update documents, label messages, send chat messages, and change or delete calendar events.

## Bring your own clients

Pass one caller-owned FastMCP client or `MCPToolset` per service when the application owns OAuth, persistent token storage, tenant selection, or HTTP policy:

```python
from fastmcp import Client
from fastmcp.client.auth import OAuth

from pydantic_ai_harness import GoogleWorkspace

gmail_url = 'https://gmailmcp.googleapis.com/mcp/v1'
gmail = Client(
    gmail_url,
    auth=OAuth(
        mcp_url=gmail_url,
        client_id='your-client-id',
        client_secret='your-client-secret',
        callback_port=3000,
    ),
)
workspace = GoogleWorkspace(services=('gmail',), clients={'gmail': gmail})
```

Configure `OAuth(token_storage=...)` on the client for persistent tokens. Construct a separate client mapping for each concurrent user's run. A shared MCP client represents one authenticated identity.

## Mutations and approval

Mutation tools are hidden until `read_only=False`. To require Pydantic AI approval before every exposed operation, wrap the generated toolset:

```python
import os

from pydantic_ai import Agent
from pydantic_ai_harness import GoogleWorkspace

workspace = GoogleWorkspace(read_only=False, access_token=os.environ['GOOGLE_ACCESS_TOKEN'])
agent = Agent(
    'openai:gpt-5.6-sol',
    toolsets=[workspace.get_toolset().approval_required()],
    defer_model_check=True,
)
```

Handle the resulting deferred requests with Pydantic AI's [tool approval](https://ai.pydantic.dev/deferred-tools/) flow. For narrower approval, pass a predicate to `approval_required()`.

Email, documents, calendar descriptions, and chat messages can contain indirect prompt injections. Keep mutation tools narrow, review deferred actions, and compose with an input or tool-result guard when content is not trusted.

## Agent specs

`GoogleWorkspace` supports Pydantic AI agent specs. Secrets and runtime clients are excluded, so provide authentication through environment variables or application code:

```yaml
model: openai:gpt-5.6-sol
capabilities:
  - GoogleWorkspace:
      services: [gmail, calendar]
      allowed_tools: [gmail_search_threads, calendar_list_events]
```

```python
from pydantic_ai import Agent
from pydantic_ai_harness import GoogleWorkspace

agent = Agent.from_file('agent.yaml', custom_capability_types=[GoogleWorkspace])
```

Google's [Workspace MCP configuration guide](https://developers.google.com/workspace/guides/configure-mcp-servers) lists the required APIs, OAuth scopes, endpoints, and current tool catalog.
