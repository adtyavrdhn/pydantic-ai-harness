---
title: Atlassian
description: Connect a Pydantic AI agent to Atlassian's hosted Rovo MCP server.
---

# Atlassian

Use `Atlassian` when an agent needs to work with Jira, Confluence, Bitbucket and the other Atlassian
apps through Atlassian's hosted Rovo MCP server. The default serves Atlassian's write tools as well
as its read ones, so the credential's scopes and the permission groups your organization admin has
enabled are the real boundary on what an agent can change.

> While Pydantic AI Harness is on 0.x releases, the API may change between minor releases; when it does, deprecation warnings and release-note migration guidance tell you (or your agent) exactly how to upgrade. See the [version policy](index.md#version-policy).

## Install

```bash
uv add "pydantic-ai-harness[atlassian]" "pydantic-ai-slim[openai]"
```

The second package installs the OpenAI provider used by the example. For another model, install its
matching provider extra instead.

## Connect

Atlassian takes three credentials, and your organization admin decides which are open to you: OAuth
2.1 is the primary method, while a personal API token and a service account API key work only where
authentication by API token has been turned on. Separately, the `delete_jira` and `manage_jira`
permission groups are off until an admin enables them. See
[Atlassian's authentication guide](https://developer.atlassian.com/cloud/rovo-mcp/guides/authentication-and-authorization/).

Left unset, `auth` reads `$ATLASSIAN_API_KEY` and sends it as a bearer token, which is how Atlassian
takes a service account API key, falling back to `'oauth'`, which opens a browser for Atlassian
sign-in and consent to one site. A *personal* API token is a different credential, which Atlassian
takes over Basic auth: pass `auth=httpx.BasicAuth('you@example.com', 'your-personal-api-token')`
rather than setting the environment variable. Some tool sets are reachable over only one method --
Jira Service Management needs an API token, while code search and Teams need OAuth -- so pick the one
your work needs. A credential cannot be written into an agent spec file, where a spec naming `auth:`
is rejected; set it in the environment instead. Set your model provider's credential as well:

```bash
export ATLASSIAN_API_KEY="your-service-account-api-key"
export OPENAI_API_KEY="your-openai-api-key"
```

```python
from pydantic_ai import Agent
from pydantic_ai_harness.atlassian import Atlassian

agent = Agent('openai:gpt-5.6-sol', capabilities=[Atlassian()])
result = agent.run_sync('Summarize my unresolved Jira work and find the oldest item')
print(result.output)
```

- `read_only=True` keeps only the tools Atlassian marks read-only, and drops any tool it leaves
  unmarked.
- An API token is not bound to one Atlassian site, so tools that act on one take a `cloudId`
  argument; OAuth consent instead covers only the site you approve.
- To have a person confirm each write, wrap the toolset with the
  [approval recipe](stackone.md#require-approval).

[Source](https://github.com/pydantic/pydantic-ai-harness/tree/main/pydantic_ai_harness/atlassian/)

## API reference

::: pydantic_ai_harness.atlassian.Atlassian
