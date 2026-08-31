"""Focused protocol and SDK-boundary tests for Daytona sandboxes."""

from __future__ import annotations

import asyncio
import builtins
import sys
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from daytona import (
    DaytonaAuthenticationError,
    DaytonaConnectionError,
    DaytonaNotFoundError,
)
from pydantic_ai.sandboxes import SandboxRef

from pydantic_ai_harness.daytona_sandbox import (
    DaytonaSandbox,
    DaytonaSandboxAuthError,
    DaytonaSandboxBackend,
    DaytonaSandboxError,
    DaytonaSandboxUnavailableError,
)
from pydantic_ai_harness.daytona_sandbox._backend import _command_context, _command_line, _DaytonaResult
from pydantic_ai_harness.daytona_sandbox._session import DaytonaSandboxSession, _finish_cleanup

from .fake_daytona import FakeDaytona

pytestmark = pytest.mark.anyio(backends=['asyncio'])


class TestCommandPreparation:
    def test_command_forms(self) -> None:
        assert _command_line(['printf', 'a b'], False) == "printf 'a b'"
        assert _command_line('printf ok', True) == 'printf ok'
        assert _command_context('run', '/work dir', {'A': 'x y'}) == ("cd -- '/work dir' && env -- 'A=x y' sh -c run")

    @pytest.mark.parametrize(
        ('command', 'shell', 'message'),
        [
            (['echo'], True, 'argv sequence'),
            ('echo', False, 'string command'),
            ([], False, 'at least the program'),
        ],
    )
    def test_invalid_command_forms(self, command: str | list[str], shell: bool, message: str) -> None:
        with pytest.raises(TypeError, match=message):
            _command_line(command, shell)


class TestBackendLifecycle:
    def test_requires_open_session(self) -> None:
        with pytest.raises(DaytonaSandboxError, match='session is not open'):
            DaytonaSandboxBackend(DaytonaSandboxSession())

    async def test_create_passes_configuration(self, fake_daytona: FakeDaytona) -> None:
        backend = await DaytonaSandboxBackend.create(
            name='stable',
            snapshot='python',
            auto_stop_minutes=15,
            working_dir='/work',
            env={'A': 'b'},
            network_block_all=True,
        )

        params = fake_daytona.create_params[0]
        assert (params.name, params.snapshot, params.auto_stop_interval) == ('stable', 'python', 15)
        assert (params.env_vars, params.network_block_all) == ({'A': 'b'}, True)
        await backend.close(terminate=True)

    async def test_create_or_connect_recovers_from_create_race(
        self, fake_daytona: FakeDaytona, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        existing = fake_daytona.sandbox('winner')
        existing.name = 'stable'
        connect = AsyncMock(
            side_effect=[DaytonaSandboxUnavailableError('not yet'), await DaytonaSandboxBackend.connect('stable')]
        )
        create = AsyncMock(side_effect=DaytonaSandboxError('already exists'))
        monkeypatch.setattr(DaytonaSandboxBackend, 'connect', connect)
        monkeypatch.setattr(DaytonaSandboxBackend, 'create', create)

        backend = await DaytonaSandboxBackend._create_or_connect(name='stable')

        assert backend.sandbox_id == 'winner'

    async def test_create_or_connect_preserves_original_create_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        create_error = DaytonaSandboxError('create failed')
        monkeypatch.setattr(
            DaytonaSandboxBackend,
            'connect',
            AsyncMock(side_effect=DaytonaSandboxUnavailableError('missing')),
        )
        monkeypatch.setattr(DaytonaSandboxBackend, 'create', AsyncMock(side_effect=create_error))

        with pytest.raises(DaytonaSandboxError, match='create failed') as exc:
            await DaytonaSandboxBackend._create_or_connect(name='stable')
        assert exc.value is create_error

    async def test_working_directory_is_discovered_once(self, fake_daytona: FakeDaytona) -> None:
        backend = await DaytonaSandboxBackend.create()
        fake_daytona.sandboxes[0].responder = lambda command, timeout: ('/srv/repo\n', 0)

        assert await backend.working_dir() == '/srv/repo'
        assert await backend.working_dir() == '/srv/repo'
        assert len(fake_daytona.sandboxes[0].process_calls) == 5

    async def test_concurrent_working_directory_discovery_runs_one_probe(
        self, fake_daytona: FakeDaytona, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = await DaytonaSandboxBackend.create()
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def probe(*args: object, **kwargs: object) -> _DaytonaResult:
            nonlocal calls
            del args, kwargs
            calls += 1
            started.set()
            await release.wait()
            return _DaytonaResult(exit_code=0, stdout='/work\n', stderr='')

        monkeypatch.setattr(backend, 'run', probe)
        first = asyncio.create_task(backend.working_dir())
        await started.wait()
        second = asyncio.create_task(backend.working_dir())
        await asyncio.sleep(0)
        release.set()

        assert await asyncio.gather(first, second) == ['/work', '/work']
        assert calls == 1

    @pytest.mark.parametrize(('output', 'exit_code'), [('relative\n', 0), ('/srv\n', 1)])
    async def test_invalid_working_directory_probe(
        self, fake_daytona: FakeDaytona, output: str, exit_code: int
    ) -> None:
        backend = await DaytonaSandboxBackend.create()
        fake_daytona.sandboxes[0].responder = lambda command, timeout: (output, exit_code)

        with pytest.raises(DaytonaSandboxError, match='determine the working directory'):
            await backend.working_dir()

    @pytest.mark.parametrize('timeout', [0, -1.0, float('inf'), float('nan')])
    async def test_invalid_command_timeout(self, fake_daytona: FakeDaytona, timeout: float) -> None:
        backend = await DaytonaSandboxBackend.create()
        with pytest.raises(ValueError, match='positive finite'):
            await backend.start(['x'], timeout=timeout)

    async def test_start_failure_cleans_remote_session(self, fake_daytona: FakeDaytona) -> None:
        backend = await DaytonaSandboxBackend.create()
        fake_daytona.sandboxes[0].exec_error = DaytonaConnectionError('offline')

        with pytest.raises(DaytonaSandboxError, match='offline'):
            await backend.start(['x'])
        assert fake_daytona.sandboxes[0].process_sessions == set()

    async def test_wait_result_is_cached(self, fake_daytona: FakeDaytona) -> None:
        backend = await DaytonaSandboxBackend.create()
        process = await backend.start(['x'])

        assert await process.wait() is await process.wait()
        assert process.pid is None
        await process.kill()

    async def test_expired_deadline_is_rejected_before_wait(self, fake_daytona: FakeDaytona) -> None:
        backend = await DaytonaSandboxBackend.create()
        process = await backend.start(['x'], timeout=5)
        process._deadline = 0

        with pytest.raises(TimeoutError, match='timed out and cleanup was requested'):
            await process.wait()

    async def test_filesystem_facade_delegates_all_operations(self, fake_daytona: FakeDaytona) -> None:
        backend = await DaytonaSandboxBackend.create(working_dir='/work')
        await backend.fs.make_dir('pkg')
        await backend.fs.write_bytes('pkg/a.py', b'x')

        assert (await backend.fs.list_dir('.'))[0].name == 'pkg'
        assert await backend.fs.exists('pkg/a.py') is True
        await backend.fs.remove('pkg')
        assert await backend.fs.exists('pkg/a.py') is False


class TestSessionConfiguration:
    @pytest.mark.parametrize('value', [0, -1, 1.5, True])
    def test_auto_stop_must_be_positive_integer(self, value: object) -> None:
        with pytest.raises(ValueError, match='auto_stop_minutes'):
            DaytonaSandboxSession(auto_stop_minutes=value)  # type: ignore[arg-type]

    def test_attached_configuration_conflicts(self) -> None:
        with pytest.raises(ValueError, match='snapshot cannot'):
            DaytonaSandboxSession(sandbox_id='sb', snapshot='snap')
        with pytest.raises(ValueError, match='sandbox_name cannot'):
            DaytonaSandboxSession(sandbox_id='sb', sandbox_name='name')
        with pytest.raises(ValueError, match='auto_stop_minutes cannot'):
            DaytonaSandboxSession(sandbox_id='sb', auto_stop_minutes=5)
        with pytest.raises(ValueError, match='network_block_all must be a boolean'):
            DaytonaSandboxSession(network_block_all=1)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match='cannot configure an attached'):
            DaytonaSandboxSession(sandbox_id='sb', network_block_all=True)


class TestSessionLifecycle:
    async def test_session_cannot_be_entered_twice(self, fake_daytona: FakeDaytona) -> None:
        async with DaytonaSandboxSession() as session:
            with pytest.raises(DaytonaSandboxError, match='already open'):
                await session.__aenter__()

    async def test_exit_without_enter_is_safe(self) -> None:
        await DaytonaSandboxSession().__aexit__(None, None, None)

    async def test_missing_package_is_named(self, monkeypatch: pytest.MonkeyPatch) -> None:
        real_import = builtins.__import__

        def no_daytona(name: str, *args: object, **kwargs: object) -> object:
            if name == 'daytona':
                raise ImportError('missing')
            return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.delitem(sys.modules, 'daytona', raising=False)
        monkeypatch.setattr(builtins, '__import__', no_daytona)
        with pytest.raises(DaytonaSandboxError, match='package is required'):
            await DaytonaSandboxSession().__aenter__()

    async def test_release_missing_package_is_named(self, monkeypatch: pytest.MonkeyPatch) -> None:
        real_import = builtins.__import__

        def no_daytona(name: str, *args: object, **kwargs: object) -> object:
            if name == 'daytona':
                raise ImportError('missing')
            return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.delitem(sys.modules, 'daytona', raising=False)
        monkeypatch.setattr(builtins, '__import__', no_daytona)
        with pytest.raises(DaytonaSandboxError, match='package is required'):
            await DaytonaSandbox().release_sandbox(cast(Any, None), SandboxRef(provider='daytona', sandbox_id='sb'))

    @pytest.mark.parametrize(
        ('error', 'error_type'),
        [
            (DaytonaAuthenticationError('denied'), DaytonaSandboxAuthError),
            (DaytonaConnectionError('offline'), DaytonaSandboxError),
        ],
    )
    @pytest.mark.parametrize('close_fails', [False, True])
    async def test_release_translates_delete_failure_and_preserves_it_when_close_fails(
        self,
        fake_daytona: FakeDaytona,
        error: Exception,
        error_type: type[Exception],
        close_fails: bool,
    ) -> None:
        sandbox = fake_daytona.sandbox()
        fake_daytona.delete_error = error
        if close_fails:
            fake_daytona.close_error = RuntimeError('close failed')

        with pytest.raises(error_type, match='credentials' if error_type is DaytonaSandboxAuthError else 'offline'):
            await DaytonaSandbox().release_sandbox(
                cast(Any, None), SandboxRef(provider='daytona', sandbox_id=sandbox.id)
            )

        assert sandbox.start_calls == []

    async def test_release_translates_close_failure_after_delete(self, fake_daytona: FakeDaytona) -> None:
        sandbox = fake_daytona.sandbox()
        fake_daytona.close_error = DaytonaConnectionError('close failed')

        with pytest.raises(DaytonaSandboxError, match='close failed'):
            await DaytonaSandbox().release_sandbox(
                cast(Any, None), SandboxRef(provider='daytona', sandbox_id=sandbox.id)
            )

        assert sandbox.deleted is True

    async def test_attached_session_is_not_deleted(self, fake_daytona: FakeDaytona) -> None:
        sandbox = fake_daytona.sandbox()
        async with DaytonaSandboxSession(sandbox_id=sandbox.id):
            pass
        assert sandbox.started is True
        assert sandbox.start_calls == [60]
        assert sandbox.deleted is False

    async def test_missing_attached_sandbox_is_unavailable(self, fake_daytona: FakeDaytona) -> None:
        with pytest.raises(DaytonaSandboxUnavailableError):
            await DaytonaSandboxSession(sandbox_id='missing').__aenter__()
        assert fake_daytona.closed_clients == 1

    async def test_attached_connection_failure_is_translated(self, fake_daytona: FakeDaytona) -> None:
        fake_daytona.get_error = DaytonaConnectionError('offline')
        with pytest.raises(DaytonaSandboxError, match='offline'):
            await DaytonaSandboxSession(sandbox_id='existing').__aenter__()

    async def test_attached_resume_failure_is_translated(self, fake_daytona: FakeDaytona) -> None:
        sandbox = fake_daytona.sandbox()
        sandbox.start_error = DaytonaConnectionError('resume failed')

        with pytest.raises(DaytonaSandboxError, match='resume failed'):
            await DaytonaSandboxSession(sandbox_id=sandbox.id).__aenter__()
        assert fake_daytona.closed_clients == 1

    async def test_resume_failure_is_not_masked_by_close_failure(self, fake_daytona: FakeDaytona) -> None:
        sandbox = fake_daytona.sandbox()
        sandbox.start_error = DaytonaConnectionError('resume failed')
        fake_daytona.close_error = DaytonaConnectionError('close failed')
        session = DaytonaSandboxSession(sandbox_id=sandbox.id)

        with pytest.raises(DaytonaSandboxError, match='resume failed'):
            await session.__aenter__()

        fake_daytona.close_error = None
        await session.close(terminate=False)
        assert fake_daytona.closed_clients == 1

    @pytest.mark.parametrize(
        ('error', 'error_type'),
        [
            (DaytonaAuthenticationError('denied'), DaytonaSandboxAuthError),
            (DaytonaConnectionError('offline'), DaytonaSandboxError),
        ],
    )
    async def test_creation_errors_are_translated(
        self, fake_daytona: FakeDaytona, error: Exception, error_type: type[Exception]
    ) -> None:
        fake_daytona.create_error = error
        with pytest.raises(error_type):
            await DaytonaSandboxSession().__aenter__()
        assert fake_daytona.closed_clients == 1

    async def test_cancelled_creation_deletes_created_sandbox(self, fake_daytona: FakeDaytona) -> None:
        fake_daytona.create_gate = asyncio.Event()
        session = DaytonaSandboxSession()
        entering = asyncio.create_task(session.__aenter__())
        await fake_daytona.create_started.wait()
        entering.cancel()
        fake_daytona.create_gate.set()

        with pytest.raises(asyncio.CancelledError):
            await entering
        assert fake_daytona.sandboxes[0].deleted is True
        assert session.sandbox_id is None

    async def test_cancelled_creation_is_not_masked_by_close_failure(self, fake_daytona: FakeDaytona) -> None:
        fake_daytona.create_gate = asyncio.Event()
        fake_daytona.close_error = DaytonaConnectionError('close failed')
        session = DaytonaSandboxSession()
        entering = asyncio.create_task(session.__aenter__())
        await fake_daytona.create_started.wait()
        entering.cancel()
        fake_daytona.create_gate.set()

        with pytest.raises(asyncio.CancelledError):
            await entering

        fake_daytona.close_error = None
        await session.close(terminate=False)
        assert fake_daytona.closed_clients == 1

    async def test_cancelled_creation_retains_identity_when_delete_fails(self, fake_daytona: FakeDaytona) -> None:
        fake_daytona.create_gate = asyncio.Event()
        fake_daytona.delete_error = DaytonaConnectionError('delete failed')
        session = DaytonaSandboxSession()
        entering = asyncio.create_task(session.__aenter__())
        await fake_daytona.create_started.wait()
        entering.cancel()
        fake_daytona.create_gate.set()

        with pytest.raises(asyncio.CancelledError):
            await entering
        assert session.sandbox_id == fake_daytona.sandboxes[0].id

        fake_daytona.delete_error = None
        await session.close(terminate=True)
        assert fake_daytona.sandboxes[0].deleted is True

    def test_cancelled_attached_start_preserves_cancellation_when_start_fails(self, fake_daytona: FakeDaytona) -> None:
        async def scenario() -> None:
            sandbox = fake_daytona.sandbox()
            sandbox.start_gate = asyncio.Event()
            sandbox.start_error = RuntimeError('start failed')
            session = DaytonaSandboxSession(sandbox_id=sandbox.id)
            entering = asyncio.create_task(session.__aenter__())
            await sandbox.start_started.wait()
            entering.cancel()
            sandbox.start_gate.set()

            with pytest.raises(asyncio.CancelledError):
                await entering
            assert fake_daytona.closed_clients == 1

        asyncio.run(scenario())

    def test_cancelled_attached_start_preserves_cancellation_after_start_succeeds(
        self, fake_daytona: FakeDaytona
    ) -> None:
        async def scenario() -> None:
            sandbox = fake_daytona.sandbox()
            sandbox.start_gate = asyncio.Event()
            session = DaytonaSandboxSession(sandbox_id=sandbox.id)
            entering = asyncio.create_task(session.__aenter__())
            await sandbox.start_started.wait()
            entering.cancel()
            sandbox.start_gate.set()

            with pytest.raises(asyncio.CancelledError):
                await entering
            assert fake_daytona.closed_clients == 1

        asyncio.run(scenario())

    def test_cancellation_waits_for_cleanup_without_callback(self) -> None:
        async def scenario() -> None:
            gate = asyncio.Event()

            async def cleanup() -> None:
                await gate.wait()

            cleaning = asyncio.create_task(_finish_cleanup(cleanup()))
            await asyncio.sleep(0)
            cleaning.cancel()
            gate.set()

            with pytest.raises(asyncio.CancelledError):
                await cleaning

        asyncio.run(scenario())

    @pytest.mark.parametrize('close_fails', [False, True])
    def test_repeated_cancellation_waits_for_client_close(self, fake_daytona: FakeDaytona, close_fails: bool) -> None:
        async def scenario() -> None:
            fake_daytona.create_gate = asyncio.Event()
            fake_daytona.close_gate = asyncio.Event()
            if close_fails:
                fake_daytona.close_error = RuntimeError('close failed')
            session = DaytonaSandboxSession()
            entering = asyncio.create_task(session.__aenter__())
            await fake_daytona.create_started.wait()
            entering.cancel()
            fake_daytona.create_gate.set()
            await fake_daytona.close_started.wait()
            entering.cancel()
            fake_daytona.close_gate.set()

            with pytest.raises(asyncio.CancelledError):
                await entering

            if close_fails:
                assert session._client is not None
                fake_daytona.close_error = None
                await session.close(terminate=False)
            else:
                assert session.sandbox_id is None

        asyncio.run(scenario())

    async def test_already_deleted_owned_sandbox_is_cleanup_success(self, fake_daytona: FakeDaytona) -> None:
        session = DaytonaSandboxSession()
        await session.__aenter__()
        fake_daytona.delete_error = DaytonaNotFoundError('gone')

        await session.close(terminate=True)

        assert session.sandbox_id is None

    async def test_client_only_close_failure_is_translated(self, fake_daytona: FakeDaytona) -> None:
        session = DaytonaSandboxSession()
        session._client = cast(Any, fake_daytona.client())
        fake_daytona.close_error = DaytonaConnectionError('close failed')

        with pytest.raises(DaytonaSandboxError, match='close failed'):
            await session.close(terminate=False)

    async def test_client_only_close_succeeds(self, fake_daytona: FakeDaytona) -> None:
        session = DaytonaSandboxSession()
        session._client = cast(Any, fake_daytona.client())

        await session.close(terminate=False)

        assert session._client is None

    async def test_close_failure_without_delete_failure_is_reported(self, fake_daytona: FakeDaytona) -> None:
        session = DaytonaSandboxSession()
        await session.__aenter__()
        fake_daytona.close_error = DaytonaConnectionError('close failed')

        with pytest.raises(DaytonaSandboxError, match='close failed'):
            await session.close(terminate=False)

    async def test_delete_and_close_failures_preserve_delete_error(self, fake_daytona: FakeDaytona) -> None:
        session = DaytonaSandboxSession()
        await session.__aenter__()
        fake_daytona.delete_error = DaytonaConnectionError('delete failed')
        fake_daytona.close_error = DaytonaConnectionError('close failed')

        with pytest.raises(DaytonaSandboxError, match='delete failed'):
            await session.close(terminate=True)

        fake_daytona.delete_error = None
        fake_daytona.close_error = None
        await session.close(terminate=True)
        assert fake_daytona.delete_calls[-1][0] == fake_daytona.sandboxes[0].id


class TestManagedProcess:
    async def test_bidirectional_process(self, fake_daytona: FakeDaytona) -> None:
        stdout: list[str] = []
        stderr: list[str] = []
        async with DaytonaSandboxSession(workdir='/work', env={'A': 'b'}) as session:
            sandbox = fake_daytona.sandboxes[0]
            sandbox.process_stdout = ['ready']
            sandbox.process_stderr = ['warning']
            sandbox.process_stdout_after_input = ['done']
            sandbox.process_waits_for_input = True
            sandbox.process_exit_code = 3
            async with session.process(
                'broker',
                'python child.py',
                on_stdout=stdout.append,
                on_stderr=stderr.append,
                max_input_bytes=4,
                io_timeout=7,
            ) as process:
                assert process.process_id == 'broker'
                await process.send('go', timeout=5)
                assert await process.wait(timeout=6) == 3
            assert sandbox.process_command == "cd -- /work && env -- A=b sh -c 'python child.py'"
        assert (stdout, stderr) == (['ready', 'done'], ['warning'])

    def test_process_configuration_validation(self, fake_daytona: FakeDaytona) -> None:
        session = DaytonaSandboxSession()
        with pytest.raises(DaytonaSandboxError, match='session is not open'):
            session.process('id', 'x', on_stdout=lambda _: None, on_stderr=lambda _: None, max_input_bytes=1)

    async def test_open_process_guards(self, fake_daytona: FakeDaytona) -> None:
        async with DaytonaSandboxSession() as session:
            with pytest.raises(ValueError, match='process_id'):
                session.process('', 'x', on_stdout=lambda _: None, on_stderr=lambda _: None, max_input_bytes=1)
            process = session.process('id', 'x', on_stdout=lambda _: None, on_stderr=lambda _: None, max_input_bytes=1)
            with pytest.raises(DaytonaSandboxError, match='not open'):
                await process.send('x')
            await process.__aenter__()
            with pytest.raises(DaytonaSandboxError, match='already open'):
                await process.__aenter__()
            with pytest.raises(ValueError, match='over the 1-byte limit'):
                await process.send('xx')
            with pytest.raises(ValueError, match='timeout must be a positive integer'):
                await process.send('x', timeout=0)
            with pytest.raises(ValueError, match='timeout must be positive'):
                await process.wait(timeout=0)
            await process.close()
            await process.close()

    async def test_cancelled_process_start_deletes_remote_session(self, fake_daytona: FakeDaytona) -> None:
        async with DaytonaSandboxSession() as session:
            sandbox = fake_daytona.sandboxes[0]
            sandbox.process_create_gate = asyncio.Event()
            process = session.process('id', 'x', on_stdout=lambda _: None, on_stderr=lambda _: None, max_input_bytes=1)
            entering = asyncio.create_task(process.__aenter__())
            await sandbox.process_create_started.wait()
            entering.cancel()
            sandbox.process_create_gate.set()
            with pytest.raises(asyncio.CancelledError):
                await entering
            assert sandbox.process_sessions == set()

    async def test_cancelled_process_start_can_retry_failed_cleanup(self, fake_daytona: FakeDaytona) -> None:
        async with DaytonaSandboxSession() as session:
            sandbox = fake_daytona.sandboxes[0]
            sandbox.process_create_gate = asyncio.Event()
            sandbox.process_delete_error = DaytonaConnectionError('delete failed')
            process = session.process('id', 'x', on_stdout=lambda _: None, on_stderr=lambda _: None, max_input_bytes=1)
            entering = asyncio.create_task(process.__aenter__())
            await sandbox.process_create_started.wait()
            entering.cancel()
            sandbox.process_create_gate.set()

            with pytest.raises(asyncio.CancelledError):
                await entering

            sandbox.process_delete_error = None
            await process.close()
            assert sandbox.process_sessions == set()

    async def test_async_output_handlers_are_awaited(self, fake_daytona: FakeDaytona) -> None:
        seen: list[str] = []

        async def capture(chunk: str) -> None:
            seen.append(chunk)

        async with DaytonaSandboxSession() as session:
            sandbox = fake_daytona.sandboxes[0]
            sandbox.process_stdout = ['out']
            sandbox.process_stderr = ['err']
            sandbox.process_stdout_after_input = ['after-out']
            sandbox.process_stderr_after_input = ['after-err']
            async with session.process('id', 'x', on_stdout=capture, on_stderr=capture, max_input_bytes=1) as process:
                assert await process.wait(timeout=1) == 0
        assert seen == ['out', 'err', 'after-out', 'after-err']

    async def test_clear_cancels_pending_log_task(self, fake_daytona: FakeDaytona) -> None:
        async with DaytonaSandboxSession() as session:
            process = session.process('id', 'x', on_stdout=lambda _: None, on_stderr=lambda _: None, max_input_bytes=1)
            await process.__aenter__()
            pending = asyncio.create_task(asyncio.sleep(60))
            process._logs = pending

            process._clear()

            await asyncio.sleep(0)
            assert pending.cancelled()

    async def test_missing_exit_status_is_reported(self, fake_daytona: FakeDaytona) -> None:
        async with DaytonaSandboxSession() as session:
            fake_daytona.sandboxes[0].process_exit_code = None
            fake_daytona.sandboxes[0].process_stdout = ['done']
            async with session.process(
                'id', 'x', on_stdout=lambda _: None, on_stderr=lambda _: None, max_input_bytes=1
            ) as process:
                with pytest.raises(  # pragma: no branch - the context manager must observe this one failure
                    DaytonaSandboxError, match='before reporting an exit status'
                ):
                    await process.wait(timeout=1)

    async def test_process_cleanup_can_be_retried(self, fake_daytona: FakeDaytona) -> None:
        async with DaytonaSandboxSession() as session:
            sandbox = fake_daytona.sandboxes[0]
            process = session.process('id', 'x', on_stdout=lambda _: None, on_stderr=lambda _: None, max_input_bytes=1)
            await process.__aenter__()
            sandbox.process_delete_error = DaytonaConnectionError('offline')
            with pytest.raises(DaytonaSandboxError, match='offline'):
                await process.close()
            sandbox.process_delete_error = None
            await process.close()

    async def test_input_and_status_errors_are_translated(self, fake_daytona: FakeDaytona) -> None:
        async with DaytonaSandboxSession() as session:
            sandbox = fake_daytona.sandboxes[0]
            process = session.process('id', 'x', on_stdout=lambda _: None, on_stderr=lambda _: None, max_input_bytes=1)
            await process.__aenter__()
            sandbox.process_input_error = DaytonaNotFoundError('gone')
            with pytest.raises(DaytonaSandboxUnavailableError):
                await process.send('x')
            sandbox.process_input_error = None
            sandbox.process_status_error = DaytonaAuthenticationError('denied')
            with pytest.raises(DaytonaSandboxAuthError):
                await process.wait(timeout=1)
            await process.close()


class TestFilesystem:
    async def test_directory_operations(self, fake_daytona: FakeDaytona) -> None:
        async with DaytonaSandboxSession(workdir='/work') as session:
            await session.make_dir('pkg')
            await session.write_bytes('pkg/a.py', b'x')
            sandbox = fake_daytona.sandboxes[0]
            sandbox.files['/work/readme'] = b'hi'
            sandbox.files['/other/file'] = b'ignored'
            sandbox.files['/work/'] = b'ignored empty name'
            sandbox.directories.update({'/other', '/work/'})
            entries = await session.list_entries('.')
            assert entries == [
                ('pkg', '/work/pkg', True, None),
                ('readme', '/work/readme', False, 2),
            ]
            assert await session.file_info('pkg') == ('pkg', '/work/pkg', True, None)
            assert await session.exists('pkg/a.py') is True
            assert await session.exists('missing') is False
            await session.remove('pkg')
            assert await session.exists('pkg/a.py') is False

    async def test_file_and_generic_errors_are_translated(self, fake_daytona: FakeDaytona) -> None:
        async with DaytonaSandboxSession() as session:
            sandbox = fake_daytona.sandboxes[0]
            sandbox.fs_error = DaytonaNotFoundError('gone')
            with pytest.raises(FileNotFoundError):
                await session.file_info('missing')
            with pytest.raises(FileNotFoundError):
                await session.read_bytes('missing')
            with pytest.raises(FileNotFoundError):
                await session.list_entries('missing')
            with pytest.raises(FileNotFoundError):
                await session.remove('missing')
            sandbox.fs_error = DaytonaAuthenticationError('denied')
            with pytest.raises(DaytonaSandboxAuthError):
                await session.make_dir('pkg')
            with pytest.raises(DaytonaSandboxAuthError):
                await session.write_bytes('a', b'x')
            with pytest.raises(DaytonaSandboxAuthError):
                await session.exists('a')
            with pytest.raises(DaytonaSandboxAuthError):
                await session.file_info('a')

    async def test_failed_parent_creation_is_reported(self, fake_daytona: FakeDaytona) -> None:
        async with DaytonaSandboxSession() as session:
            fake_daytona.sandboxes[0].mkdir_exit_code = 1
            with pytest.raises(DaytonaSandboxError, match='Could not create'):
                await session.write_bytes('pkg/a.py', b'x')

    async def test_parent_creation_sdk_failure_is_translated(self, fake_daytona: FakeDaytona) -> None:
        async with DaytonaSandboxSession() as session:
            fake_daytona.sandboxes[0].exec_error = DaytonaConnectionError('offline')
            with pytest.raises(DaytonaSandboxError, match='offline'):
                await session.write_bytes('pkg/a.py', b'x')
