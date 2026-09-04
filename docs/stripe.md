---
title: Stripe
description: Give a Pydantic AI agent read-only Stripe access with explicit, approval-gated writes.
---

# Stripe

Use `Stripe` when an agent needs controlled access to one Stripe platform or connected account through Stripe's
official hosted MCP server. It is read-only by default, distinguishes sandbox from live credentials, and puts every
opt-in write through Pydantic AI's tool approval flow.

[Source](https://github.com/pydantic/pydantic-ai-harness/tree/main/pydantic_ai_harness/stripe/)

> While Pydantic AI Harness is on 0.x releases, the API may change between minor releases; when it does, deprecation warnings and release-note migration guidance tell you (or your agent) exactly how to upgrade. See the [version policy](index.md#version-policy).

## Installation

Install Harness, Pydantic AI's MCP transport, and your model provider:

```bash
uv add pydantic-ai-harness "pydantic-ai-slim[mcp,openai]"
```

Create a [restricted API key](https://docs.stripe.com/keys) with only the permissions the agent needs. Do not use an
unrestricted `sk_...` key. Supply credentials from a secret store or environment variable.

## Read from a sandbox

`mode='sandbox'` is the default and accepts only `rk_test_...` keys. The model receives API discovery, documentation,
account information, and `stripe_api_read`; it does not receive `stripe_api_write`.

```python
import os

from pydantic_ai import Agent
from pydantic_ai_harness import Stripe

agent = Agent(
    'openai:gpt-5.6-sol',
    capabilities=[Stripe(api_key=os.environ['STRIPE_API_KEY'])],
)
result = agent.run_sync('List the five most recent customers')
print(result.output)
```

The key prefix and `mode` must agree. Live access is deliberate:

```python
import os

from pydantic_ai_harness import Stripe

stripe = Stripe(api_key=os.environ['STRIPE_LIVE_RESTRICTED_KEY'], mode='live')
```

## Scope a connected account

Set `connected_account` to bind every request to one Connect account with Stripe's `Stripe-Account` header. OAuth does
not support this flow. Use a restricted platform key with the connected-account permissions required by the agent.

```python
import os

from pydantic_ai_harness import Stripe

stripe = Stripe(
    api_key=os.environ['STRIPE_PLATFORM_RESTRICTED_KEY'],
    connected_account=os.environ['STRIPE_CONNECTED_ACCOUNT_ID'],
)
```

Construct a separate `Stripe` instance for each user and account. Do not share one instance across tenants. The API
key and connected account are omitted from representations and agent instructions, but they remain credentials and
must stay out of logs and source control.

## Enable approved writes

`enable_writes=True` exposes Stripe's generic `stripe_api_write` tool. Every call requires approval before the MCP
request runs. The restricted key remains the authorization boundary and should grant only the required write
permissions.

```python
import os

from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai_harness import Stripe

agent = Agent(
    'openai:gpt-5.6-sol',
    capabilities=[
        Stripe(api_key=os.environ['STRIPE_API_KEY'], enable_writes=True),
    ],
    output_type=[str, DeferredToolRequests],
)
result = agent.run_sync('Refund payment pi_123')

if isinstance(result.output, DeferredToolRequests):
    for call in result.output.approvals:
        print(call.tool_name, call.args)
```

Resume the run with `DeferredToolResults` after your application approves or denies each request. See Pydantic AI's
[tool approval documentation](/ai/tools-toolsets/deferred-tools/). Approval prevents the model from acting without
confirmation. It does not replace authentication and server-side authorization for clients that can submit message
history.

Stripe can add tools to its hosted server. This capability uses an exact allowlist, so new tools do not become
available automatically. Re-check the [Stripe MCP tool list](https://docs.stripe.com/mcp) before changing the allowlist.

## API reference

::: pydantic_ai_harness.Stripe
