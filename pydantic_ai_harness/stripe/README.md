# Stripe

`Stripe` lets an agent read one Stripe platform or connected account and request approval for opt-in writes through
Stripe's hosted MCP server.

[Source](https://github.com/pydantic/pydantic-ai-harness/tree/main/pydantic_ai_harness/stripe/)

> While Pydantic AI Harness is on 0.x releases, the API may change between minor releases; when it does, deprecation warnings and release-note migration guidance tell you (or your agent) exactly how to upgrade. See the [version policy](https://github.com/pydantic/pydantic-ai-harness#version-policy).

## Install

```bash
uv add "pydantic-ai-harness[stripe]" "pydantic-ai-slim[openai]"
```

## Set up Stripe and your model

In the Stripe Dashboard, create a restricted API key with only the read permissions the agent needs. Export that key
and your model-provider key:

```bash
export STRIPE_API_KEY='rk_test_...'
export OPENAI_API_KEY='...'
```

The capability sends `STRIPE_API_KEY` directly to Stripe as a bearer token. It does not run OAuth or open a browser.
Do not use an unrestricted `sk_...` key.

## Run an agent

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

You can ask the agent to:

- search for Stripe API methods and inspect their parameters;
- retrieve account information;
- read customers, payments, refunds, invoices, subscriptions, and other methods supported by Stripe MCP;
- search Stripe documentation;
- request supported API writes when writes are enabled.

## Operational constraints

- Access is read-only by default. `enable_writes=True` exposes `stripe_api_write`; every call returns a
  `DeferredToolRequests` approval request before Stripe receives the write. Preserve the request metadata when
  resuming. Approved results are replayable within the same scope, so persist and consume them atomically.
- `mode='sandbox'` accepts `rk_test_...` keys. Set `mode='live'` explicitly for an `rk_live_...` key.
- Set `connected_account='acct_...'` to send every request to one Connect account. Connected-account access requires
  a restricted platform key with the needed connected-account permissions and does not support OAuth.
- The capability sends requests only to `https://mcp.stripe.com` and exposes an exact tool allowlist. Stripe labels
  the MCP server Public preview. Confirm that preview services meet your requirements before using live mode.
