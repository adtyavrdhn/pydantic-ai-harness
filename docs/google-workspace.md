---
title: Google Workspace
description: Let a Pydantic AI agent read and change a user's Gmail, Calendar, Drive, Docs, Sheets, Slides, Chat, and People data through Google's official remote MCP servers.
---

# Google Workspace

Google Workspace lets an agent read and change a user's Gmail, Calendar, Drive, Docs, Sheets, Slides, Chat, and People data through Google's official remote MCP servers. The default exposes every tool Google publishes for the products you select, including the ones that send, change, and delete.

[Source](https://github.com/pydantic/pydantic-ai-harness/tree/main/pydantic_ai_harness/google_workspace/)

> While Pydantic AI Harness is on 0.x releases, the API may change between minor releases; when it does, deprecation warnings and release-note migration guidance tell you (or your agent) exactly how to upgrade. See the [version policy](index.md#version-policy).

## Install

```bash
uv add "pydantic-ai-harness[google-workspace]" "pydantic-ai-slim[openai]"
```

## Credentials

Google's servers authenticate with a Google OAuth bearer token. They do not support dynamic client registration, so your application runs the OAuth flow and owns the token. [Google's Workspace MCP guide](https://developers.google.com/workspace/guides/configure-mcp-servers) covers enabling each product and lists the scopes each tool accepts.

Set `GOOGLE_ACCESS_TOKEN` to the token, or pass it as `auth=`. A spec file cannot carry the token: an agent defined in YAML reads it from `GOOGLE_ACCESS_TOKEN`. Set `OPENAI_API_KEY` for the example model.

## Example

```python
import asyncio

from pydantic_ai import Agent
from pydantic_ai_harness.google_workspace import GoogleWorkspace

agent = Agent('openai:gpt-5.6-sol', capabilities=[GoogleWorkspace(services=['gmail', 'calendar'])])


async def main() -> None:
    result = await agent.run('Summarize unread project mail and list my meetings today.')
    print(result.output)


asyncio.run(main())
```

Tool names are prefixed by product, so the agent sees `gmail_search_threads` and `calendar_list_events`.

## Operational constraints

- The token's scopes are the real boundary: issue a read-scoped token for an agent that should only read. `read_only=True` narrows further, to the tools Google marks read-only.
- Nothing pauses before a write. To require confirmation, wrap the toolset with the [tool approval](https://pydantic.dev/docs/ai/tools-toolsets/toolsets/#requiring-tool-approval).

## API reference

::: pydantic_ai_harness.google_workspace.GoogleWorkspace
