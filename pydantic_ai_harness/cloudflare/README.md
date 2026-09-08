# Cloudflare

Use `Cloudflare` when an agent needs Cloudflare's developer documentation, or the tools that read
and change a Cloudflare account. It connects to one of Cloudflare's managed MCP servers, picked by
`url`, and exposes every tool that server publishes — including the ones that create, update, and
delete resources. The API token's scopes are the real boundary.

> While Pydantic AI Harness is on 0.x releases, the API may change between minor releases; when it does, deprecation warnings and release-note migration guidance tell you (or your agent) exactly how to upgrade. See the [version policy](https://github.com/pydantic/pydantic-ai-harness#version-policy).

## Install

```bash
uv add "pydantic-ai-harness[cloudflare]" "pydantic-ai-slim[openai]"
```

The second package installs the OpenAI provider used by the example. For another model, install its
matching provider extra instead.

## Connect

The default server, `https://docs.mcp.cloudflare.com/mcp`, searches the Cloudflare developer
documentation and needs no Cloudflare credential:

```bash
export OPENAI_API_KEY="your-openai-api-key"
```

```python
from pydantic_ai import Agent
from pydantic_ai_harness.cloudflare import Cloudflare

agent = Agent('openai:gpt-5.6-sol', capabilities=[Cloudflare()])
result = agent.run_sync('How do I bind a D1 database to a Worker?')
print(result.output)
```

Cloudflare runs one server per product area and lists them all under
[servers for Cloudflare](https://developers.cloudflare.com/agents/model-context-protocol/cloudflare/servers-for-cloudflare/);
set `url` to the one you need. Those act on your account, so create an
[API token](https://developers.cloudflare.com/fundamentals/api/get-started/create-token/) and set
`CLOUDFLARE_API_TOKEN`, or pass `auth='oauth'` for browser login. Once set, the credential is sent
to whatever `url` names, the documentation server included. An agent spec cannot carry the token; set
the environment variable.

- `read_only=True` keeps only the tools the server marks `readOnlyHint` and hides everything else,
  including tools Cloudflare left unannotated. Cloudflare annotates one server at a time, so check
  a server's `tools/list` before relying on the flag: where it annotates nothing, the agent gets no
  tools at all.
- Writes run without confirmation. To review each one first, follow the
  [tool approval](https://pydantic.dev/docs/ai/tools-toolsets/toolsets/#requiring-tool-approval).

[Source](https://github.com/pydantic/pydantic-ai-harness/tree/main/pydantic_ai_harness/cloudflare/)
