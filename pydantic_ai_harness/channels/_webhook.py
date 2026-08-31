"""Private queue bridge for webhook channel adapters."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from pydantic_ai_harness.channels._types import InboundMessage


class WebhookInbox:
    def __init__(self, max_queued_messages: int) -> None:
        self._max_queued_messages = max_queued_messages
        self._queue: asyncio.Queue[InboundMessage | None] = asyncio.Queue(max_queued_messages)
        self._closed = False

    def open(self) -> None:
        if self._closed:
            self._queue = asyncio.Queue(self._max_queued_messages)
            self._closed = False

    def put(self, message: InboundMessage) -> bool:
        if self._closed:  # pragma: no cover
            return False
        try:
            self._queue.put_nowait(message)
        except asyncio.QueueFull:
            return False
        return True

    def close(self) -> None:
        self._closed = True
        if self._queue.full():
            self._queue.get_nowait()
        self._queue.put_nowait(None)

    async def messages(self) -> AsyncIterator[InboundMessage]:
        queue = self._queue
        while True:
            message = await queue.get()
            if message is None:
                return
            yield message
