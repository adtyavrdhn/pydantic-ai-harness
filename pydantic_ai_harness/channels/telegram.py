"""Telegram Bot API adapter for the provider-neutral Channels host.

External assumptions verified 2026-09-04 against https://core.telegram.org/bots/api:

* webhook requests carry the configured secret in `X-Telegram-Bot-Api-Secret-Token`;
* `update_id` identifies deliveries, while messages identify their chat, optional topic, and source message;
* Bot API requests use `https://api.telegram.org/bot<token>/METHOD_NAME`;
* `sendMessage` accepts 1 to 4096 characters and supports topics and reply parameters;
* flood-control responses may include an integer `parameters.retry_after` delay.

Re-check the linked primary documentation before changing authentication, identity, or retry policy.
"""

from __future__ import annotations

import hmac
import logging
import re
from collections.abc import Collection, Mapping
from typing import TypeGuard

import anyio
import httpx
from pydantic import TypeAdapter, ValidationError

from ._host import ChannelEvent

_DEFAULT_API_URL = 'https://api.telegram.org'
_MAX_TEXT_CHARS = 4096
_MAX_RETRY_AFTER_SECONDS = 60
_SECRET_PATTERN = re.compile(r'[A-Za-z0-9_-]{1,256}')
_CONVERSATION_PATTERN = re.compile(r'telegram:chat:(-?[0-9]+)(?::topic:([0-9]+))?')
_MESSAGE_PATTERN = re.compile(r'telegram:message:([0-9]+)')
_TELEGRAM_URL_PATTERN = re.compile(r'(https?://[^/\s]+/bot)[^/\s]+(/[^\s]*)?')
_JSON_ADAPTER: TypeAdapter[object] = TypeAdapter(object)
_MAPPING_ADAPTER: TypeAdapter[dict[str, object]] = TypeAdapter(dict[str, object])


class TelegramError(RuntimeError):
    """A Telegram webhook payload or Bot API request is invalid."""


class TelegramWebhookError(TelegramError):
    """A Telegram webhook request fails secret-token verification."""


class _TelegramTokenFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = _TELEGRAM_URL_PATTERN.sub(r'\1<redacted>\2', message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


_TOKEN_FILTER = _TelegramTokenFilter()


class _RateLimited(TelegramError):
    def __init__(self, message: str, *, retry_after: float) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class TelegramChannel:
    """Translate verified Telegram updates and send replies through the Bot API."""

    def __init__(
        self,
        *,
        bot_token: str,
        webhook_secret: str,
        allowed_senders: Collection[int],
        client: httpx.AsyncClient | None = None,
        api_url: str = _DEFAULT_API_URL,
    ) -> None:
        """Configure Telegram authentication, admission, and HTTP transport.

        Args:
            bot_token: Token issued by BotFather for Bot API requests.
            webhook_secret: Secret configured through Telegram's `setWebhook` method.
            allowed_senders: Telegram user or sender-chat IDs allowed to start runs.
            client: Optional pooled HTTP client. The caller retains ownership.
            api_url: Bot API server base URL.
        """
        if not bot_token:
            raise ValueError('bot_token must not be empty')
        if _SECRET_PATTERN.fullmatch(webhook_secret) is None:
            raise ValueError('webhook_secret must contain 1-256 ASCII letters, digits, underscores, or hyphens')
        if isinstance(allowed_senders, str):
            raise TypeError('allowed_senders must be a collection of integer Telegram IDs')
        sender_values: Collection[object] = allowed_senders
        if any(not _is_integer_id(sender_id) for sender_id in sender_values):
            raise TypeError('allowed_senders must contain only integer Telegram IDs')
        sender_ids = frozenset(allowed_senders)
        if not sender_ids:
            raise ValueError('allowed_senders must contain at least one Telegram ID')
        if not api_url:
            raise ValueError('api_url must not be empty')

        self._bot_token = bot_token
        self._webhook_secret = webhook_secret
        self._allowed_senders = sender_ids
        self._client = client
        self._api_url = api_url.rstrip('/')

        httpx_logger = logging.getLogger('httpx')
        if _TOKEN_FILTER not in httpx_logger.filters:
            httpx_logger.addFilter(_TOKEN_FILTER)

    def parse_request(self, raw_body: bytes, headers: Mapping[str, str]) -> ChannelEvent | None:
        """Verify and normalize one caller-owned Telegram webhook request.

        Unsupported update types, non-text messages, bot messages, and disallowed senders return
        `None`. The caller should atomically claim the returned `event_id` before passing the event
        to [`ChannelHost.handle`][pydantic_ai_harness.channels.ChannelHost.handle].
        """
        secret_values = [
            value for name, value in headers.items() if name.casefold() == 'x-telegram-bot-api-secret-token'
        ]
        if len(secret_values) > 1:
            raise TelegramWebhookError('Telegram webhook has ambiguous secret token headers')
        if len(secret_values) != 1 or not hmac.compare_digest(secret_values[0], self._webhook_secret):
            raise TelegramWebhookError('Telegram webhook secret token is missing or invalid')

        try:
            payload = _mapping(_JSON_ADAPTER.validate_json(raw_body))
        except ValidationError:
            payload = None
        if payload is None:
            raise TelegramError('Telegram webhook payload must be a JSON object')

        update_id = payload.get('update_id')
        if isinstance(update_id, bool) or not isinstance(update_id, int) or update_id < 0:
            raise TelegramError('Telegram webhook payload has an invalid update_id')

        raw_message = payload.get('message')
        if raw_message is None:
            return None
        message = _mapping(raw_message)
        if message is None:
            raise TelegramError('Telegram webhook payload has an invalid message')

        text = message.get('text')
        if not isinstance(text, str) or not text:
            return None

        chat = _mapping(message.get('chat'))
        message_id = message.get('message_id')
        if chat is None or not _is_integer_id(message_id) or message_id <= 0:
            raise TelegramError('Telegram webhook payload has invalid message identity')
        chat_id = chat.get('id')
        if not _is_integer_id(chat_id):
            raise TelegramError('Telegram webhook payload has invalid chat identity')

        topic_id = message.get('message_thread_id')
        if topic_id is not None and (not _is_integer_id(topic_id) or topic_id <= 0):
            raise TelegramError('Telegram webhook payload has invalid topic identity')

        sender = self._sender(message)
        if sender is None:
            return None
        sender_kind, sender_id = sender
        if sender_id not in self._allowed_senders:
            return None

        conversation_id = f'telegram:chat:{chat_id}'
        if topic_id is not None:
            conversation_id += f':topic:{topic_id}'
        return ChannelEvent(
            event_id=f'telegram:update:{update_id}',
            conversation_id=conversation_id,
            sender_id=f'telegram:{sender_kind}:{sender_id}',
            text=text,
            reply_to_id=f'telegram:message:{message_id}',
        )

    def _sender(self, message: Mapping[str, object]) -> tuple[str, int] | None:
        raw_sender_chat = message.get('sender_chat')
        if raw_sender_chat is not None:
            sender_chat = _mapping(raw_sender_chat)
            if sender_chat is None:
                raise TelegramError('Telegram webhook payload has invalid sender-chat identity')
            sender_chat_id = sender_chat.get('id')
            if not _is_integer_id(sender_chat_id):
                raise TelegramError('Telegram webhook payload has invalid sender-chat identity')
            return 'chat', sender_chat_id

        raw_sender = message.get('from')
        sender = _mapping(raw_sender)
        if sender is None:
            raise TelegramError('Telegram webhook payload has no sender identity')
        sender_id = sender.get('id')
        is_bot = sender.get('is_bot')
        if not _is_integer_id(sender_id) or not isinstance(is_bot, bool):
            raise TelegramError('Telegram webhook payload has invalid sender identity')
        if is_bot:
            return None
        return 'user', sender_id

    async def reply(self, event: ChannelEvent, text: str) -> None:
        """Reply once in Telegram, splitting text at the provider limit.

        A flood-control response is retried once after Telegram's `retry_after` delay. Transport
        failures and other provider errors are surfaced without retry because delivery may already
        have happened.
        """
        if not text:
            raise ValueError('text must not be empty')
        chat_id, topic_id = _decode_conversation(event.conversation_id)
        message_id = _decode_message(event.reply_to_id)

        for start in range(0, len(text), _MAX_TEXT_CHARS):
            payload: dict[str, object] = {
                'chat_id': chat_id,
                'text': text[start : start + _MAX_TEXT_CHARS],
            }
            if topic_id is not None:
                payload['message_thread_id'] = topic_id
            if message_id is not None:
                payload['reply_parameters'] = {'message_id': message_id}
            await self._send_with_one_flood_retry(payload)

    async def _send_with_one_flood_retry(self, payload: Mapping[str, object]) -> None:
        try:
            await self._send(payload)
        except _RateLimited as exc:
            await anyio.sleep(exc.retry_after)
            try:
                await self._send(payload)
            except _RateLimited as second:
                raise TelegramError(str(second)) from None

    async def _send(self, payload: Mapping[str, object]) -> None:
        if self._client is not None:
            await self._post(self._client, payload)
            return
        async with httpx.AsyncClient() as client:
            await self._post(client, payload)

    async def _post(self, client: httpx.AsyncClient, payload: Mapping[str, object]) -> None:
        method = 'sendMessage'
        try:
            response = await client.post(
                f'{self._api_url}/bot{self._bot_token}/{method}',
                json=dict(payload),
            )
        except httpx.RequestError:
            raise TelegramError(f'Telegram Bot API request failed: {method}') from None

        try:
            envelope = _mapping(_JSON_ADAPTER.validate_json(response.content))
        except ValidationError:
            envelope = None
        if envelope is None:
            raise TelegramError(f'Telegram Bot API returned an invalid response: {method}')
        if response.is_success and envelope.get('ok') is True:
            return

        description = envelope.get('description')
        message = description if isinstance(description, str) else f'Telegram Bot API rejected {method}'
        message = message.replace(self._bot_token, '<redacted>')
        retry_after = _retry_after(envelope)
        if retry_after is not None:
            raise _RateLimited(message, retry_after=retry_after)
        raise TelegramError(message)


def _mapping(value: object) -> dict[str, object] | None:
    try:
        return _MAPPING_ADAPTER.validate_python(value)
    except ValidationError:
        return None


def _is_integer_id(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _decode_conversation(conversation_id: str) -> tuple[int, int | None]:
    match = _CONVERSATION_PATTERN.fullmatch(conversation_id)
    if match is None:
        raise TelegramError('event conversation_id is not a Telegram chat identity')
    topic = match.group(2)
    return int(match.group(1)), int(topic) if topic is not None else None


def _decode_message(reply_to_id: str | None) -> int | None:
    if reply_to_id is None:
        return None
    match = _MESSAGE_PATTERN.fullmatch(reply_to_id)
    if match is None:
        raise TelegramError('event reply_to_id is not a Telegram message identity')
    return int(match.group(1))


def _retry_after(envelope: Mapping[str, object]) -> float | None:
    parameters = _mapping(envelope.get('parameters'))
    if parameters is None:
        return None
    retry_after = parameters.get('retry_after')
    if (
        isinstance(retry_after, bool)
        or not isinstance(retry_after, int)
        or retry_after < 0
        or retry_after > _MAX_RETRY_AFTER_SECONDS
    ):
        return None
    return float(retry_after)
