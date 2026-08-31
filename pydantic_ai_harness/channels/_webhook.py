"""Private queue bridge for webhook channel adapters."""

from __future__ import annotations

from collections.abc import AsyncGenerator

import anyio
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream

from pydantic_ai_harness.channels._types import InboundMessage


class WebhookInbox:
    def __init__(self, max_queued_messages: int) -> None:
        self._max_queued_messages = max_queued_messages
        self._send_stream: MemoryObjectSendStream[InboundMessage] | None = None
        self._receive_stream: MemoryObjectReceiveStream[InboundMessage] | None = None
        self._receive_claimed = False
        self._closed = True

    def open(self) -> None:
        if self._closed:
            self._send_stream, self._receive_stream = anyio.create_memory_object_stream[InboundMessage](
                self._max_queued_messages
            )
            self._receive_claimed = False
            self._closed = False

    def put(self, message: InboundMessage) -> bool:
        send_stream = self._send_stream
        if self._closed or send_stream is None:
            return False
        try:
            send_stream.send_nowait(message)
        except (anyio.WouldBlock, anyio.BrokenResourceError, anyio.ClosedResourceError):
            return False
        return True

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        send_stream = self._send_stream
        if send_stream is not None:
            send_stream.close()
        receive_stream = self._receive_stream
        if receive_stream is not None:
            statistics = receive_stream.statistics()
            if not self._receive_claimed and statistics.current_buffer_used == 0:
                receive_stream.close()

    async def messages(self) -> AsyncGenerator[InboundMessage, None]:
        receive_stream = self._receive_stream
        if receive_stream is None:
            return
        self._receive_claimed = True
        try:
            try:
                async with receive_stream:
                    async for message in receive_stream:
                        yield message
            except anyio.ClosedResourceError:
                return
        finally:
            if receive_stream is self._receive_stream:
                self._receive_claimed = False
