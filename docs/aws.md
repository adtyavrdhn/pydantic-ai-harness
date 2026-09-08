---
title: AWS
description: Connect a Pydantic AI agent to the managed AWS MCP Server.
---

# AWS

Use `AWS` when an agent needs current AWS documentation, Region and service availability, or the
caller's own AWS account, through AWS's managed MCP server. It connects with write access by
default, so the agent is offered the tools that create and change resources; `read_only=True`
hides those. AWS enforces access, so the credential's IAM permissions decide what actually runs.

> While Pydantic AI Harness is on 0.x releases, the API may change between minor releases; when it does, deprecation warnings and release-note migration guidance tell you (or your agent) exactly how to upgrade. See the [version policy](index.md#version-policy).

## Install

```bash
uv add "pydantic-ai-harness[aws]" "pydantic-ai-slim[openai]"
```

The second package installs the OpenAI provider used by the example. For another model, install its
matching provider extra instead.

## Connect

AWS answers its documentation, Region and skill-discovery tools with no credential, so the example
below runs as written:

```python
from pydantic_ai import Agent
from pydantic_ai_harness.aws import AWS

agent = Agent('openai:gpt-5.6-sol', capabilities=[AWS()])
result = agent.run_sync('Which Regions is Amazon Bedrock available in?')
print(result.output)
```

To reach an account, pass `auth='oauth'` to sign in through the browser on the first request; AWS
refreshes that session for up to 12 hours. For a token instead, run the AWS CLI command under
[OAuth for AWS MCP Server](https://docs.aws.amazon.com/agent-toolkit/latest/userguide/oauth-authentication.html)
and set it as `AWS_MCP_TOKEN` or pass it as `auth='<token>'`; it expires after an hour and is not
refreshed. A spec file cannot carry the token, so an agent defined in YAML or JSON reads it from
`AWS_MCP_TOKEN`.

- `read_only=True` serves only the tools AWS marks `readOnlyHint`.
- `url='https://aws-mcp.eu-central-1.api.aws/mcp'` reaches the EU endpoint, which keeps requests in
  the EU. The default endpoint is `us-east-1`.
- `aws___get_presigned_url` returns a temporary bearer credential for an Amazon S3 object, and it lands in
  the message history like any other tool result. Use short expiries and redact it from logs.

To confirm every tool call before it runs, pass `AWS().get_toolset().approval_required()` to the agent's
`toolsets=` and handle the requests the way [StackOne's approval recipe](stackone.md#require-approval) shows.

[Source](https://github.com/pydantic/pydantic-ai-harness/tree/main/pydantic_ai_harness/aws/)

## API reference

::: pydantic_ai_harness.aws.AWS
