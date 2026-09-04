# Channels

Channels run a text-output Pydantic AI agent for messages received from chat providers. Use them
when an HTTP endpoint, queue consumer, or polling loop needs to preserve conversation history and
send the agent's response through the provider that delivered the message.

[Source code](https://github.com/pydantic/pydantic-ai-harness/tree/main/pydantic_ai_harness/channels/)

> While Pydantic AI Harness is on 0.x releases, the API may change between minor releases; when it does, deprecation warnings and release-note migration guidance tell you (or your agent) exactly how to upgrade. See the [version policy](https://github.com/pydantic/pydantic-ai-harness#version-policy).

## Quick start

`ChannelHost` sits outside `AbstractCapability`. It starts each turn through the agent's public
`run()` method, so capabilities, tools, model settings, and output validation configured on the
agent continue to apply.

```python
import os

import anyio
from pydantic_ai import Agent

from pydantic_ai_harness.channels import ChannelEvent, ChannelHost
from pydantic_ai_harness.channels.slack import SlackChannel, SlackUrlVerification

agent = Agent('openai:gpt-5.6-sol', output_type=str)
slack = SlackChannel(
    signing_secret=os.environ['SLACK_SIGNING_SECRET'],
    bot_token=os.environ['SLACK_BOT_TOKEN'],
    team_id=os.environ['SLACK_TEAM_ID'],
)
host = ChannelHost(agent, slack)
send_events, receive_events = anyio.create_memory_object_stream[ChannelEvent](100)


def accept_slack_request(raw_body: bytes, headers: dict[str, str]) -> str | None:
    request = slack.parse_request(raw_body, headers)
    if isinstance(request, SlackUrlVerification):
        return request.challenge
    if request is not None:
        send_events.send_nowait(request)
    return None


async def consume_events() -> None:
    async with receive_events:
        async for event in receive_events:
            await host.handle(event)
```

Call `accept_slack_request` with the untouched request bytes. Return its challenge for Slack URL
verification, otherwise acknowledge the verified request immediately after enqueueing it. Run
`consume_events` as a worker owned by your application.

## Host contract

Adapters normalize five values into `ChannelEvent`: `event_id`, `conversation_id`, `sender_id`,
`text`, and optional `reply_to_id`. `SlackChannel` requires the workspace's `team_id` and rejects
events for other installations before normalization. Build a separate adapter for each provider
installation or credential set.

The host loads the conversation's Pydantic AI messages, calls `AbstractAgent.run()`, sends the text
result through `ChannelAdapter.reply()`, then saves `result.all_messages()`. Calls for one
`conversation_id` are serialized within one `ChannelHost`; different conversations may run at the
same time.

`InMemoryConversationStore` is the default and loses history when the process exits. Implement
`ConversationStore` for persistent history. Multiple workers must partition their queue by
`conversation_id` or coordinate outside the host because host locks are process-local.

The host does not claim events, acknowledge provider delivery, or retry. Claim `event_id` in the
caller-owned queue before calling `handle()`. Duplicate calls run duplicate agent turns. Agent,
reply, and store errors propagate. The host replies before saving history: a reply failure leaves
history unchanged, while a save failure or cancellation can occur after the user has received the
reply. A durable store should own its transaction and retry policy. Do not blindly retry the whole
handler after an ambiguous delivery or save failure.

## Slack behavior

`SlackChannel.parse_request()` verifies `X-Slack-Request-Timestamp` and `X-Slack-Signature` against
the raw body before decoding JSON. Requests more than five minutes from local time are rejected.
It accepts `app_mention` and ordinary `message` callbacks, ignores bot messages and message
subtypes, and returns `None` for unsupported events.

Signature verification authenticates Slack, not the human sender. Apply channel and sender policy
to the normalized IDs before enqueueing events when the app should serve only an allowlist.

Root messages use their `ts` as `reply_to_id`. Thread replies keep the parent's `thread_ts`, so
`SlackChannel.reply()` posts the response to the original thread with `chat.postMessage`.

Pass an `httpx.AsyncClient` to reuse connections. The caller owns an injected client. Without one,
each reply uses a short-lived client. `SlackChannel` stores its signing secret and bot token in
private attributes and excludes them from `repr`; the caller remains responsible for secret
storage and token selection.

Slack requires an HTTP 2xx response within three seconds and may retry delivery. HTTP framework,
queue, claim/ack, and retry policy remain application concerns.

## API reference

::: pydantic_ai_harness.channels.ChannelEvent

::: pydantic_ai_harness.channels.ChannelHost

::: pydantic_ai_harness.channels.ConversationStore

::: pydantic_ai_harness.channels.InMemoryConversationStore

::: pydantic_ai_harness.channels.slack.SlackChannel
