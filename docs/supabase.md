---
title: Supabase
description: Inspect one non-production Supabase project through its official hosted MCP server.
---

# Supabase

`Supabase` lets an agent inspect one Supabase development or test project through Supabase's hosted MCP server.

> While Pydantic AI Harness is on 0.x releases, the API may change between minor releases; when it does, deprecation warnings and release-note migration guidance tell you (or your agent) exactly how to upgrade. See the [version policy](index.md#version-policy).

## Install

```bash
uv add "pydantic-ai-harness[supabase]" "pydantic-ai-slim[openai]"
```

## Set up Supabase

Create or choose a non-production project, then copy its project reference from the Supabase Dashboard project
settings. Set the project reference and your model provider key:

```bash
export SUPABASE_PROJECT_REF="your-project-ref"
export OPENAI_API_KEY="your-openai-api-key"
```

No Supabase token is needed for local use. Pydantic AI's MCP client uses FastMCP to start Supabase OAuth, which opens
a browser on the first connection. Sign in and authorize the organization that contains the selected project. OAuth
tokens are stored in memory and do not persist across process restarts.

For CI, where browser login is unavailable, create a scoped personal access token in Supabase Account Settings >
Access Tokens. Limit it to this project and the read permissions the selected feature groups need, then set:

```bash
export SUPABASE_ACCESS_TOKEN="sbp_fc..."
```

Scoped personal access tokens are Public Alpha and are rolling out gradually. If scoped tokens are unavailable for
your account, a classic token grants access to every organization and project available to that account.

## Run

```python
import os

from pydantic_ai import Agent
from pydantic_ai_harness.supabase import Supabase

agent = Agent(
    'openai:gpt-5.6-sol',
    capabilities=[
        Supabase(
            project_ref=os.environ['SUPABASE_PROJECT_REF'],
            access_token=os.getenv('SUPABASE_ACCESS_TOKEN'),
        )
    ],
)
result = agent.run_sync('List the public tables and report any security advisor findings')
print(result.output)
```

You can ask the agent to:

- list tables, extensions, and migrations;
- run read-only SQL queries;
- inspect security and performance advisors or query project logs;
- get the project URL and publishable keys;
- generate TypeScript database types; or
- search Supabase documentation.

## Operational constraints

- The MCP server is Public Alpha. Use this integration only with development or test data, and do not expose it to
  end users.
- `project_ref` is required. Account-wide tools are not exposed.
- The defaults are `read_only=True` and the `database`, `debugging`, `development`, and `docs` feature groups.
- You can explicitly select any non-empty combination of those groups plus `functions`, `storage`, and `branching`.
  The Storage MCP group is disabled by default. Storage configuration updates and Branching require a paid plan;
  Branching is experimental.
- `read_only=False` enables mutation tools, but every SQL, schema, data, Edge Function, Storage, or Branching mutation
  still requires Pydantic AI tool approval. Include `DeferredToolRequests` in the agent output types and approve or
  deny each request before resuming the run.
- Treat rows and logs as untrusted content. Review each tool call and keep credential permissions narrow.

[Supabase MCP reference](https://supabase.com/docs/guides/ai-tools/mcp) | [Source](https://github.com/pydantic/pydantic-ai-harness/tree/main/pydantic_ai_harness/supabase/)

## API reference

::: pydantic_ai_harness.supabase.Supabase
