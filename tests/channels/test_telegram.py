from __future__ import annotations

import json
import logging
from collections.abc import Mapping

import httpx
import pytest
from pydantic import TypeAdapter
from pydantic_ai import Agent

from pydantic_ai_harness.channels import ChannelEvent, ChannelHost
from pydantic_ai_harness.channels.telegram import TelegramChannel, TelegramError, TelegramWebhookError

pytestmark = pytest.mark.anyio

_BODY_ADAPTER = TypeAdapter(dict[str, object])


@pytest.fixture
def anyio_backend() -> str:
    return 'asyncio'


def _response(payload: object, *, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, json=payload)


def _headers(secret: str = 'webhook-secret') -> Mapping[str, str]:
    return {'x-telegram-bot-api-secret-token': secret}


def _update(
    *,
    update_id: int = 11,
    chat_id: int = -100123,
    sender_id: int = 22,
    message_id: int = 33,
    topic_id: int | None = 44,
    text: str = 'hello',
) -> bytes:
    message: dict[str, object] = {
        'message_id': message_id,
        'from': {'id': sender_id, 'is_bot': False},
        'chat': {'id': chat_id, 'type': 'supergroup'},
        'text': text,
    }
    if topic_id is not None:
        message['message_thread_id'] = topic_id
    return json.dumps({'update_id': update_id, 'message': message}).encode()


def _channel(client: httpx.AsyncClient | None = None) -> TelegramChannel:
    return TelegramChannel(
        bot_token='bot-secret',
        webhook_secret='webhook-secret',
        allowed_senders={22},
        client=client,
    )


class TestTelegramChannel:
    async def test_maps_verified_topic_reply_update(self) -> None:
        channel = _channel()

        event = channel.parse_request(
            _update(),
            {'X-TELEGRAM-BOT-API-SECRET-TOKEN': 'webhook-secret'},
        )

        assert event == ChannelEvent(
            event_id='telegram:update:11',
            conversation_id='telegram:chat:-100123:topic:44',
            sender_id='telegram:user:22',
            text='hello',
            reply_to_id='telegram:message:33',
        )

    async def test_rejects_unverified_or_malformed_webhook(self) -> None:
        channel = _channel()

        for headers in ({}, _headers('wrong'), {'X-Telegram-Bot-Api-Secret-Token': 'wrong'}):
            with pytest.raises(TelegramWebhookError, match='secret token'):
                channel.parse_request(_update(), headers)

        with pytest.raises(TelegramWebhookError, match='ambiguous'):
            channel.parse_request(
                _update(),
                {
                    'X-Telegram-Bot-Api-Secret-Token': 'webhook-secret',
                    'x-telegram-bot-api-secret-token': 'webhook-secret',
                },
            )

        for body in (b'not json', b'[]', b'{"update_id":true}', b'{"update_id":-1}', b'{"message":{}}'):
            with pytest.raises(TelegramError, match='payload'):
                channel.parse_request(body, _headers())

    async def test_ignores_unsupported_or_disallowed_updates(self) -> None:
        channel = _channel()

        assert channel.parse_request(b'{"update_id":1,"callback_query":{}}', _headers()) is None
        assert (
            channel.parse_request(
                b'{"update_id":2,"message":{"message_id":3,"from":{"id":22},"chat":{"id":4},"photo":[]}}',
                _headers(),
            )
            is None
        )
        assert channel.parse_request(_update(update_id=3, sender_id=99), _headers()) is None

        bot_message = _BODY_ADAPTER.validate_python(json.loads(_update(update_id=4)))
        message = _BODY_ADAPTER.validate_python(bot_message['message'])
        sender = _BODY_ADAPTER.validate_python(message['from'])
        sender['is_bot'] = True
        message['from'] = sender
        bot_message['message'] = message
        assert channel.parse_request(json.dumps(bot_message).encode(), _headers()) is None

    async def test_maps_anonymous_chat_sender_without_trusting_fake_user(self) -> None:
        body = _BODY_ADAPTER.validate_python(json.loads(_update()))
        message = _BODY_ADAPTER.validate_python(body['message'])
        message['sender_chat'] = {'id': -100999}
        body['message'] = message
        channel = TelegramChannel(
            bot_token='bot-secret',
            webhook_secret='webhook-secret',
            allowed_senders={-100999},
        )

        event = channel.parse_request(json.dumps(body).encode(), _headers())

        assert event is not None
        assert event.sender_id == 'telegram:chat:-100999'

    async def test_rejects_malformed_message_identities(self) -> None:
        channel = _channel()

        malformed_messages = (
            {'message_id': 0, 'from': {'id': 22, 'is_bot': False}, 'chat': {'id': 1}, 'text': 'hello'},
            {'message_id': 1, 'from': {'id': 22, 'is_bot': False}, 'chat': {'id': True}, 'text': 'hello'},
            {
                'message_id': 1,
                'message_thread_id': 0,
                'from': {'id': 22, 'is_bot': False},
                'chat': {'id': 1},
                'text': 'hello',
            },
            {'message_id': 1, 'from': {'id': True, 'is_bot': False}, 'chat': {'id': 1}, 'text': 'hello'},
            {'message_id': 1, 'from': {'id': 22}, 'chat': {'id': 1}, 'text': 'hello'},
            {
                'message_id': 1,
                'sender_chat': 'invalid',
                'from': {'id': 22, 'is_bot': False},
                'chat': {'id': 1},
                'text': 'hello',
            },
        )

        for message in malformed_messages:
            body = json.dumps({'update_id': 1, 'message': message}).encode()
            with pytest.raises(TelegramError, match='identity'):
                channel.parse_request(body, _headers())

    async def test_duplicate_update_is_idempotent_at_admission_boundary(self) -> None:
        requests: list[dict[str, object]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(_BODY_ADAPTER.validate_python(json.loads(request.content)))
            return _response({'ok': True, 'result': {'message_id': 1}})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        channel = _channel(client)
        host = ChannelHost(Agent('test'), channel)
        claimed: set[str] = set()

        for _ in range(2):
            event = channel.parse_request(_update(), _headers())
            assert event is not None
            if event.event_id in claimed:
                continue
            claimed.add(event.event_id)
            await host.handle(event)

        assert claimed == {'telegram:update:11'}
        assert len(requests) == 1
        await client.aclose()

    async def test_sends_to_original_topic_and_message(self) -> None:
        requests: list[dict[str, object]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(_BODY_ADAPTER.validate_python(json.loads(request.content)))
            return _response({'ok': True, 'result': {'message_id': 1}})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        channel = _channel(client)
        event = channel.parse_request(_update(), _headers())
        assert event is not None

        await channel.reply(event, 'answer')

        assert requests == [
            {
                'chat_id': -100123,
                'message_thread_id': 44,
                'reply_parameters': {'message_id': 33},
                'text': 'answer',
            }
        ]
        await client.aclose()

    async def test_chunks_text_at_telegram_limit(self) -> None:
        requests: list[dict[str, object]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(_BODY_ADAPTER.validate_python(json.loads(request.content)))
            return _response({'ok': True, 'result': {'message_id': len(requests)}})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        channel = _channel(client)
        event = ChannelEvent(
            event_id='telegram:update:1',
            conversation_id='telegram:chat:7',
            sender_id='telegram:user:22',
            text='prompt',
        )

        await channel.reply(event, 'a' * 4097)
        await channel.reply(event, '😀' * 2049)

        assert [request['text'] for request in requests] == ['a' * 4096, 'a', '😀' * 2049]
        assert all('message_thread_id' not in request and 'reply_parameters' not in request for request in requests)
        with pytest.raises(ValueError, match='text must not be empty'):
            await channel.reply(event, '')
        await client.aclose()

    async def test_retry_policy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = 0
        sleeps: list[float] = []

        async def sleep(delay: float) -> None:
            sleeps.append(delay)

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls == 1:
                return _response(
                    {
                        'ok': False,
                        'error_code': 429,
                        'description': 'Too Many Requests',
                        'parameters': {'retry_after': 2},
                    },
                    status_code=429,
                )
            return _response({'ok': True, 'result': {'message_id': 1}})

        monkeypatch.setattr('pydantic_ai_harness.channels.telegram.anyio.sleep', sleep)
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        channel = _channel(client)
        event = channel.parse_request(_update(), _headers())
        assert event is not None

        await channel.reply(event, 'answer')

        assert calls == 2
        assert sleeps == [2.0]
        await client.aclose()

    async def test_does_not_retry_ambiguous_transport_failure(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            raise httpx.ReadError('connection lost', request=request)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        channel = _channel(client)
        event = channel.parse_request(_update(), _headers())
        assert event is not None

        with pytest.raises(TelegramError, match='request failed') as exc_info:
            await channel.reply(event, 'answer')

        assert calls == 1
        assert exc_info.value.__cause__ is None
        assert 'bot-secret' not in str(exc_info.value)
        await client.aclose()

    async def test_rejects_second_rate_limit_and_malformed_retry_control(self, monkeypatch: pytest.MonkeyPatch) -> None:
        retry_after: object = 0
        calls = 0

        async def sleep(_delay: float) -> None:
            return None

        def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return _response(
                {
                    'ok': False,
                    'error_code': 429,
                    'description': 'Too Many Requests',
                    'parameters': {'retry_after': retry_after},
                },
                status_code=429,
            )

        monkeypatch.setattr('pydantic_ai_harness.channels.telegram.anyio.sleep', sleep)
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        channel = _channel(client)
        event = channel.parse_request(_update(), _headers())
        assert event is not None

        with pytest.raises(TelegramError, match='Too Many Requests'):
            await channel.reply(event, 'first')
        assert calls == 2

        retry_after = False
        with pytest.raises(TelegramError, match='Too Many Requests'):
            await channel.reply(event, 'second')
        assert calls == 3

        retry_after = 61
        with pytest.raises(TelegramError, match='Too Many Requests'):
            await channel.reply(event, 'third')
        assert calls == 4
        await client.aclose()

    async def test_rejects_invalid_provider_responses_and_event_identity(self) -> None:
        responses = iter(
            (
                httpx.Response(200, content=b'not json'),
                _response([], status_code=500),
                _response({'ok': False}, status_code=401),
                _response({'ok': True}, status_code=500),
            )
        )

        def handler(_request: httpx.Request) -> httpx.Response:
            return next(responses)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        channel = _channel(client)
        event = ChannelEvent(
            event_id='telegram:update:1',
            conversation_id='telegram:chat:7',
            sender_id='telegram:user:22',
            text='prompt',
        )

        for _ in range(4):
            with pytest.raises(TelegramError):
                await channel.reply(event, 'answer')

        with pytest.raises(TelegramError, match='conversation_id'):
            await channel.reply(
                ChannelEvent(
                    event_id='telegram:update:1',
                    conversation_id='slack:channel:7',
                    sender_id='telegram:user:22',
                    text='prompt',
                ),
                'answer',
            )
        with pytest.raises(TelegramError, match='reply_to_id'):
            await channel.reply(
                ChannelEvent(
                    event_id='telegram:update:1',
                    conversation_id='telegram:chat:7',
                    sender_id='telegram:user:22',
                    text='prompt',
                    reply_to_id='slack:message:2',
                ),
                'answer',
            )
        await client.aclose()

    async def test_uses_custom_api_url(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return _response({'ok': True})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        channel = TelegramChannel(
            bot_token='bot-secret',
            webhook_secret='webhook-secret',
            allowed_senders={22},
            client=client,
            api_url='https://telegram.test/root/',
        )
        event = channel.parse_request(_update(), _headers())
        assert event is not None

        await channel.reply(event, 'answer')

        assert str(requests[0].url) == 'https://telegram.test/root/botbot-secret/sendMessage'
        await client.aclose()

    async def test_bot_token_is_redacted(self, caplog: pytest.LogCaptureFixture) -> None:
        calls = 0

        def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls == 1:
                return _response({'ok': True, 'result': {'message_id': 1}})
            return _response(
                {
                    'ok': False,
                    'error_code': 401,
                    'description': 'Unauthorized https://api.telegram.org/botbot-secret/sendMessage',
                },
                status_code=401,
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        channel = _channel(client)
        event = channel.parse_request(_update(), _headers())
        assert event is not None

        with caplog.at_level(logging.INFO, logger='httpx'):
            await channel.reply(event, 'first')
            with pytest.raises(TelegramError, match='Unauthorized') as exc_info:
                await channel.reply(event, 'second')

        assert 'bot-secret' not in repr(channel)
        assert 'webhook-secret' not in repr(channel)
        assert 'bot-secret' not in str(exc_info.value)
        assert 'bot-secret' not in caplog.text
        assert '<redacted>' in caplog.text
        await client.aclose()

    async def test_agent_integration(self) -> None:
        requests: list[dict[str, object]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(_BODY_ADAPTER.validate_python(json.loads(request.content)))
            return _response({'ok': True, 'result': {'message_id': 1}})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        channel = _channel(client)
        event = channel.parse_request(_update(text='run the agent'), _headers())
        assert event is not None

        result = await ChannelHost(Agent('test'), channel).handle(event)

        assert result.output == 'success (no tool calls)'
        assert requests[0]['text'] == result.output
        assert all(message.conversation_id == event.conversation_id for message in result.all_messages())
        await client.aclose()

    async def test_validates_configuration(self) -> None:
        with pytest.raises(ValueError, match='bot_token'):
            TelegramChannel(bot_token='', webhook_secret='secret', allowed_senders={22})
        with pytest.raises(ValueError, match='webhook_secret'):
            TelegramChannel(bot_token='token', webhook_secret='', allowed_senders={22})
        with pytest.raises(ValueError, match='webhook_secret'):
            TelegramChannel(bot_token='token', webhook_secret='contains spaces', allowed_senders={22})
        with pytest.raises(ValueError, match='allowed_senders'):
            TelegramChannel(bot_token='token', webhook_secret='secret', allowed_senders=set())
        with pytest.raises(TypeError, match='allowed_senders'):
            TelegramChannel(bot_token='token', webhook_secret='secret', allowed_senders='22')  # type: ignore[arg-type]
        with pytest.raises(TypeError, match='allowed_senders'):
            TelegramChannel(bot_token='token', webhook_secret='secret', allowed_senders={True})
        with pytest.raises(ValueError, match='api_url'):
            TelegramChannel(bot_token='token', webhook_secret='secret', allowed_senders={22}, api_url='')
