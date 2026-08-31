# Channels

Put a text-output Pydantic AI agent where your users already send messages.
Channels are useful for:

- a personal assistant in a private chat
- a support agent in team messages
- an internal agent that can use the same tools and capabilities as your app

[Source](https://github.com/pydantic/pydantic-ai-harness/tree/main/pydantic_ai_harness/channels/)

> While Pydantic AI Harness is on 0.x releases, the API may change between minor releases; when it does, deprecation warnings and release-note migration guidance tell you (or your agent) exactly how to upgrade. See the [version policy](../../docs/index.md#version-policy).

This quickstart uses Anthropic. Install the provider extra and set
`ANTHROPIC_API_KEY` before running it:

```bash
uv add "pydantic-ai-harness[anthropic]"
```

## Slack: DMs and app mentions

Use Slack to answer direct messages or respond when somebody mentions the agent
in a channel.

Create a Slack app with `chat:write`, `app_mentions:read`, and `im:history` bot
scopes. Subscribe it to `app_mention` and `message.im`. Set `SLACK_BOT_TOKEN`
and `SLACK_SIGNING_SECRET` from the app settings, keeping both outside source
control. Replace `U0123456789` below with your Slack member id from
**Profile > Copy member ID**.

```python
import asyncio
import os

from pydantic_ai import Agent

from pydantic_ai_harness.channels import ChannelHost, WebhookRequest, WebhookResponse
from pydantic_ai_harness.channels.slack import SlackChannel

agent = Agent('anthropic:claude-fable-5')
channel = SlackChannel(
    os.environ['SLACK_BOT_TOKEN'],
    os.environ['SLACK_SIGNING_SECRET'],
)
host = ChannelHost(agent, channel, allowed_senders={'U0123456789'})


def receive_slack(method: str, headers: dict[str, str], body: bytes) -> WebhookResponse:
    request = WebhookRequest(method=method, headers=headers, query={}, body=body)
    return channel.handle_webhook(request)


def start_channel() -> asyncio.Task[None]:
    return asyncio.create_task(host.serve())
```

Connect the two functions to your web app:

1. Call `start_channel()` when the app starts.
2. Forward the Events API route to `receive_slack()` and return its status code
   and body.
3. On shutdown, cancel the channel task, then await it while suppressing
   `asyncio.CancelledError`.

The lifespan and route must use the same process and event loop. Multi-worker
deployments need external ingress routing and storage.

## How channels fit Pydantic AI

Each inbound message starts an ordinary `Agent.run()`. The agent keeps its
instructions, tools, capabilities, dependencies, and history processors.

`ChannelHost` runs outside the agent loop. It loads the conversation's Pydantic
AI message history, runs the agent, saves the updated history, and asks the
adapter to send the text response. A long-lived channel connection is therefore
an integration, not an `AbstractCapability`.

## Conversation behavior

- `allowed_senders` is required and cannot be empty. Messages from other sender
  ids are dropped before the history store or agent is called.
- Turns in one conversation run in arrival order. Different conversations may
  run concurrently.
- At most 100 accepted turns may be running or waiting by default. Set
  `max_pending_turns` to tune this backpressure limit.
- `/new` waits for earlier turns in the conversation, then deletes its stored
  Pydantic AI message history. It does not cancel an active turn.
- `InMemoryConversationStore` is the default. It keeps history only for the
  current process. Implement `ConversationStore` for durable storage.
- One `ChannelHost` serves one adapter. Use separate hosts and stores for
  multiple bot accounts or providers.
- Route each bot installation to exactly one live host process. The in-process lane and
  replace-style store API do not serialize turns across multiple workers.

The host accepts agents with text output. It does not convert structured or
deferred tool outputs into chat messages.

## Delivery and failure behavior

The host does not retry `send_text()`. A timeout can occur after a provider
accepted a message, so retrying at this layer can duplicate replies. Each
adapter owns retry decisions it can make from provider-specific responses.

When an agent run or conversation store operation fails, the host logs the
exception and sends the static `error_reply`. Exception text is not sent to the
chat. If sending a reply fails, the host logs the failure and continues serving.
History remains saved after a successful run even when delivery cannot be
confirmed.

`SlackChannel` retries one `chat.postMessage` call after an HTTP 429 with a
valid `Retry-After` of at most 60 seconds. It does not retry timeouts or other
ambiguous failures because Slack may already have accepted the message.

The webhook handler verifies and enqueues a request before returning HTTP 200.
It returns HTTP 503 while the channel is opening or when its bounded queue is
full, allowing Slack to retry the event. Duplicate suppression covers the
10,000 most recently accepted `event_id` values. The queue and duplicate window
are process-local. A restart can reprocess a redelivered event, and can lose an
acknowledged event that was still waiting in the queue.

`serve()` runs until it is cancelled or the adapter ends. It owns every turn in
an AnyIO task group, so no turn task outlives it. Cancellation closes the
message iterator, cancels in-flight turns, and then closes the adapter.

## Security

Anyone allowed to message the agent can supply model input to an agent that may
have access to tools, credentials, files, and network services. Use a narrow
sender allowlist and apply normal Pydantic AI guardrails to the connected agent.

Slack signatures are checked over the exact request bytes with the app signing
secret. Requests more than five minutes from the local clock are rejected.
Replies disable link and media unfurling so Slack does not fetch URLs generated
by the agent.
`SlackChannel` validates one workspace and drops events from other workspaces,
bot-authored messages, edits, messages with a subtype (including file shares),
and unaddressed channel messages. These filters
also prevent the bot from responding to its own replies.

## Adapters and stores

`ChannelAdapter` has three responsibilities: manage its connection, yield
normalized `InboundMessage` values, and send text once. Provider-specific
polling, webhooks, formatting, limits, and retry classification stay inside the
adapter.

The async-iterator interface supports both polling and webhooks. A webhook
adapter can authenticate a request, put an accepted message onto a bounded
queue, and yield that queue from `messages()` without changing `ChannelHost`.

`ConversationStore` exposes `load`, replace-style `save`, and `delete`. Store
failures fail the current turn. A store can add its own retry or fallback policy
without adding storage policy to the host. A shared durable store does not make
multiple live hosts safe without external per-conversation serialization.

## Not included

The Slack adapter does not include Socket Mode, multi-workspace OAuth routing,
files, reactions, message edits, or messages in channels that do not mention
the bot. Socket Mode requires a WebSocket client and can be added separately.

The host does not define media, reactions, typing indicators, streaming edits,
tool approvals, or provider authentication. Adapters add only the provider
behavior they document.

## API reference

- [`ChannelHost`][pydantic_ai_harness.channels.ChannelHost]
- [`ChannelAdapter`][pydantic_ai_harness.channels.ChannelAdapter]
- [`ChannelError`][pydantic_ai_harness.channels.ChannelError]
- [`InboundMessage`][pydantic_ai_harness.channels.InboundMessage]
- [`ConversationStore`][pydantic_ai_harness.channels.ConversationStore]
- [`InMemoryConversationStore`][pydantic_ai_harness.channels.InMemoryConversationStore]
- [`WebhookRequest`][pydantic_ai_harness.channels.WebhookRequest]
- [`WebhookResponse`][pydantic_ai_harness.channels.WebhookResponse]
- [`SlackChannel`][pydantic_ai_harness.channels.slack.SlackChannel]
