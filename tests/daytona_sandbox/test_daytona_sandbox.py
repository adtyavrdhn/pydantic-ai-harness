"""Direct tests for the Daytona sandbox backend and capability."""

from __future__ import annotations

import asyncio
import inspect

import pytest
from pydantic_ai.models.test import TestModel
from pydantic_ai.sandboxes import Sandbox, SandboxBackend, SandboxRef, SupportsFilesystem, SupportsStart
from pydantic_ai.tools import RunContext
from pydantic_ai.usage import RunUsage

import pydantic_ai_harness
import pydantic_ai_harness.daytona_sandbox as daytona_sandbox
from pydantic_ai_harness.daytona_sandbox import (
    DaytonaSandbox,
    DaytonaSandboxBackend,
    DaytonaSandboxCommandTimeoutError,
    DaytonaSandboxError,
)

from .fake_daytona import FakeDaytona

pytestmark = pytest.mark.anyio(backends=['asyncio'])


def _ctx(run_id: str = 'run-1') -> RunContext[None]:
    return RunContext(deps=None, model=TestModel(), usage=RunUsage(), run_id=run_id)


class TestLifecycle:
    async def test_acquire_is_idempotent_for_one_logical_run(self, fake_daytona: FakeDaytona) -> None:
        capability = DaytonaSandbox[None]()
        first = await capability.acquire_sandbox(_ctx())
        second = await capability.acquire_sandbox(_ctx())
        assert first == second
        assert len(fake_daytona.sandboxes) == 1
        assert fake_daytona.create_params[0].name is not None
        assert fake_daytona.closed_clients == 3

    async def test_different_runs_use_different_names(self, fake_daytona: FakeDaytona) -> None:
        capability = DaytonaSandbox[None]()
        await capability.acquire_sandbox(_ctx('run-1'))
        await capability.acquire_sandbox(_ctx('run-2'))
        assert fake_daytona.create_params[0].name != fake_daytona.create_params[1].name

    async def test_get_sandbox_reconnects_by_ref(self, fake_daytona: FakeDaytona) -> None:
        sandbox = fake_daytona.sandbox()
        backend = await DaytonaSandbox[None]().get_sandbox(
            _ctx(), SandboxRef(provider='daytona', sandbox_id=sandbox.id)
        )
        assert isinstance(backend, DaytonaSandboxBackend)
        assert backend.sandbox_id == sandbox.id

    async def test_get_sandbox_declines_other_providers(self, fake_daytona: FakeDaytona) -> None:
        backend = await DaytonaSandbox[None]().get_sandbox(_ctx(), SandboxRef(provider='modal', sandbox_id='x'))
        assert backend is None
        assert fake_daytona.sandboxes == []

    async def test_release_deletes_without_starting(self, fake_daytona: FakeDaytona) -> None:
        capability = DaytonaSandbox[None]()
        ref = await capability.acquire_sandbox(_ctx())
        sandbox = fake_daytona.sandboxes[0]
        await capability.release_sandbox(_ctx(), ref)
        assert fake_daytona.get_calls[-1] == ref.sandbox_id
        assert sandbox.start_calls == []
        assert fake_daytona.delete_calls == [(ref.sandbox_id, 60, True)]

    async def test_release_is_idempotent_when_sandbox_is_gone(self, fake_daytona: FakeDaytona) -> None:
        await DaytonaSandbox[None]().release_sandbox(_ctx(), SandboxRef(provider='daytona', sandbox_id='gone'))

    async def test_attached_sandbox_is_not_owned(self, fake_daytona: FakeDaytona) -> None:
        capability = DaytonaSandbox[None](sandbox_id='existing')
        ref = await capability.acquire_sandbox(_ctx())
        await capability.release_sandbox(_ctx(), ref)
        assert ref == SandboxRef(provider='daytona', sandbox_id='existing')
        assert fake_daytona.sandboxes == []


class TestBackend:
    async def test_protocol_conformance(self, fake_daytona: FakeDaytona) -> None:
        backend = await DaytonaSandboxBackend.create()
        assert isinstance(backend, SandboxBackend)
        assert isinstance(backend, SupportsFilesystem)
        assert isinstance(backend, SupportsStart)
        assert backend.provider == 'daytona'

    async def test_rejects_relative_working_dir(self, fake_daytona: FakeDaytona) -> None:
        with pytest.raises(ValueError, match='workdir must be an absolute sandbox path'):
            await DaytonaSandboxBackend.create(working_dir='repo')

    async def test_rejects_relative_cwd(self, fake_daytona: FakeDaytona) -> None:
        backend = await DaytonaSandboxBackend.create()

        with pytest.raises(ValueError, match='cwd must be an absolute sandbox path'):
            await backend.run(['pwd'], cwd='repo')

    async def test_attached_start_timeout_is_typed(self, fake_daytona: FakeDaytona) -> None:
        sandbox = fake_daytona.sandbox()
        sandbox.start_error = asyncio.TimeoutError()
        with pytest.raises(DaytonaSandboxCommandTimeoutError, match='sandbox setup timed out'):
            await DaytonaSandboxBackend.connect(sandbox.id)

    async def test_process_setup_timeout_is_typed(self, fake_daytona: FakeDaytona) -> None:
        backend = await DaytonaSandboxBackend.create()
        fake_daytona.sandboxes[0].process_create_error = asyncio.TimeoutError()
        with pytest.raises(DaytonaSandboxCommandTimeoutError, match='process setup timed out'):
            await backend.start(['true'])

    async def test_argv_is_quoted_and_streams_stay_separate(self, fake_daytona: FakeDaytona) -> None:
        backend = await DaytonaSandboxBackend.create()
        sandbox = fake_daytona.sandboxes[0]
        sandbox.process_stdout = ['out', 'put']
        sandbox.process_stderr = ['error']
        sandbox.process_exit_code = 3
        result = await backend.run(['printf', 'a b'], timeout=5)
        assert (result.stdout, result.stderr, result.exit_code) == ('output', 'error', 3)
        assert sandbox.process_command == "printf 'a b'"
        assert sandbox.process_sessions == set()

    async def test_timeout_uses_one_deadline_and_kills_process(self, fake_daytona: FakeDaytona) -> None:
        backend = await DaytonaSandboxBackend.create()
        sandbox = fake_daytona.sandboxes[0]
        sandbox.process_waits_for_input = True
        with pytest.raises(TimeoutError, match='timed out and cleanup was requested'):
            await backend.run(['sleep', '30'], timeout=0.01)
        assert sandbox.process_sessions == set()

    async def test_cancellation_kills_process(self, fake_daytona: FakeDaytona) -> None:
        backend = await DaytonaSandboxBackend.create()
        sandbox = fake_daytona.sandboxes[0]
        sandbox.process_waits_for_input = True
        task = asyncio.create_task(backend.run(['sleep', '30']))
        await sandbox.process_create_started.wait()
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert sandbox.process_sessions == set()

    async def test_filesystem_roundtrip_and_metadata(self, fake_daytona: FakeDaytona) -> None:
        backend = await DaytonaSandboxBackend.create(working_dir='/workspace')
        sandbox = Sandbox(backend)
        await sandbox.fs.write_bytes('notes/a.txt', b'hello')
        assert await sandbox.fs.read_bytes('notes/a.txt') == b'hello'
        entry = await sandbox.fs.stat('notes/a.txt')
        assert (entry.path, entry.name, entry.size, entry.is_dir) == (
            '/workspace/notes/a.txt',
            'a.txt',
            5,
            False,
        )

    async def test_missing_file_uses_builtin_error(self, fake_daytona: FakeDaytona) -> None:
        backend = await DaytonaSandboxBackend.create(working_dir='/workspace')
        with pytest.raises(FileNotFoundError):
            await backend.fs.read_bytes('/missing')

    async def test_unexpected_cleanup_failure_is_visible(self, fake_daytona: FakeDaytona) -> None:
        backend = await DaytonaSandboxBackend.create()
        fake_daytona.delete_error = RuntimeError('delete failed')
        with pytest.raises(DaytonaSandboxError, match='delete failed'):
            await backend.close(terminate=True)
        assert fake_daytona.closed_clients == 0
        fake_daytona.delete_error = None
        await backend.close(terminate=True)
        assert fake_daytona.closed_clients == 1


class TestConfiguration:
    def test_defaults(self) -> None:
        capability = DaytonaSandbox()
        assert capability.snapshot is None
        assert capability.auto_stop_minutes == 60
        assert capability.network_block_all is False

    def test_configuration_is_keyword_only(self) -> None:
        assert all(
            parameter.kind is inspect.Parameter.KEYWORD_ONLY
            for parameter in inspect.signature(DaytonaSandbox).parameters.values()
        )

    @pytest.mark.parametrize('minutes', [0, -1, 1.5, True])
    def test_rejects_invalid_auto_stop(self, minutes: object) -> None:
        with pytest.raises(ValueError, match='auto_stop_minutes'):
            DaytonaSandbox(auto_stop_minutes=minutes)  # type: ignore[arg-type]

    def test_rejects_relative_workdir(self) -> None:
        with pytest.raises(ValueError, match='absolute'):
            DaytonaSandbox(workdir='repo')

    def test_rejects_non_boolean_network_setting(self) -> None:
        with pytest.raises(ValueError, match='network_block_all'):
            DaytonaSandbox(network_block_all=1)  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        'settings',
        [
            {'snapshot': 'base'},
            {'auto_stop_minutes': 30},
            {'env': {'A': 'b'}},
            {'network_block_all': True},
        ],
    )
    def test_attach_rejects_creation_settings(self, settings: dict[str, object]) -> None:
        with pytest.raises(ValueError, match='only apply when creating'):
            DaytonaSandbox(sandbox_id='existing', **settings)  # type: ignore[arg-type]

    def test_copies_environment_mapping(self) -> None:
        source = {'A': 'one'}
        capability = DaytonaSandbox(env=source)
        source['A'] = 'two'
        assert capability.env == {'A': 'one'}

    def test_public_exports_are_narrow(self) -> None:
        assert not hasattr(pydantic_ai_harness, 'DaytonaSandbox')
        assert set(daytona_sandbox.__all__) == {
            'DaytonaSandbox',
            'DaytonaSandboxAuthError',
            'DaytonaSandboxBackend',
            'DaytonaSandboxCommandTimeoutError',
            'DaytonaSandboxError',
            'DaytonaSandboxTerminalError',
            'DaytonaSandboxUnavailableError',
        }

    def test_serialization_name(self) -> None:
        assert DaytonaSandbox.get_serialization_name() == 'DaytonaSandbox'
