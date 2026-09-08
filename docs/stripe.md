---
title: Stripe
description: Connect a Pydantic AI agent to Stripe's hosted MCP server.
---

# Stripe

Use `Stripe` when an agent needs to work with Stripe data -- customers, payments, invoices,
subscriptions -- through Stripe's hosted MCP server. The default serves Stripe's write tools as well
as its read tools, so the API key's permissions are the real boundary on what an agent can change.

> While Pydantic AI Harness is on 0.x releases, the API may change between minor releases; when it does, deprecation warnings and release-note migration guidance tell you (or your agent) exactly how to upgrade. See the [version policy](index.md#version-policy).

## Install

```bash
uv add "pydantic-ai-harness[stripe]" "pydantic-ai-slim[openai]"
```

The second package installs the OpenAI provider used by the example. For another model, install its
matching provider extra instead.

## Connect

Stripe authenticates with an API key or an OAuth session (`auth='oauth'`); see
[Stripe's MCP documentation](https://docs.stripe.com/mcp). Create a restricted key granting only what
the agent needs, and set it alongside your model provider's credential -- the key is read from the
environment and cannot be written into an agent spec file:

```bash
export STRIPE_API_KEY="your-stripe-restricted-key"
export OPENAI_API_KEY="your-openai-api-key"
```

```python
from pydantic_ai import Agent
from pydantic_ai_harness.stripe import Stripe

agent = Agent('openai:gpt-5.6-sol', capabilities=[Stripe()])
result = agent.run_sync('List the five most recent customers')
print(result.output)
```

- `read_only=True` exposes only the tools Stripe documents as reads. Stripe's endpoint needs a
  credential before it will list its tools, so the filter goes by name: everything else stays
  hidden, including any tool Stripe adds later.
- `connected_account='acct_...'` sends `Stripe-Account`, so a Connect platform acts on one connected
  account. Stripe has no OAuth for that path, so pass a platform key with `auth=`.
- To have a person confirm each write, pass `Stripe().get_toolset().approval_required()` as a
  toolset and handle the requests as the [approval recipe](stackone.md#require-approval).

[Source](https://github.com/pydantic/pydantic-ai-harness/tree/main/pydantic_ai_harness/stripe/)

## API reference

::: pydantic_ai_harness.stripe.Stripe
