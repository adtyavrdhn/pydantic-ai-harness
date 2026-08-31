"""Tests for the public Modal sandbox capability."""

from __future__ import annotations

import inspect
from typing import cast

import anyio
import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.sandboxes import Sandbox, SandboxRef
from pydantic_ai.tools import RunContext
from pydantic_ai.usage import RunUsage

import pydantic_ai_harness
import pydantic_ai_harness.modal_sandbox as modal_sandbox
from pydantic_ai_harness.modal_sandbox import ModalSandbox, ModalSandboxBackend, ModalSandboxError, ModalSandboxSession
from pydantic_ai_harness.modal_sandbox._toolset import ModalSandboxToolset

from .fake_modal import FakeModal

pytestmark = pytest.mark.anyio(backends=['asyncio'])


@pytest.fixture
def anyio_backend() -> str:
    return 'asyncio'


def _ctx(run_id: str = 'run-1', *, sandbox: Sandbox | None = None) -> RunContext[None]:
    if sandbox is None:
        return RunContext(deps=None, model=TestModel(), usage=RunUsage(), run_id=run_id)
    return RunContext(deps=None, model=TestModel(), usage=RunUsage(), run_id=run_id, sandbox=sandbox)


class TestLifecycle:
    async def test_released_tools_still_run(self, fake_modal: FakeModal) -> None:
        backend = await ModalSandboxBackend.create()
        ctx = _ctx(sandbox=Sandbox(backend))
        toolset = cast(ModalSandboxToolset[None], ModalSandbox[None](max_output_bytes=100).get_toolset())
        assert isinstance(toolset, ModalSandboxToolset)
        run_toolset = await toolset.for_run(ctx)
        assert isinstance(run_toolset, ModalSandboxToolset)

        async with run_toolset:
            assert await run_toolset.run_command(ctx, 'echo hello') == '[stdout]\nsh -c echo hello'
            assert await run_toolset.write_file(ctx, '/note.txt', 'hello') == "Wrote 5 bytes to '/note.txt'."
            assert await run_toolset.read_file(ctx, '/note.txt') == 'hello'
            fake_modal.sandboxes[0].listing = []
            assert await run_toolset.list_directory(ctx, '/') == '(empty)'
        await backend.close(terminate=True)

    async def test_released_tool_and_ctx_sandbox_share_one_sandbox(self, fake_modal: FakeModal) -> None:
        async def respond(messages: list[ModelMessage], _: AgentInfo) -> ModelResponse:
            returns = [
                part
                for message in messages
                if isinstance(message, ModelRequest)
                for part in message.parts
                if isinstance(part, ToolReturnPart)
            ]
            if not returns:
                return ModelResponse(parts=[ToolCallPart('write_file', {'path': '/shared.txt', 'content': 'same'})])
            if len(returns) == 1:
                return ModelResponse(parts=[ToolCallPart('read_via_ctx', {})])
            return ModelResponse(parts=[TextPart('done')])

        agent = Agent(FunctionModel(respond), deps_type=type(None), capabilities=[ModalSandbox[None]()])

        @agent.tool
        async def read_via_ctx(ctx: RunContext[None]) -> str:
            return await ctx.sandbox.read_text('/shared.txt')

        result = await agent.run('Use the sandbox.')

        assert result.output == 'done'
        assert len(fake_modal.sandboxes) == 1
        returns = [
            part
            for message in result.all_messages()
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        assert [part.content for part in returns] == ["Wrote 4 bytes to '/shared.txt'.", 'same']

    def test_default_instructions_describe_released_tools(self) -> None:
        instructions = ModalSandbox().get_instructions()

        assert instructions is not None
        assert all(name in instructions for name in ('run_command', 'read_file', 'write_file', 'list_directory'))

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
        backend = await ModalSandbox[None]().get_sandbox(_ctx(), SandboxRef(provider='modal', sandbox_id='sb-existing'))

        assert isinstance(backend, ModalSandboxBackend)
        assert fake_modal.attach_ids == ['sb-existing']

    async def test_get_sandbox_declines_other_providers(self, fake_modal: FakeModal) -> None:
        backend = await ModalSandbox[None]().get_sandbox(_ctx(), SandboxRef(provider='e2b', sandbox_id='x'))

        assert backend is None
        assert fake_modal.sandboxes == []

    async def test_release_reconnects_and_terminates(self, fake_modal: FakeModal) -> None:
        capability = ModalSandbox[None]()
        ref = await capability.acquire_sandbox(_ctx())

        await capability.release_sandbox(_ctx(), ref)

        assert fake_modal.attach_ids == [ref.sandbox_id]
        assert fake_modal.sandboxes[-1].terminated is True

    async def test_release_is_idempotent_when_sandbox_is_gone(self, fake_modal: FakeModal) -> None:
        fake_modal.attach_error = fake_modal.unavailable_type('gone')

        await ModalSandbox[None]().release_sandbox(_ctx(), SandboxRef(provider='modal', sandbox_id='gone'))

    async def test_attached_sandbox_is_not_owned(self, fake_modal: FakeModal) -> None:
        capability = ModalSandbox[None](sandbox_id='sb-existing')

        ref = await capability.acquire_sandbox(_ctx())
        await capability.release_sandbox(_ctx(), ref)

        assert ref == SandboxRef(provider='modal', sandbox_id='sb-existing')
        assert fake_modal.sandboxes == []

    async def test_open_session_remains_caller_owned(self, fake_modal: FakeModal) -> None:
        async with ModalSandboxSession() as session:
            capability = ModalSandbox[None](session=session)
            ref = await capability.acquire_sandbox(_ctx())

            assert isinstance(await capability.get_sandbox(_ctx(), ref), ModalSandboxBackend)
            await capability.release_sandbox(_ctx(), ref)
            assert fake_modal.sandboxes[0].terminated is False

        assert fake_modal.sandboxes[0].terminated is True

    async def test_unopened_session_cannot_be_acquired(self) -> None:
        capability = ModalSandbox[None](session=ModalSandboxSession())

        with pytest.raises(ModalSandboxError, match='must already be entered'):
            await capability.acquire_sandbox(_ctx())

    async def test_run_toolset_rejects_a_different_sandbox(self, fake_modal: FakeModal) -> None:
        first = await ModalSandboxBackend.create()
        second = await ModalSandboxBackend.create()
        first_ctx = _ctx(sandbox=Sandbox(first))
        second_ctx = _ctx(sandbox=Sandbox(second))
        toolset = cast(ModalSandboxToolset[None], ModalSandbox[None]().get_toolset())
        run_toolset = cast(ModalSandboxToolset[None], await toolset.for_run(first_ctx))

        async with run_toolset:
            await run_toolset.run_command(first_ctx, 'true')
            with pytest.raises(ModalSandboxError, match='different sandboxes'):
                await run_toolset.run_command(second_ctx, 'true')

        await first.close(terminate=True)
        await second.close(terminate=True)

    async def test_concurrent_first_tools_share_one_attached_session(self, fake_modal: FakeModal) -> None:
        backend = await ModalSandboxBackend.create()
        ctx = _ctx(sandbox=Sandbox(backend))
        toolset = cast(ModalSandboxToolset[None], ModalSandbox[None]().get_toolset())
        run_toolset = cast(ModalSandboxToolset[None], await toolset.for_run(ctx))
        results: list[str] = []

        async with run_toolset:
            async with anyio.create_task_group() as task_group:

                async def run() -> None:
                    results.append(await run_toolset.run_command(ctx, 'true'))

                task_group.start_soon(run)
                task_group.start_soon(run)

        assert len(results) == 2
        assert fake_modal.attach_ids == [backend.sandbox_id]
        await backend.close(terminate=True)


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

    @pytest.mark.parametrize('timeout', [0, -1, 1.5, True])
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
            'ModalSandboxCommandTimeoutError',
            'ModalSandboxError',
            'ModalSandboxExecResult',
            'ModalSandboxSession',
            'ModalSandboxTerminalError',
            'ModalSandboxUnavailableError',
        }

    def test_serialization_name(self) -> None:
        assert ModalSandbox.get_serialization_name() == 'ModalSandbox'
