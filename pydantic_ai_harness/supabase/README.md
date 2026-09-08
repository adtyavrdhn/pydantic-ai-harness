# Supabase

Use `Supabase` when an agent needs to inspect or change one Supabase project's tables, data,
migrations, logs, and advisors through Supabase's hosted MCP server. The default connection serves
Supabase's write tools, `execute_sql` among them, so the token's scopes and the project you point it
at are the real boundary on what an agent can change.

> While Pydantic AI Harness is on 0.x releases, the API may change between minor releases; when it does, deprecation warnings and release-note migration guidance tell you (or your agent) exactly how to upgrade. See the [version policy](https://github.com/pydantic/pydantic-ai-harness#version-policy).

## Install

```bash
uv add "pydantic-ai-harness[supabase]" "pydantic-ai-slim[openai]"
```

The second package installs the OpenAI provider used by the example. For another model, install its
matching provider extra instead.

## Connect

Supabase authenticates with a personal access token or a browser login; see
[Supabase's MCP documentation](https://supabase.com/docs/guides/ai-tools/mcp). Set the token
alongside your model provider's credential:

```bash
export SUPABASE_ACCESS_TOKEN="your-supabase-access-token"
export OPENAI_API_KEY="your-openai-api-key"
```

`project_ref` is the project's reference ID, from its dashboard URL.

```python
from pydantic_ai import Agent
from pydantic_ai_harness.supabase import Supabase

agent = Agent('openai:gpt-5.6-sol', capabilities=[Supabase(project_ref='your-project-ref')])
result = agent.run_sync('List the public tables and report any security advisor findings')
print(result.output)
```

- `read_only=True` connects with Supabase's `read_only=true`, so the server runs SQL as a read-only
  Postgres user and withholds its write tools.
- `auth=` overrides `SUPABASE_ACCESS_TOKEN`, and `auth='oauth'` uses Supabase's browser login instead
  of a token. An agent spec cannot carry the token; set the environment variable.
- To have a person confirm each write, wrap the toolset with Pydantic AI's
  [tool approval](https://pydantic.dev/docs/ai/tools-toolsets/toolsets/#requiring-tool-approval).

[Source](https://github.com/pydantic/pydantic-ai-harness/tree/main/pydantic_ai_harness/supabase/)
