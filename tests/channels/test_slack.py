from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Mapping

import anyio
import httpx
import pytest

from pydantic_ai_harness.channels import ChannelEvent
from pydantic_ai_harness.channels.slack import (
    SlackAPIError,
    SlackChannel,
    SlackError,
    SlackSignatureError,
    SlackUrlVerification,
)


def _body(event: object | None = None, **overrides: object) -> bytes:
    payload: dict[str, object] = {
        'type': 'event_callback',
        'team_id': 'T123',
        'event_id': 'Ev123',
        'event': {
            'type': 'app_mention',
            'channel': 'C123',
            'user': 'U123',
            'text': '<@BOT> hello',
            'ts': '1700000000.000001',
        },
    }
    if event is not None:
        payload['event'] = event
    payload.update(overrides)
    return json.dumps(payload, separators=(',', ':')).encode()


def _headers(body: bytes, *, secret: str = 'signing-secret', timestamp: int | None = None) -> dict[str, str]:
    timestamp = int(time.time()) if timestamp is None else timestamp
    timestamp_text = str(timestamp)
    signed = b'v0:' + timestamp_text.encode() + b':' + body
    signature = 'v0=' + hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return {'X-Slack-Request-Timestamp': timestamp_text, 'x-SLACK-signature': signature}


class TestSlackRequestParsing:
    @pytest.mark.parametrize('field', ['signing_secret', 'bot_token', 'team_id'])
    def test_rejects_empty_credentials(self, field: str) -> None:
        with pytest.raises(ValueError, match=field):
            if field == 'signing_secret':
                SlackChannel(signing_secret='', bot_token='token', team_id='team')
            elif field == 'bot_token':
                SlackChannel(signing_secret='secret', bot_token='', team_id='team')
            else:
                SlackChannel(signing_secret='secret', bot_token='token', team_id='')

    def test_normalizes_root_message_and_thread_reply(self) -> None:
        channel = SlackChannel(signing_secret='signing-secret', bot_token='xoxb-secret', team_id='T123')
        root_body = _body()
        thread_body = _body(
            {
                'type': 'message',
                'channel': 'C123',
                'user': 'U456',
                'text': 'follow up',
                'ts': '1700000001.000001',
                'thread_ts': '1700000000.000001',
            },
            event_id='Ev456',
        )
        other_root_body = _body(
            {
                'type': 'message',
                'channel': 'C123',
                'user': 'U789',
                'text': 'separate root',
                'ts': '1700000099.000001',
            },
            event_id='Ev789',
        )

        root = channel.parse_request(root_body, _headers(root_body))
        thread = channel.parse_request(thread_body, _headers(thread_body))
        other_root = channel.parse_request(other_root_body, _headers(other_root_body))
        assert root == ChannelEvent(
            event_id='Ev123',
            conversation_id='slack:T123:C123:1700000000.000001',
            sender_id='U123',
            text='<@BOT> hello',
            reply_to_id='1700000000.000001',
            delivery_id='C123',
        )
        assert thread == ChannelEvent(
            event_id='Ev456',
            conversation_id='slack:T123:C123:1700000000.000001',
            sender_id='U456',
            text='follow up',
            reply_to_id='1700000000.000001',
            delivery_id='C123',
        )
        assert isinstance(other_root, ChannelEvent)
        assert other_root.conversation_id == 'slack:T123:C123:1700000099.000001'

    def test_rejects_event_for_another_workspace_installation(self) -> None:
        body = _body(team_id='T999')
        channel = SlackChannel(signing_secret='signing-secret', bot_token='xoxb-secret', team_id='T123')

        with pytest.raises(SlackError, match='workspace installation'):
            channel.parse_request(body, _headers(body))

    def test_conversation_identity_isolated_by_workspace_and_channel(self) -> None:
        original_body = _body()
        other_workspace_body = _body(team_id='T999')
        other_channel_body = _body(
            {
                'type': 'app_mention',
                'channel': 'C999',
                'user': 'U123',
                'text': '<@BOT> hello',
                'ts': '1700000000.000001',
            }
        )
        original = SlackChannel(signing_secret='signing-secret', bot_token='token', team_id='T123').parse_request(
            original_body, _headers(original_body)
        )
        other_workspace = SlackChannel(
            signing_secret='signing-secret', bot_token='token', team_id='T999'
        ).parse_request(other_workspace_body, _headers(other_workspace_body))
        other_channel = SlackChannel(signing_secret='signing-secret', bot_token='token', team_id='T123').parse_request(
            other_channel_body, _headers(other_channel_body)
        )

        assert isinstance(original, ChannelEvent)
        assert isinstance(other_workspace, ChannelEvent)
        assert isinstance(other_channel, ChannelEvent)
        assert len({original.conversation_id, other_workspace.conversation_id, other_channel.conversation_id}) == 3

    def test_returns_url_verification_challenge(self) -> None:
        body = _body(type='url_verification', challenge='challenge-value')
        channel = SlackChannel(signing_secret='signing-secret', bot_token='xoxb-secret', team_id='T123')

        assert channel.parse_request(body, _headers(body)) == SlackUrlVerification('challenge-value')

    def test_ignores_unknown_request_type(self) -> None:
        body = _body(type='unknown')
        channel = SlackChannel(signing_secret='signing-secret', bot_token='xoxb-secret', team_id='T123')

        assert channel.parse_request(body, _headers(body)) is None

    @pytest.mark.parametrize(
        'event',
        [
            {'type': 'reaction_added'},
            {'type': 'message', 'bot_id': 'B123'},
            {'type': 'message', 'subtype': 'message_changed'},
        ],
    )
    def test_ignores_unsupported_and_bot_events(self, event: Mapping[str, object]) -> None:
        body = _body(event)
        channel = SlackChannel(signing_secret='signing-secret', bot_token='xoxb-secret', team_id='T123')

        assert channel.parse_request(body, _headers(body)) is None

    def test_rejects_missing_malformed_stale_and_wrong_signatures(self) -> None:
        body = _body()
        channel = SlackChannel(signing_secret='signing-secret', bot_token='xoxb-secret', team_id='T123')

        with pytest.raises(SlackSignatureError, match='Missing'):
            channel.parse_request(body, {})
        with pytest.raises(SlackSignatureError, match='Missing'):
            channel.parse_request(body, {'x-slack-request-timestamp': str(int(time.time()))})
        with pytest.raises(SlackSignatureError, match='timestamp'):
            channel.parse_request(body, {'x-slack-request-timestamp': 'nope', 'x-slack-signature': 'v0=x'})
        with pytest.raises(SlackSignatureError, match='five-minute'):
            channel.parse_request(body, _headers(body) | {'x-slack-request-timestamp': '1' + '0' * 400})
        with pytest.raises(SlackSignatureError, match='five-minute'):
            channel.parse_request(body, _headers(body, timestamp=int(time.time()) - 301))
        with pytest.raises(SlackSignatureError, match='signature'):
            channel.parse_request(body + b' ', _headers(body))

    @pytest.mark.parametrize('body', [b'not-json', b'[]', _body(event_id=123), _body(event='bad')])
    def test_rejects_malformed_payloads(self, body: bytes) -> None:
        channel = SlackChannel(signing_secret='signing-secret', bot_token='xoxb-secret', team_id='T123')

        with pytest.raises(SlackError):
            channel.parse_request(body, _headers(body))

    @pytest.mark.parametrize('thread_timestamp', ['', 123])
    def test_rejects_malformed_explicit_thread_timestamp(self, thread_timestamp: object) -> None:
        body = _body(
            {
                'type': 'message',
                'channel': 'C123',
                'user': 'U123',
                'text': 'hello',
                'ts': '1700000000.000001',
                'thread_ts': thread_timestamp,
            }
        )
        channel = SlackChannel(signing_secret='signing-secret', bot_token='xoxb-secret', team_id='T123')

        with pytest.raises(SlackError, match='thread_ts'):
            channel.parse_request(body, _headers(body))

    @pytest.mark.parametrize(
        ('body', 'field'),
        [
            (_body(team_id=''), 'team_id'),
            (_body(event_id=''), 'event_id'),
            (
                _body({'type': 'message', 'channel': '', 'user': 'U123', 'text': 'hello', 'ts': '1700000000.1'}),
                'channel',
            ),
            (
                _body({'type': 'message', 'channel': 'C123', 'user': '', 'text': 'hello', 'ts': '1700000000.1'}),
                'user',
            ),
            (_body({'type': 'message', 'channel': 'C123', 'user': 'U123', 'text': 'hello', 'ts': ''}), 'ts'),
        ],
    )
    def test_rejects_empty_identity_fields(self, body: bytes, field: str) -> None:
        channel = SlackChannel(signing_secret='signing-secret', bot_token='xoxb-secret', team_id='T123')

        with pytest.raises(SlackError, match=field):
            channel.parse_request(body, _headers(body))


@pytest.mark.anyio
class TestSlackReply:
    async def test_posts_to_slack_endpoint_with_original_thread(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={'ok': True})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            channel = SlackChannel(
                signing_secret='signing-secret',
                bot_token='xoxb-secret',
                team_id='T123',
                client=client,
            )
            await channel.reply(
                ChannelEvent(
                    event_id='Ev123',
                    conversation_id='slack:T123:C123:1700000000.000001',
                    sender_id='U123',
                    text='hello',
                    reply_to_id='1700000000.000001',
                    delivery_id='C123',
                ),
                'agent reply',
            )
            assert not client.is_closed

        assert len(seen) == 1
        assert seen[0].url == 'https://slack.com/api/chat.postMessage'
        assert seen[0].headers['authorization'] == 'Bearer xoxb-secret'
        assert json.loads(seen[0].content) == {
            'channel': 'C123',
            'text': 'agent reply',
            'thread_ts': '1700000000.000001',
        }

    async def test_uses_conversation_as_delivery_fallback(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={'ok': True})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            channel = SlackChannel(signing_secret='secret', bot_token='token', team_id='team', client=client)
            await channel.reply(
                ChannelEvent(event_id='e', conversation_id='C-fallback', sender_id='u', text='x'), 'reply'
            )

        assert json.loads(seen[0].content)['channel'] == 'C-fallback'

    async def test_surfaces_http_and_slack_api_errors(self) -> None:
        responses = iter(
            [
                httpx.Response(503),
                httpx.Response(200, json={'ok': False, 'error': 'not_in_channel'}),
                httpx.Response(200, content=b'not-json'),
                httpx.Response(200, json={'ok': False}),
            ]
        )

        def handler(_request: httpx.Request) -> httpx.Response:
            return next(responses)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            channel = SlackChannel(signing_secret='secret', bot_token='token', team_id='team', client=client)
            event = ChannelEvent(event_id='e', conversation_id='c', sender_id='u', text='x')

            with pytest.raises(httpx.HTTPStatusError):
                await channel.reply(event, 'first')
            with pytest.raises(SlackAPIError, match='not_in_channel'):
                await channel.reply(event, 'second')
            with pytest.raises(SlackAPIError, match='invalid response'):
                await channel.reply(event, 'third')
            with pytest.raises(SlackAPIError, match='unknown_error'):
                await channel.reply(event, 'fourth')

    async def test_closes_short_lived_default_client(self, monkeypatch: pytest.MonkeyPatch) -> None:
        clients: list[httpx.AsyncClient] = []
        async_client_type = httpx.AsyncClient

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={'ok': True})

        def client_factory() -> httpx.AsyncClient:
            client = async_client_type(transport=httpx.MockTransport(handler))
            clients.append(client)
            return client

        monkeypatch.setattr(httpx, 'AsyncClient', client_factory)
        channel = SlackChannel(signing_secret='secret', bot_token='token', team_id='team')

        await channel.reply(
            ChannelEvent(event_id='e', conversation_id='c', sender_id='u', text='x'),
            'reply',
        )

        assert len(clients) == 1
        assert clients[0].is_closed

    async def test_closes_short_lived_default_client_after_http_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        clients: list[httpx.AsyncClient] = []
        async_client_type = httpx.AsyncClient

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(503)

        def client_factory() -> httpx.AsyncClient:
            client = async_client_type(transport=httpx.MockTransport(handler))
            clients.append(client)
            return client

        monkeypatch.setattr(httpx, 'AsyncClient', client_factory)
        channel = SlackChannel(signing_secret='secret', bot_token='token', team_id='team')

        with pytest.raises(httpx.HTTPStatusError):
            await channel.reply(ChannelEvent(event_id='e', conversation_id='c', sender_id='u', text='x'), 'reply')

        assert len(clients) == 1
        assert clients[0].is_closed

    async def test_closes_short_lived_default_client_after_cancellation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        clients: list[httpx.AsyncClient] = []
        async_client_type = httpx.AsyncClient
        started = anyio.Event()

        async def handler(_request: httpx.Request) -> httpx.Response:
            started.set()
            await anyio.Event().wait()
            return httpx.Response(200, json={'ok': True})

        def client_factory() -> httpx.AsyncClient:
            client = async_client_type(transport=httpx.MockTransport(handler))
            clients.append(client)
            return client

        monkeypatch.setattr(httpx, 'AsyncClient', client_factory)
        channel = SlackChannel(signing_secret='secret', bot_token='token', team_id='team')

        cancel_scope = anyio.CancelScope()

        async def run_reply() -> None:
            with cancel_scope:
                await channel.reply(ChannelEvent(event_id='e', conversation_id='c', sender_id='u', text='x'), 'reply')

        async with anyio.create_task_group() as group:
            group.start_soon(run_reply)
            await started.wait()
            cancel_scope.cancel()

        assert len(clients) == 1
        assert clients[0].is_closed


def test_secrets_are_absent_from_repr() -> None:
    representation = repr(SlackChannel(signing_secret='signing-secret', bot_token='xoxb-secret', team_id='T123'))
    assert 'signing-secret' not in representation
    assert 'xoxb-secret' not in representation
