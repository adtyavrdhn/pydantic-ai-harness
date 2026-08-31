from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from types import TracebackType

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from typing_extensions import Self

from pydantic_ai_harness.channels import (
    ChannelError,
    ChannelHost,
    InboundMessage,
    InMemoryConversationStore,
)


class FakeChannel:
    def __init__(self, messages: Sequence[InboundMessage] = ()) -> None:
        self.inbound = list(messages)
        self.sent: list[tuple[str, str]] = []
        self.fail_sends = False
        self.entered = False

    async def __aenter__(self) -> Self:
        self.entered = True
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        self.entered = False
        return None

    async def messages(self) -> AsyncIterator[InboundMessage]:
        for message in self.inbound:
            yield message

    async def send_text(self, conversation_id: str, text: str) -> None:
        if self.fail_sends:
            raise ChannelError('uncertain delivery')
        self.sent.append((conversation_id, text))


class GateStore(InMemoryConversationStore):
    def __init__(self) -> None:
        super().__init__()
        self.started: list[str] = []
        self.two_started = asyncio.Event()
        self.release = asyncio.Event()

    async def load(self, conversation_id: str) -> Sequence[ModelMessage]:
        self.started.append(conversation_id)
        if len(self.started) == 2:
            self.two_started.set()
        await self.release.wait()
        return await super().load(conversation_id)


def inbound(text: str, *, conversation_id: str = 'chat-1', sender_id: str = 'user-1') -> InboundMessage:
    return InboundMessage(
        conversation_id=conversation_id,
        sender_id=sender_id,
        message_id=f'message-{text}',
        text=text,
    )


@pytest.mark.anyio
class TestChannelHost:
    async def test_continues_history_and_keeps_agent_capabilities(self) -> None:
        request_counts: list[int] = []

        def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            count = sum(isinstance(message, ModelRequest) for message in messages)
            request_counts.append(count)
            return ModelResponse(parts=[TextPart(f'reply {count}')])

        agent: Agent[None, str] = Agent(FunctionModel(model), instructions='Keep this instruction.')
        channel = FakeChannel([inbound('first'), inbound('second')])
        store = InMemoryConversationStore()

        await ChannelHost(agent, channel, allowed_senders={'user-1'}, store=store).serve()

        assert request_counts == [1, 2]
        assert channel.sent == [('chat-1', 'reply 1'), ('chat-1', 'reply 2')]
        assert len(await store.load('chat-1')) == 4
        assert channel.entered is False

    async def test_rejects_sender_before_reading_store(self) -> None:
        class UnreadableStore(InMemoryConversationStore):
            async def load(self, conversation_id: str) -> Sequence[ModelMessage]:
                raise AssertionError('store must not be read')  # pragma: no cover

        channel = FakeChannel([inbound('ignored', sender_id='stranger')])
        agent: Agent[None, str] = Agent('test')

        await ChannelHost(agent, channel, allowed_senders={'user-1'}, store=UnreadableStore()).serve()

        assert channel.sent == []

    async def test_uses_a_falsey_conversation_store(self) -> None:
        class FalseyStore(InMemoryConversationStore):
            def __bool__(self) -> bool:
                return False

        store = FalseyStore()
        channel = FakeChannel([inbound('hello')])
        assert not store

        await ChannelHost(Agent('test'), channel, allowed_senders={'user-1'}, store=store).serve()

        assert len(await store.load('chat-1')) == 2

    async def test_new_clears_history_before_next_turn(self) -> None:
        channel = FakeChannel([inbound('first'), inbound('/new'), inbound('second')])
        store = InMemoryConversationStore()
        agent: Agent[None, str] = Agent('test', instructions='Reply in text.')

        await ChannelHost(agent, channel, allowed_senders={'user-1'}, store=store).serve()

        assert [text for _, text in channel.sent] == [
            'success (no tool calls)',
            'Started a new conversation.',
            'success (no tool calls)',
        ]
        assert len(await store.load('chat-1')) == 2

    async def test_serializes_one_conversation_and_runs_distinct_conversations_concurrently(self) -> None:
        store = GateStore()
        channel = FakeChannel(
            [
                inbound('one'),
                inbound('queued'),
                inbound('other', conversation_id='chat-2'),
            ]
        )
        agent: Agent[None, str] = Agent('test')
        serve = asyncio.create_task(ChannelHost(agent, channel, allowed_senders={'user-1'}, store=store).serve())

        await asyncio.wait_for(store.two_started.wait(), timeout=1)
        assert store.started == ['chat-1', 'chat-2']
        store.release.set()
        await serve

        assert store.started == ['chat-1', 'chat-2', 'chat-1']

    async def test_bounds_running_and_waiting_turns(self) -> None:
        store = GateStore()
        channel = FakeChannel(
            [
                inbound('one', conversation_id='chat-1'),
                inbound('two', conversation_id='chat-2'),
                inbound('three', conversation_id='chat-3'),
            ]
        )
        agent: Agent[None, str] = Agent('test')
        serve = asyncio.create_task(
            ChannelHost(
                agent,
                channel,
                allowed_senders={'user-1'},
                store=store,
                max_pending_turns=2,
            ).serve()
        )

        await asyncio.wait_for(store.two_started.wait(), timeout=1)
        await asyncio.sleep(0)
        assert store.started == ['chat-1', 'chat-2']

        store.release.set()
        await serve
        assert store.started == ['chat-1', 'chat-2', 'chat-3']

    async def test_run_failure_replies_without_leaking_the_error(self, caplog: pytest.LogCaptureFixture) -> None:
        def fail(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            raise RuntimeError('secret model detail')

        agent: Agent[None, str] = Agent(FunctionModel(fail))
        channel = FakeChannel([inbound('fail')])

        await ChannelHost(agent, channel, allowed_senders={'user-1'}, error_reply='Try again later.').serve()

        assert channel.sent == [('chat-1', 'Try again later.')]
        assert 'secret model detail' in caplog.text

    async def test_send_failure_is_not_retried(self, caplog: pytest.LogCaptureFixture) -> None:
        agent: Agent[None, str] = Agent('test')
        channel = FakeChannel([inbound('hello')])
        channel.fail_sends = True

        await ChannelHost(agent, channel, allowed_senders={'user-1'}).serve()

        assert channel.sent == []
        assert caplog.text.count('Channel send failed') == 1

    async def test_store_failure_uses_static_error_reply(self) -> None:
        class FailingStore(InMemoryConversationStore):
            async def load(self, conversation_id: str) -> Sequence[ModelMessage]:
                raise RuntimeError('database URL with credentials')

        channel = FakeChannel([inbound('hello')])
        agent: Agent[None, str] = Agent('test')

        await ChannelHost(agent, channel, allowed_senders={'user-1'}, store=FailingStore()).serve()

        assert channel.sent == [('chat-1', 'Sorry, something went wrong handling that message.')]

    async def test_reset_store_failure_uses_static_error_reply(self) -> None:
        class FailingStore(InMemoryConversationStore):
            async def delete(self, conversation_id: str) -> None:
                raise RuntimeError('database unavailable')

        channel = FakeChannel([inbound('/new')])
        agent: Agent[None, str] = Agent('test')

        await ChannelHost(agent, channel, allowed_senders={'user-1'}, store=FailingStore()).serve()

        assert channel.sent == [('chat-1', 'Sorry, something went wrong handling that message.')]

    @pytest.mark.parametrize('finite_adapter', [False, True])
    async def test_cancellation_closes_adapter_and_cancels_turn(self, finite_adapter: bool) -> None:
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            started.set()
            try:
                await asyncio.Future()
            finally:
                cancelled.set()
            raise AssertionError('cancelled model returned')  # pragma: no cover

        class WaitingChannel(FakeChannel):
            async def messages(self) -> AsyncIterator[InboundMessage]:
                yield inbound('wait')
                await asyncio.Future()

        channel = FakeChannel([inbound('wait')]) if finite_adapter else WaitingChannel()
        agent: Agent[None, str] = Agent(FunctionModel(model))
        task = asyncio.create_task(ChannelHost(agent, channel, allowed_senders={'user-1'}).serve())
        await asyncio.wait_for(started.wait(), timeout=1)

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert cancelled.is_set()
        assert channel.entered is False

    def test_requires_at_least_one_allowed_sender(self) -> None:
        with pytest.raises(ValueError, match='allowed_senders'):
            ChannelHost(Agent('test'), FakeChannel(), allowed_senders=set())

    def test_rejects_string_allowed_senders(self) -> None:
        with pytest.raises(TypeError, match='collection of sender ids'):
            ChannelHost(Agent('test'), FakeChannel(), allowed_senders='user-1')

    def test_requires_positive_pending_turn_limit(self) -> None:
        with pytest.raises(ValueError, match='max_pending_turns'):
            ChannelHost(Agent('test'), FakeChannel(), allowed_senders={'user-1'}, max_pending_turns=0)

        with pytest.raises(ValueError, match='max_pending_turns'):
            ChannelHost(Agent('test'), FakeChannel(), allowed_senders={'user-1'}, max_pending_turns=True)
