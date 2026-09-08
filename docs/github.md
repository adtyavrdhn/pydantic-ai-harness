---
title: GitHub
description: Connect an agent to GitHub's hosted MCP server to read and change repositories, issues, and pull requests.
---

# GitHub

`GitHub` connects an agent to GitHub's hosted MCP server, so it can read and change repositories,
issues, and pull requests. The default serves write tools as well as read tools, and the token you
give it decides what the agent can actually reach.

[Source](https://github.com/pydantic/pydantic-ai-harness/tree/main/pydantic_ai_harness/github/)

> While Pydantic AI Harness is on 0.x releases, the API may change between minor releases; when it does, deprecation warnings and release-note migration guidance tell you (or your agent) exactly how to upgrade. See the [version policy](index.md#version-policy).

## Install

```bash
uv add "pydantic-ai-harness[github]" "pydantic-ai-slim[openai]"
```

## Set up GitHub

Create a [fine-grained personal access token](https://github.com/settings/personal-access-tokens)
granting only the repositories and permissions the agent needs, then set it as `GITHUB_TOKEN`,
which `GitHub` reads when you pass no `auth=`. An agent spec cannot name the token, so a
checked-in spec file never carries one:

```bash
export GITHUB_TOKEN='your-token'
export OPENAI_API_KEY='your-model-key'
```

## Review a pull request

```python
from pydantic_ai import Agent

from pydantic_ai_harness.github import GitHub

agent = Agent(
    'openai:gpt-5.6-sol',
    instructions='Review the requested pull request. Cite file paths and line numbers for every finding.',
    capabilities=[GitHub(read_only=True)],
)

result = agent.run_sync('Review pull request #123 in pydantic/pydantic-ai')
print(result.output)
```

## Operational constraints

- The token's permissions are GitHub's authorization boundary, and writes run without confirmation.
  Use the [tool approval](https://pydantic.dev/docs/ai/tools-toolsets/toolsets/#requiring-tool-approval) where a change needs a human first.
- `read_only=True` sends GitHub's `X-MCP-Readonly` header, and GitHub then serves only the tools it
  marks read-only.
- For GitHub Enterprise Cloud with data residency, pass your tenant's endpoint as
  `url='https://copilot-api.<tenant>.ghe.com/mcp/'`.

## API reference

::: pydantic_ai_harness.github.GitHub
