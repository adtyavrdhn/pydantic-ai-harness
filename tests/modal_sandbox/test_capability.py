"""Tests for the public Modal sandbox capability."""

from __future__ import annotations

import inspect

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.sandboxes import SandboxRef
from pydantic_ai.tools import RunContext
from pydantic_ai.usage import RunUsage

import pydantic_ai_harness
import pydantic_ai_harness.modal_sandbox as modal_sandbox
from pydantic_ai_harness.modal_sandbox import ModalSandbox, ModalSandboxBackend

from .fake_modal import FakeModal

pytestmark = pytest.mark.anyio(backends=['asyncio'])


@pytest.fixture
def anyio_backend() -> str:
    return 'asyncio'


def _ctx(run_id: str = 'run-1') -> RunContext[None]:
    return RunContext(deps=None, model=TestModel(), usage=RunUsage(), run_id=run_id)


class TestLifecycle:
    async def test_agent_tool_consumes_ctx_sandbox(self, fake_modal: FakeModal) -> None:
        async def respond(messages: list[ModelMessage], _: AgentInfo) -> ModelResponse:
            returns = [
                part
                for message in messages
                if isinstance(message, ModelRequest)
                for part in message.parts
                if isinstance(part, ToolReturnPart)
            ]
            if not returns:
                return ModelResponse(parts=[ToolCallPart('run_in_sandbox', {})])
            return ModelResponse(parts=[TextPart('done')])

        agent = Agent(FunctionModel(respond), deps_type=type(None), capabilities=[ModalSandbox[None]()])

        @agent.tool
        async def run_in_sandbox(ctx: RunContext[None]) -> str:
            return (await ctx.sandbox.run(['echo', 'hello'])).stdout

        result = await agent.run('Use the sandbox.')

        assert result.output == 'done'
        assert len(fake_modal.sandboxes) == 1
        assert fake_modal.sandboxes[0].terminated is True

    async def test_acquire_is_idempotent_for_one_logical_run(self, fake_modal: FakeModal) -> None:
        capability = ModalSandbox[None]()

        first = await capability.acquire_sandbox(_ctx())
        second = await capability.acquire_sandbox(_ctx())

        assert first == second
        assert len(fake_modal.sandboxes) == 1
        assert fake_modal.sandboxes[0].detached is True
        assert fake_modal.create_kwargs[0]['name'] is not None

    async def test_different_runs_use_different_names(self, fake_modal: FakeModal) -> None:
        capability = ModalSandbox[None]()

        await capability.acquire_sandbox(_ctx('run-1'))
        await capability.acquire_sandbox(_ctx('run-2'))

        assert fake_modal.create_kwargs[0]['name'] != fake_modal.create_kwargs[1]['name']

    async def test_create_race_reconnects_to_the_winner(self, fake_modal: FakeModal) -> None:
        existing = await ModalSandboxBackend.create(name='shared')
        fake_modal.name_lookup_misses = 1

        connected = await ModalSandboxBackend.create_or_connect(name='shared')

        assert connected.sandbox_id == existing.sandbox_id
        assert fake_modal.name_lookups[-2:] == [
            ('pydantic-ai-harness', 'shared'),
            ('pydantic-ai-harness', 'shared'),
        ]

    async def test_get_sandbox_reconnects_by_ref(self, fake_modal: FakeModal) -> None:
        backend = await ModalSandbox[None]().get_sandbox(_ctx(), SandboxRef(sandbox_id='sb-existing'))

        assert isinstance(backend, ModalSandboxBackend)
        assert fake_modal.attach_ids == ['sb-existing']

    async def test_release_reconnects_and_terminates(self, fake_modal: FakeModal) -> None:
        capability = ModalSandbox[None]()
        ref = await capability.acquire_sandbox(_ctx())

        await capability.release_sandbox(_ctx(), ref)

        assert fake_modal.attach_ids == [ref.sandbox_id]
        assert fake_modal.sandboxes[-1].terminated is True

    async def test_release_is_idempotent_when_sandbox_is_gone(self, fake_modal: FakeModal) -> None:
        fake_modal.attach_error = fake_modal.unavailable_type('gone')

        await ModalSandbox[None]().release_sandbox(_ctx(), SandboxRef(sandbox_id='gone'))

    async def test_attached_sandbox_is_not_owned(self, fake_modal: FakeModal) -> None:
        capability = ModalSandbox[None](sandbox_id='sb-existing')

        ref = await capability.acquire_sandbox(_ctx())
        await capability.release_sandbox(_ctx(), ref)

        assert ref == SandboxRef(sandbox_id='sb-existing')
        assert fake_modal.sandboxes == []


class TestConfiguration:
    def test_defaults(self) -> None:
        capability = ModalSandbox()

        assert capability.image == 'python:3.12-slim'
        assert capability.app_name == 'pydantic-ai-harness'
        assert capability.sandbox_timeout == 300

    def test_configuration_is_keyword_only(self) -> None:
        assert all(
            parameter.kind is inspect.Parameter.KEYWORD_ONLY
            for parameter in inspect.signature(ModalSandbox).parameters.values()
        )

    @pytest.mark.parametrize('timeout', [0, -1])
    def test_rejects_invalid_sandbox_timeout(self, timeout: object) -> None:
        with pytest.raises(ValueError, match='sandbox_timeout'):
            ModalSandbox(sandbox_timeout=timeout)  # type: ignore[arg-type]

    def test_rejects_relative_workdir(self) -> None:
        with pytest.raises(ValueError, match='workdir must be an absolute sandbox path'):
            ModalSandbox(workdir='repo')

    @pytest.mark.parametrize(
        'settings',
        [
            {'image': 'ubuntu:22.04'},
            {'app_name': 'other'},
            {'create_app_if_missing': False},
            {'sandbox_timeout': 600},
            {'workdir': '/work'},
            {'env': {'A': 'b'}},
        ],
    )
    def test_attach_rejects_creation_settings(self, settings: dict[str, object]) -> None:
        with pytest.raises(ValueError, match='only apply when creating'):
            ModalSandbox(sandbox_id='sb-existing', **settings)  # type: ignore[arg-type]

    def test_copies_environment_mapping(self) -> None:
        source = {'A': 'one'}
        capability = ModalSandbox(env=source)
        source['A'] = 'two'

        assert capability.env == {'A': 'one'}

    def test_public_exports_are_narrow(self) -> None:
        assert pydantic_ai_harness.ModalSandbox is ModalSandbox
        assert set(modal_sandbox.__all__) == {
            'ModalSandbox',
            'ModalSandboxAuthError',
            'ModalSandboxBackend',
            'ModalSandboxError',
            'ModalSandboxUnavailableError',
        }

    def test_serialization_name(self) -> None:
        assert ModalSandbox.get_serialization_name() == 'ModalSandbox'
