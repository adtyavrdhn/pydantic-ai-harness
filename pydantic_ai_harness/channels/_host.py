"""Run a Pydantic AI agent for messages from one channel adapter."""

from __future__ import annotations

import logging
from collections.abc import Collection
from contextlib import aclosing
from dataclasses import dataclass, field
from typing import Generic

import anyio
from pydantic_ai.agent import AbstractAgent
from pydantic_ai.tools import AgentDepsT

from pydantic_ai_harness.channels._types import (
    ChannelAdapter,
    ConversationStore,
    InboundMessage,
    InMemoryConversationStore,
)

logger = logging.getLogger(__name__)

_DEFAULT_ERROR_REPLY = 'Sorry, something went wrong handling that message.'
_DEFAULT_RESET_REPLY = 'Started a new conversation.'


@dataclass(slots=True)
class _ConversationLane:
    lock: anyio.Lock = field(default_factory=anyio.Lock)
    users: int = 0


class ChannelHost(Generic[AgentDepsT]):
    """Serve a text-output Pydantic AI agent through one messaging channel.

    The host starts one agent run per accepted inbound message. Turns are
    serialized within each conversation and can run concurrently across
    conversations. The agent's existing capabilities participate normally in
    every run.
    """

    def __init__(
        self,
        agent: AbstractAgent[AgentDepsT, str],
        adapter: ChannelAdapter,
        *,
        allowed_senders: Collection[str],
        store: ConversationStore | None = None,
        deps: AgentDepsT = None,
        error_reply: str = _DEFAULT_ERROR_REPLY,
        reset_reply: str = _DEFAULT_RESET_REPLY,
        max_pending_turns: int = 100,
    ) -> None:
        """Configure the channel host.

        Args:
            agent: A Pydantic AI agent whose output is text.
            adapter: The single channel connection this host serves.
            allowed_senders: Provider sender ids allowed to start agent runs.
            store: Conversation history storage. Defaults to process-local memory.
            deps: Dependencies passed to every agent run.
            error_reply: Static reply sent when a turn fails.
            reset_reply: Reply sent after the `/new` command clears history.
            max_pending_turns: Maximum accepted turns that may be running or waiting.

        Raises:
            TypeError: If `allowed_senders` is a string instead of a collection of ids.
            ValueError: If `allowed_senders` is empty or `max_pending_turns` is not positive.
        """
        if isinstance(allowed_senders, str):
            raise TypeError('allowed_senders must be a collection of sender ids, not a string')
        sender_ids = frozenset(allowed_senders)
        if not sender_ids:
            raise ValueError('allowed_senders must contain at least one sender id')
        if type(max_pending_turns) is not int or max_pending_turns <= 0:
            raise ValueError('max_pending_turns must be a positive integer')

        self._agent = agent
        self._adapter = adapter
        self._allowed_senders = sender_ids
        self._store = store if store is not None else InMemoryConversationStore()
        self._deps = deps
        self._error_reply = error_reply
        self._reset_reply = reset_reply
        self._max_pending_turns = max_pending_turns
        self._serving = False

    async def serve(self) -> None:
        """Receive and handle messages until the adapter ends or this task is cancelled."""
        if self._serving:
            raise RuntimeError('ChannelHost is already serving')
        self._serving = True
        turn_slots = anyio.Semaphore(self._max_pending_turns)
        lanes: dict[str, _ConversationLane] = {}
        try:
            async with self._adapter:
                async with anyio.create_task_group() as task_group:
                    async with aclosing(self._adapter.messages()) as inbound:
                        async for message in inbound:
                            if message.sender_id not in self._allowed_senders:
                                logger.warning('Ignoring channel message from sender %s', message.sender_id)
                                continue
                            await turn_slots.acquire()
                            lane = lanes.setdefault(message.conversation_id, _ConversationLane())
                            lane.users += 1
                            task_group.start_soon(self._handle_serialized, message, lane, lanes, turn_slots)
        finally:
            self._serving = False

    async def _handle_serialized(
        self,
        message: InboundMessage,
        lane: _ConversationLane,
        lanes: dict[str, _ConversationLane],
        turn_slots: anyio.Semaphore,
    ) -> None:
        try:
            async with lane.lock:
                await self._handle(message)
        finally:
            lane.users -= 1
            if lane.users == 0:
                del lanes[message.conversation_id]
            turn_slots.release()

    async def _handle(self, message: InboundMessage) -> None:
        if message.text.strip() == '/new':
            await self._reset(message)
            return

        try:
            history = await self._store.load(message.conversation_id)
            result = await self._agent.run(
                message.text,
                message_history=history or None,
                deps=self._deps,
            )
            await self._store.save(message.conversation_id, result.all_messages())
        except Exception:
            logger.exception(
                'Agent turn failed for channel message %s in conversation %s',
                message.message_id,
                message.conversation_id,
            )
            await self._send_without_retry(message.conversation_id, self._error_reply)
            return

        await self._send_without_retry(message.conversation_id, result.output)

    async def _reset(self, message: InboundMessage) -> None:
        try:
            await self._store.delete(message.conversation_id)
        except Exception:
            logger.exception('Could not reset channel conversation %s', message.conversation_id)
            await self._send_without_retry(message.conversation_id, self._error_reply)
            return
        await self._send_without_retry(message.conversation_id, self._reset_reply)

    async def _send_without_retry(self, conversation_id: str, text: str) -> None:
        try:
            await self._adapter.send_text(conversation_id, text)
        except Exception:
            logger.exception('Channel send failed for conversation %s', conversation_id)
