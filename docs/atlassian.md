---
title: Atlassian
description: Give a Pydantic AI agent site-scoped Jira access through Atlassian's hosted Rovo MCP server.
---

# Atlassian

Use `Atlassian` when an agent needs focused access to Jira on one Atlassian Cloud site. Confluence, Jira Service
Management, and Bitbucket Cloud can be selected from the same Atlassian-hosted Rovo MCP connection.

[Source](https://github.com/pydantic/pydantic-ai-harness/tree/main/pydantic_ai_harness/atlassian/)

> While Pydantic AI Harness is on 0.x releases, the API may change between minor releases; when it does, deprecation warnings and release-note migration guidance tell you (or your agent) exactly how to upgrade. See the [version policy](index.md#version-policy).

## Installation

```bash
uv add "pydantic-ai-harness[atlassian]" "pydantic-ai-slim[openai]"
```

Set the model credential and copy the site's `cloudId` from
`https://<your-site>.atlassian.net/_edge/tenant_info`:

```bash
export ATLASSIAN_CLOUD_ID='your-cloud-id'
export OPENAI_API_KEY='your-openai-api-key'
```

## Read Jira

```python
import os

from pydantic_ai import Agent
from pydantic_ai_harness.atlassian import Atlassian

agent = Agent(
    'openai:gpt-5.6-sol',
    capabilities=[Atlassian(cloud_id=os.environ['ATLASSIAN_CLOUD_ID'])],
)
result = agent.run_sync('Summarize unresolved work assigned to me')
print(result.output)
```

The first connection uses Atlassian's OAuth 2.1 consent flow. The default exposes an exact allowlist of read and
search tools for Jira. It does not expose Rovo's generic discovery/execute tools, unreviewed future tools, writes, or
deletes.

Each product call is checked against `cloud_id` before it reaches the server. Atlassian also applies the authenticated
principal's product permissions and OAuth site scope. Construct one capability or toolset per user in multi-user
applications; do not share an authenticated MCP client between users. Site and product instructions are included by
default; set `include_instructions=False` when equivalent instructions are already supplied elsewhere.

## Enable writes

Set `access='read_write'` to add the reviewed mutation tools. They require Pydantic AI approval by default:

```python
from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai_harness.atlassian import Atlassian

agent = Agent(
    'openai:gpt-5.6-sol',
    capabilities=[Atlassian(cloud_id='your-cloud-id', access='read_write')],
    output_type=[str, DeferredToolRequests],
)
pending = agent.run_sync('Create ENG issues from this release checklist')
print(pending.output)
```

Resume the run with `DeferredToolResults` after the user approves or denies each request. Set
`require_approval=False` only when another trusted approval policy wraps the tools or the caller intentionally accepts
unattended writes. `access='destructive'` is a separate opt-in that adds Jira's permanent delete tools; those calls are
also approval-gated by default.

For custom toolset composition, use `AtlassianToolset`. It inherits Pydantic AI's `filtered()`, `approval_required()`,
`prefixed()`, and metadata combinators. Every tool carries `atlassian_product`, `atlassian_access`, and
`atlassian_cloud_id` metadata.

## Select related products

```python
from pydantic_ai_harness.atlassian import Atlassian

Atlassian(
    cloud_id='your-cloud-id',
    products=('jira', 'confluence', 'bitbucket'),
)
```

Supported values follow Atlassian's current Rovo MCP v2 catalogue:

| Value | Authentication notes | Exposed by default |
|---|---|---|
| `jira` | OAuth 2.1 or API token | Yes |
| `confluence` | OAuth 2.1 or API token | No |
| `jira_service_management` | API token only | No |
| `bitbucket` | OAuth 2.1 or API token; workspace must be linked to an Atlassian organization | No |

Pass a service-account API key with `authorization_token=` for Bearer authentication. Personal API tokens use Basic
authentication and require a preconfigured FastMCP client passed with `client=`. A preconfigured client can also
provide persistent OAuth storage, custom TLS, or connection pooling. A URL is not accepted as `client`, and
`authorization_token` cannot be combined with a preconfigured client. These rules keep the convenience Bearer path
pinned to Atlassian's official endpoint. Both values are excluded from the capability's representation. Configure one
of these API-token mechanisms when selecting Jira Service Management; its tools do not support OAuth 2.1.

Atlassian organization administrators can independently allow or block read, write, search, delete, and manage
permission groups. The user's existing Jira, Confluence, JSM, and Bitbucket permissions still apply. Some enriched
Teamwork Graph operations consume Rovo credits; this capability does not expose those cross-product search tools.

## Multiple sites

The default capability id includes `cloud_id`, so histories and deferred loading retain site identity. Two sites can
still publish the same tool names. Wrap each capability with Pydantic AI's `PrefixTools` when one agent needs both:

```python
from pydantic_ai import Agent
from pydantic_ai.capabilities import PrefixTools
from pydantic_ai_harness.atlassian import Atlassian

agent = Agent(
    'openai:gpt-5.6-sol',
    capabilities=[
        PrefixTools(Atlassian(cloud_id='site-a'), prefix='site_a'),
        PrefixTools(Atlassian(cloud_id='site-b'), prefix='site_b'),
    ],
)
```

## References

- [Atlassian Rovo MCP v2 tools](https://support.atlassian.com/atlassian-ai-gateway/docs/supported-tools/)
- [OAuth 2.1 and site scoping](https://support.atlassian.com/atlassian-ai-gateway/docs/configure-oauth-2-1/)
- [Authentication methods](https://support.atlassian.com/atlassian-ai-gateway/docs/authentication-and-authorization/)
- [API-token authentication](https://support.atlassian.com/atlassian-ai-gateway/docs/configure-authentication-via-api-token/)
- [Organization permissions](https://support.atlassian.com/security-and-access-policies/docs/Configure-Atlassian-Rovo-MCP-server-permission/)
- [Rovo credit usage](https://support.atlassian.com/rovo/docs/rovo-usage-limits/)

## API reference

::: pydantic_ai_harness.atlassian.Atlassian

::: pydantic_ai_harness.atlassian.AtlassianToolset
