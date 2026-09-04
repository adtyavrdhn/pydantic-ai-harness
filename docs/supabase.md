---
title: Supabase
description: Inspect one non-production Supabase project through its official hosted MCP server.
---

# Supabase

Use `Supabase` to inspect one non-production Supabase development or test project through Supabase's official hosted
MCP server. The server is Public Alpha. Do not use this integration with production data or expose it to end users.

[Source](https://github.com/pydantic/pydantic-ai-harness/tree/main/pydantic_ai_harness/supabase/)

> While Pydantic AI Harness is on 0.x releases, the API may change between minor releases; when it does, deprecation warnings and release-note migration guidance tell you (or your agent) exactly how to upgrade. See the [version policy](index.md#version-policy).

## Installation

Install Harness and Pydantic AI's MCP extra:

```bash
uv add pydantic-ai-harness "pydantic-ai-slim[mcp,openai]"
```

## Read one development project

```python
import os

from pydantic_ai import Agent
from pydantic_ai_harness import Supabase

agent = Agent(
    'openai:gpt-5.6-sol',
    capabilities=[
        Supabase(project_ref=os.environ['SUPABASE_PROJECT_REF']),
    ],
)
result = agent.run_sync('List the public tables and check the security advisors')
print(result.output)
```

The default uses browser OAuth and may open a browser on first connection. FastMCP's default OAuth storage is
in-memory, so the token does not persist across process restarts. For CI, pass a PAT explicitly:

```python
Supabase(
    project_ref=os.environ['SUPABASE_PROJECT_REF'],
    access_token=os.environ['SUPABASE_ACCESS_TOKEN'],
)
```

The token is excluded from capability and toolset representations. Construct one capability or toolset per user;
Pydantic AI MCP sessions retain the identity that opened them.

Scoped PATs are Public Alpha and are still rolling out. Prefer a scoped PAT limited to the selected project and the
read permissions needed by the chosen feature groups. Classic PATs cover every organization and project the account
can access.

## Defaults and feature groups

`project_ref` is required, so account-wide tools are not exposed. `read_only=True` makes `execute_sql` use Supabase's
read-only Postgres user and removes other mutation tools. The default `features` are:

```python
Supabase(
    project_ref='your-development-project-ref',
    features=('database', 'debugging', 'development', 'docs'),
)
```

You can select any non-empty combination of `database`, `debugging`, `development`, `docs`, `functions`, `storage`,
and `branching`. The capability sends this exact list to the server and exposes only the currently documented tools
in those groups. This fail-closed list prevents a new Public Alpha server tool from appearing before Harness has
classified it.

Storage is not enabled by Supabase by default. Updating Storage configuration requires a paid plan. Branching is
experimental and also requires a paid plan.

## Enable writes and approvals

Writes require `read_only=False`. SQL can change data or schema, so `execute_sql`, `apply_migration`, and every other
documented mutation tool then require Pydantic AI approval by default:

```python
from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai_harness import Supabase

agent = Agent(
    'openai:gpt-5.6-sol',
    capabilities=[Supabase(project_ref='your-development-project-ref', read_only=False)],
    output_type=[str, DeferredToolRequests],
)
```

Handle `DeferredToolRequests` with Pydantic AI's
[tool approval](/ai/tools-toolsets/toolsets/#requiring-tool-approval) flow.

To add a stricter caller policy, compose the returned toolset with the public toolset wrapper:

```python
from pydantic_ai import Agent
from pydantic_ai_harness import Supabase

supabase = Supabase(
    project_ref='your-development-project-ref',
    read_only=False,
)
tools = supabase.get_toolset().approval_required()
agent = Agent('openai:gpt-5.6-sol', toolsets=[tools])
```

This version requires approval for every Supabase tool call. The capability's mutation approval remains in place
under the caller's stricter wrapper.

See the [Supabase MCP documentation](https://supabase.com/docs/guides/ai-tools/mcp) for current security guidance,
feature groups, authentication, and plan restrictions.

## API reference

::: pydantic_ai_harness.supabase.Supabase
