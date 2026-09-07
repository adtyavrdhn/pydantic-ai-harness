# Google Workspace

Google Workspace lets an agent read, and optionally change, a user's Gmail, Calendar, Drive, Docs, Sheets, Slides, Chat, and People data through Google's official remote MCP servers.

[Source](https://github.com/pydantic/pydantic-ai-harness/tree/main/pydantic_ai_harness/google_workspace/)

> While Pydantic AI Harness is on 0.x releases, the API may change between minor releases; when it does, deprecation warnings and release-note migration guidance tell you (or your agent) exactly how to upgrade. See the [version policy](../../docs/index.md#version-policy).

## Install

```bash
uv add "pydantic-ai-harness[google-workspace]" "pydantic-ai-slim[openai]"
```

## Provider setup

Google's Workspace MCP servers are in Developer Preview. In Google Cloud:

1. Join the Google Workspace Developer Preview Program, then enable the Workspace API and MCP service for each product you will select.
2. Create an OAuth client. Google's servers do not support dynamic client registration, so your application runs the OAuth flow and owns the token.
3. Obtain a bearer token with the scope for each selected product. Each scope below starts with `https://www.googleapis.com/auth/`.

| Product | Read scope | Write scope |
|---|---|---|
| Gmail | `gmail.readonly` | `gmail.modify` |
| Drive | `drive.readonly` | `drive.file` |
| Docs | `documents.readonly` | `documents` |
| Sheets | `spreadsheets.readonly` | `spreadsheets` |
| Slides | `presentations.readonly` | `presentations` |
| Calendar | `calendar.readonly` | `calendar.events` |
| Chat | `chat.messages.readonly` | `chat.messages.create` |
| People | `contacts.readonly` | none |

Each tool's reference page in the [Workspace MCP guide](https://developers.google.com/workspace/guides/configure-mcp-servers) lists every scope it accepts.

Set `GOOGLE_ACCESS_TOKEN` to the token, or pass it as `auth=`. Set `OPENAI_API_KEY` for the example model.

## Example

```python
import asyncio

from pydantic_ai import Agent
from pydantic_ai_harness.google_workspace import GoogleWorkspace

agent = Agent('openai:gpt-5.6-sol', capabilities=[GoogleWorkspace(['gmail', 'calendar'])])


async def main() -> None:
    result = await agent.run('Summarize unread project mail and list my meetings today.')
    print(result.output)


asyncio.run(main())
```

Only the tools Google marks read-only are exposed. Tool names are prefixed by product, such as `gmail_search_threads` and `calendar_list_events`.

## Allowing changes

Pass `access='write'` to expose every tool for the selected products. Add `require_approval=True` to pause the run before each tool that is not read-only, then approve and resume it as the [deferred tools guide](https://pydantic.dev/docs/ai/deferred-tools/) describes:

```python
from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai_harness.google_workspace import GoogleWorkspace

agent = Agent(
    'openai:gpt-5.6-sol',
    capabilities=[GoogleWorkspace('calendar', access='write', require_approval=True)],
    output_type=[str, DeferredToolRequests],
)
```

`allowed_tools` is an exact allowlist of prefixed names, such as `allowed_tools=['calendar_create_event']`. It narrows what the access mode exposes and never widens it.

## Operational constraints

- `access='read'` filters the tools the agent sees. It does not narrow the token's scopes, so issue the token with read scopes unless you enable writes.
- `auth` accepts a bearer token or an `httpx.Auth`. Use an `httpx.Auth` when the token must be refreshed or looked up per request. For per-user credentials, build the capability inside a [per-run toolset](https://pydantic.dev/docs/ai/mcp/client/#per-user-authentication).
- Use one instance per agent. Two instances for the same product produce colliding tool names. When one run needs two identities, pass each `.get_toolset().prefixed('alice')` through `toolsets` instead; the capability's instructions are then not added automatically.
- Workspace content can contain instructions aimed at the model. The default instructions tell the model to treat it as untrusted data. Google asks applications to screen prompts and responses with [Model Armor or an equivalent](https://developers.google.com/workspace/guides/configure-mcp-security).
