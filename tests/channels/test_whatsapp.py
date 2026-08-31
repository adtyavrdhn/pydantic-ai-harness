from __future__ import annotations

import asyncio
import hashlib
import hmac
import json

import httpx
import pytest
from pydantic import TypeAdapter

from pydantic_ai_harness.channels import ChannelError, WebhookRequest
from pydantic_ai_harness.channels.whatsapp import WhatsAppChannel

_BODY_ADAPTER = TypeAdapter(dict[str, object])
_APP_SECRET = 'app-secret'
_PHONE_ID = '123456789'


def response(payload: object, *, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, json=payload)


def signed_request(payload: object, *, secret: str = _APP_SECRET, method: str = 'POST') -> WebhookRequest:
    body = json.dumps(payload, separators=(',', ':')).encode()
    signature = 'sha256=' + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return WebhookRequest(
        method=method,
        headers={'X-Hub-Signature-256': signature},
        query={},
        body=body,
    )


def challenge_request(*, token: str = 'verify-token', challenge: str = 'challenge') -> WebhookRequest:
    return WebhookRequest(
        method='GET',
        headers={},
        query={
            'hub.mode': 'subscribe',
            'hub.verify_token': token,
            'hub.challenge': challenge,
        },
        body=b'',
    )


def webhook_payload(messages: object, *, phone_number_id: str = _PHONE_ID) -> dict[str, object]:
    return {
        'object': 'whatsapp_business_account',
        'entry': [
            {
                'id': 'WABA1',
                'changes': [
                    {
                        'field': 'messages',
                        'value': {
                            'metadata': {'phone_number_id': phone_number_id},
                            'messages': messages,
                        },
                    }
                ],
            }
        ],
    }


def text_message(message_id: str = 'wamid.1', *, body: str = 'hello', sender: str = '15551234567') -> dict[str, object]:
    return {
        'from': sender,
        'id': message_id,
        'type': 'text',
        'text': {'body': body},
    }


@pytest.mark.anyio
class TestWhatsAppChannel:
    async def test_verifies_challenge(self) -> None:
        channel = WhatsAppChannel('token', _PHONE_ID, _APP_SECRET, 'verify-token')
        accepted = channel.handle_webhook(challenge_request())
        assert (accepted.status_code, accepted.body) == (200, 'challenge')

        assert channel.handle_webhook(challenge_request(token='wrong')).status_code == 403
        missing_challenge = WebhookRequest(
            method='GET',
            headers={},
            query={'hub.mode': 'subscribe', 'hub.verify_token': 'verify-token'},
            body=b'',
        )
        assert channel.handle_webhook(missing_challenge).status_code == 403
        wrong_mode = WebhookRequest(
            method='GET',
            headers={},
            query={
                'hub.mode': 'unsubscribe',
                'hub.verify_token': 'verify-token',
                'hub.challenge': 'challenge',
            },
            body=b'',
        )
        assert channel.handle_webhook(wrong_mode).status_code == 403
        assert channel.handle_webhook(signed_request({}, method='PUT')).status_code == 405

    async def test_normalizes_batched_text_and_suppresses_duplicates(self) -> None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response({})))
        channel = WhatsAppChannel(
            'token',
            _PHONE_ID,
            _APP_SECRET,
            'verify-token',
            http_client=client,
            max_queued_messages=2,
        )
        request = signed_request(
            webhook_payload(
                [
                    text_message('wamid.1', body='first'),
                    text_message('wamid.2', body='second'),
                ]
            )
        )

        async with channel:
            assert channel.handle_webhook(request).status_code == 200
            assert channel.handle_webhook(request).status_code == 200
            first = await anext(channel.messages())
            second = await anext(channel.messages())

        assert (first.conversation_id, first.sender_id, first.message_id, first.text) == (
            '15551234567',
            '15551234567',
            'wamid.1',
            'first',
        )
        assert second.message_id == 'wamid.2'
        assert client.is_closed is False
        await client.aclose()

    async def test_rejects_invalid_signatures_and_payloads(self) -> None:
        channel = WhatsAppChannel('token', _PHONE_ID, _APP_SECRET, 'verify-token')
        assert channel.handle_webhook(WebhookRequest('POST', {}, {}, b'{}')).status_code == 401

        non_ascii = WebhookRequest(
            'POST',
            {'x-hub-signature-256': 'sha256=é'},
            {},
            b'{}',
        )
        assert channel.handle_webhook(non_ascii).status_code == 401
        assert channel.handle_webhook(signed_request({}, secret='wrong')).status_code == 401

        malformed_body = b'not json'
        malformed_signature = 'sha256=' + hmac.new(_APP_SECRET.encode(), malformed_body, hashlib.sha256).hexdigest()
        malformed = WebhookRequest(
            'POST',
            {'x-hub-signature-256': malformed_signature},
            {},
            malformed_body,
        )
        assert channel.handle_webhook(malformed).status_code == 400
        assert channel.handle_webhook(signed_request({'object': 'other'})).status_code == 200

    async def test_filters_statuses_other_numbers_and_malformed_messages(self) -> None:
        channel = WhatsAppChannel('token', _PHONE_ID, _APP_SECRET, 'verify-token')
        payload: dict[str, object] = {
            'object': 'whatsapp_business_account',
            'entry': [
                123,
                {'changes': 'invalid'},
                {
                    'changes': [
                        123,
                        {'field': 'statuses', 'value': {}},
                        {'field': 'messages', 'value': 123},
                        {
                            'field': 'messages',
                            'value': {
                                'metadata': {'phone_number_id': 'other'},
                                'messages': [text_message('ignored')],
                            },
                        },
                        {
                            'field': 'messages',
                            'value': {
                                'metadata': {'phone_number_id': _PHONE_ID},
                                'messages': [
                                    123,
                                    {'type': 'image'},
                                    {**text_message('empty'), 'text': {'body': ''}},
                                    {**text_message('bad-sender'), 'from': 123},
                                    text_message('accepted'),
                                ],
                            },
                        },
                    ]
                },
            ],
        }

        async with channel:
            assert channel.handle_webhook(signed_request(payload)).status_code == 200
            assert (await anext(channel.messages())).message_id == 'accepted'

            invalid_entries = webhook_payload('invalid')
            assert channel.handle_webhook(signed_request(invalid_entries)).status_code == 200
            assert (
                channel.handle_webhook(
                    signed_request({'object': 'whatsapp_business_account', 'entry': 'invalid'})
                ).status_code
                == 200
            )

    async def test_queue_full_leaves_message_retryable(self) -> None:
        channel = WhatsAppChannel(
            'token',
            _PHONE_ID,
            _APP_SECRET,
            'verify-token',
            max_queued_messages=1,
        )
        first = signed_request(webhook_payload([text_message('wamid.1', body='first')]))
        second = signed_request(webhook_payload([text_message('wamid.2', body='second')]))

        assert channel.handle_webhook(first).status_code == 503
        async with channel:
            assert channel.handle_webhook(first).status_code == 200
            assert channel.handle_webhook(second).status_code == 503
            assert (await anext(channel.messages())).text == 'first'
            assert channel.handle_webhook(second).status_code == 200
            assert (await anext(channel.messages())).text == 'second'
        assert channel.handle_webhook(second).status_code == 503

    async def test_chunks_messages_and_retries_one_throttling_error(self) -> None:
        posts: list[dict[str, object]] = []
        paths: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            paths.append(request.url.path)
            posts.append(_BODY_ADAPTER.validate_python(json.loads(request.content)))
            if len(posts) == 1:
                return response(
                    {
                        'error': {
                            'code': 130429,
                            'message': 'Rate limit hit',
                        }
                    },
                    status_code=429,
                )
            return response({'messages': [{'id': f'wamid.{len(posts)}'}]})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        channel = WhatsAppChannel(
            'token',
            _PHONE_ID,
            _APP_SECRET,
            'verify-token',
            http_client=client,
            api_version='v24.0',
            retry_delay=0,
        )

        async with channel:
            await channel.send_text('15551234567', 'a' * 4097)

        assert [post['text'] for post in posts] == [
            {'body': 'a' * 4096},
            {'body': 'a' * 4096},
            {'body': 'a'},
        ]
        assert all(post['to'] == '15551234567' for post in posts)
        assert paths == [
            f'/v24.0/{_PHONE_ID}/messages',
            f'/v24.0/{_PHONE_ID}/messages',
            f'/v24.0/{_PHONE_ID}/messages',
        ]
        await client.aclose()

    async def test_surfaces_service_window_and_unknown_api_errors(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls == 1:
                return response(
                    {
                        'error': {
                            'code': 131047,
                            'message': 'Re-engagement message',
                        }
                    },
                    status_code=400,
                )
            return response({})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        channel = WhatsAppChannel('token', _PHONE_ID, _APP_SECRET, 'verify-token', http_client=client)

        async with channel:
            with pytest.raises(ChannelError, match='Re-engagement message'):
                await channel.send_text('15551234567', 'outside window')
            with pytest.raises(ChannelError, match='unknown WhatsApp Cloud API error'):
                await channel.send_text('15551234567', 'unknown')

        assert calls == 2
        await client.aclose()

    async def test_surfaces_invalid_and_transport_responses_without_token(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls == 1:
                return httpx.Response(500, content=b'not json')
            raise httpx.ConnectError('offline', request=request)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        channel = WhatsAppChannel(
            'secret-token',
            _PHONE_ID,
            _APP_SECRET,
            'verify-token',
            http_client=client,
        )

        async with channel:
            with pytest.raises(ChannelError, match='invalid response'):
                await channel.send_text('15551234567', 'invalid')
            with pytest.raises(ChannelError, match='request failed') as exc_info:
                await channel.send_text('15551234567', 'offline')

        assert exc_info.value.__suppress_context__ is True
        assert 'secret-token' not in str(exc_info.value)
        await client.aclose()

    async def test_owned_client_closes_and_webhooks_require_open_channel(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client_class = httpx.AsyncClient
        client = client_class(transport=httpx.MockTransport(lambda request: response({})))
        monkeypatch.setattr(httpx, 'AsyncClient', lambda: client)
        channel = WhatsAppChannel('token', _PHONE_ID, _APP_SECRET, 'verify-token')
        request = signed_request(webhook_payload([text_message()]))

        assert channel.handle_webhook(request).status_code == 503
        async with channel:
            assert channel.handle_webhook(request).status_code == 200
        assert client.is_closed
        assert channel.handle_webhook(request).status_code == 503

    async def test_closing_channel_ends_pending_message_iterator(self) -> None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response({})))
        channel = WhatsAppChannel('token', _PHONE_ID, _APP_SECRET, 'verify-token', http_client=client)
        async with channel:
            pending_message = asyncio.ensure_future(anext(channel.messages()))
            await asyncio.sleep(0)

        with pytest.raises(StopAsyncIteration):
            await pending_message

        async with channel:
            assert channel.handle_webhook(signed_request(webhook_payload([text_message()]))).status_code == 200
            assert (await anext(channel.messages())).message_id == 'wamid.1'
        await client.aclose()

    async def test_requires_open_channel_and_nonempty_text(self) -> None:
        channel = WhatsAppChannel('token', _PHONE_ID, _APP_SECRET, 'verify-token')
        with pytest.raises(RuntimeError, match='opened'):
            await channel.send_text('15551234567', 'hello')

        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response({})))
        channel = WhatsAppChannel('token', _PHONE_ID, _APP_SECRET, 'verify-token', http_client=client)
        async with channel:
            with pytest.raises(ValueError, match='text'):
                await channel.send_text('15551234567', '')
            with pytest.raises(RuntimeError, match='already open'):
                await channel.__aenter__()
        await client.aclose()

    def test_validates_configuration(self) -> None:
        base: tuple[str, str, str, str] = ('token', _PHONE_ID, _APP_SECRET, 'verify-token')
        for index, name in enumerate(('access_token', 'phone_number_id', 'app_secret', 'verify_token')):
            values: list[str] = list(base)
            values[index] = ''
            with pytest.raises(ValueError, match=name):
                WhatsAppChannel(*values)

        with pytest.raises(ValueError, match='api_version'):
            WhatsAppChannel(*base, api_version='latest')
        for value in (0, True):
            with pytest.raises(ValueError, match='max_queued_messages'):
                WhatsAppChannel(*base, max_queued_messages=value)
        with pytest.raises(ValueError, match='retry_delay'):
            WhatsAppChannel(*base, retry_delay=-1)
        with pytest.raises(ValueError, match='retry_delay'):
            WhatsAppChannel(*base, retry_delay=float('nan'))
