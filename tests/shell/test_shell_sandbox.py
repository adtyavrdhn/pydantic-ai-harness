"""Tests for running shell tools through the core sandbox facade."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Mapping
from pathlib import Path

import anyio
import pytest
from pydantic_ai import RunContext
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.models.test import TestModel
from pydantic_ai.sandboxes import (
    CommandResult,
    LocalSandbox,
    Sandbox,
    SandboxCommand,
    SandboxFilesystem,
    SandboxResult,
    SandboxTimeoutError,
    SandboxUnavailableError,
)
from pydantic_ai.usage import RunUsage

from pydantic_ai_harness.shell import _toolset as shell_toolset
from pydantic_ai_harness.shell._toolset import ShellToolset

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return 'asyncio'


@pytest.fixture(autouse=True)
def short_kill_grace_period(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shell_toolset, '_KILL_GRACE_PERIOD', 0.05)


@pytest.fixture
async def sandbox(tmp_path: Path) -> AsyncIterator[Sandbox]:
    async with LocalSandbox(root=tmp_path) as backend:
        yield Sandbox.wrap(backend)


def _ctx(sandbox: Sandbox | None = None) -> RunContext[None]:
    if sandbox is None:
        return RunContext[None](deps=None, model=TestModel(), usage=RunUsage(), prompt=None, messages=[], run_step=0)
    return RunContext[None](
        deps=None,
        model=TestModel(),
        usage=RunUsage(),
        prompt=None,
        messages=[],
        run_step=0,
        sandbox=sandbox,
    )


def _toolset(
    cwd: Path,
    *,
    persist_cwd: bool = False,
    max_output_chars: int = 50_000,
    env: Mapping[str, str] | None = None,
    denied_env_patterns: tuple[str, ...] = (),
    denied_commands: tuple[str, ...] = (),
) -> ShellToolset[None]:
    return ShellToolset(
        cwd=cwd,
        allowed_commands=[],
        denied_commands=denied_commands,
        denied_operators=[],
        default_timeout=10.0,
        max_output_chars=max_output_chars,
        persist_cwd=persist_cwd,
        allow_interactive=False,
        env=env,
        denied_env_patterns=denied_env_patterns,
    )


async def _call(
    toolset: ShellToolset[None],
    ctx: RunContext[None],
    name: str,
    tool_args: dict[str, object],
) -> str:
    tools = await toolset.get_tools(ctx)
    result: object = await toolset.call_tool(name, tool_args, ctx, tools[name])
    assert isinstance(result, str)
    return result


def _command_id(result: str) -> str:
    return result.rsplit('ID: ', 1)[1].strip()


class _ControllableFilesystem:
    def __init__(self, filesystem: SandboxFilesystem) -> None:
        self.filesystem = filesystem
        self.read_error: RuntimeError | None = None
        self.remove_error: RuntimeError | None = None

    async def read_bytes(self, path: str) -> bytes:
        if self.read_error is not None:
            raise self.read_error
        return await self.filesystem.read_bytes(path)

    async def remove(self, path: str) -> None:
        if self.remove_error is not None:
            raise self.remove_error
        await self.filesystem.remove(path)


class _RecordingLocalBackend:
    provider = 'recording-local'

    def __init__(self, backend: LocalSandbox) -> None:
        self.backend = backend
        self.fs = _ControllableFilesystem(backend.fs)
        self.environments: list[Mapping[str, str] | None] = []
        self.run_error: RuntimeError | None = None
        self.raise_after_kill = False
        self.sandbox_id = backend.sandbox_id

    async def working_dir(self) -> str:
        return await self.backend.working_dir()

    async def run(
        self,
        command: SandboxCommand,
        *,
        shell: bool = False,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> SandboxResult:
        self.environments.append(env)
        if self.run_error is not None:
            raise self.run_error
        result = await self.backend.run(command, shell=shell, cwd=cwd, env=env, timeout=timeout)
        if self.raise_after_kill and not isinstance(command, str) and command[:2] == ['kill', '-TERM']:
            raise RuntimeError('cleanup failed')
        return result


class _FailingBackend:
    provider = 'failing'
    sandbox_id = 'failing-1'

    def __init__(self, error: BaseException) -> None:
        self.error = error

    async def working_dir(self) -> str:
        return '/work'

    async def run(
        self,
        command: SandboxCommand,
        *,
        shell: bool = False,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> SandboxResult:
        raise self.error


class _TimeoutBackend(_FailingBackend):
    def __init__(self) -> None:
        super().__init__(SandboxTimeoutError('timed out', stdout='before\n', stderr='problem\n'))


class _ResultBackend(_FailingBackend):
    def __init__(self, stdout: str, stderr: str = '') -> None:
        super().__init__(RuntimeError('unused'))
        self.stdout = stdout
        self.stderr = stderr

    async def run(
        self,
        command: SandboxCommand,
        *,
        shell: bool = False,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> SandboxResult:
        return CommandResult(exit_code=0, stdout=self.stdout, stderr=self.stderr)


class TestRunCommand:
    async def test_runs_in_sandbox_root_and_labels_output(self, tmp_path: Path, sandbox: Sandbox) -> None:
        result = await _call(_toolset(tmp_path / 'host-only'), _ctx(sandbox), 'run_command', {'command': 'pwd'})
        assert result == f'[stdout]\n{tmp_path}\n'

    async def test_nonzero_exit_code_is_rendered(self, tmp_path: Path, sandbox: Sandbox) -> None:
        result = await _call(
            _toolset(tmp_path),
            _ctx(sandbox),
            'run_command',
            {'command': 'printf error >&2; exit 7'},
        )
        assert result == '[stderr]\nerror\n[exit code: 7]'

    async def test_host_environment_is_not_passed_to_sandbox(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv('HARNESS_HOST_ONLY', 'secret')
        async with LocalSandbox(root=tmp_path) as local:
            backend = _RecordingLocalBackend(local)
            await _call(_toolset(tmp_path), _ctx(Sandbox.wrap(backend)), 'run_command', {'command': 'true'})
        assert backend.environments == [None]

    async def test_explicit_environment_is_filtered(self, tmp_path: Path, sandbox: Sandbox) -> None:
        toolset = _toolset(
            tmp_path,
            env={'HARNESS_VISIBLE': 'yes', 'HARNESS_DENIED': 'secret'},
            denied_env_patterns=('HARNESS_DENIED',),
        )
        result = await _call(
            toolset,
            _ctx(sandbox),
            'run_command',
            {'command': 'printf \'%s:%s\' "$HARNESS_VISIBLE" "${HARNESS_DENIED-absent}"'},
        )
        assert result == '[stdout]\nyes:absent'

    async def test_persist_cwd_tracks_only_successful_absolute_capture(self, tmp_path: Path, sandbox: Sandbox) -> None:
        (tmp_path / 'sub').mkdir()
        toolset = _toolset(tmp_path / 'host-only', persist_cwd=True)
        ctx = _ctx(sandbox)

        await _call(toolset, ctx, 'run_command', {'command': 'cd sub'})
        expected = f'[stdout]\n{tmp_path / "sub"}\n'
        assert await _call(toolset, ctx, 'run_command', {'command': 'pwd'}) == expected

        await _call(toolset, ctx, 'run_command', {'command': 'cd ..; false'})
        assert await _call(toolset, ctx, 'run_command', {'command': 'pwd'}) == expected

        await _call(toolset, ctx, 'run_command', {'command': 'pwd() { printf relative; }'})
        assert toolset._sandbox_cwd == str(tmp_path / 'sub')

    async def test_invalid_utf8_cwd_capture_is_ignored(self, tmp_path: Path, sandbox: Sandbox) -> None:
        toolset = _toolset(tmp_path, persist_cwd=True)
        await _call(toolset, _ctx(sandbox), 'run_command', {'command': "pwd() { printf '\\377'; }"})
        assert toolset._sandbox_cwd is None

    async def test_cwd_capture_cleanup_is_best_effort(self, tmp_path: Path) -> None:
        async with LocalSandbox(root=tmp_path) as local:
            backend = _RecordingLocalBackend(local)
            backend.fs.remove_error = RuntimeError('remove failed')
            result = await _call(
                _toolset(tmp_path, persist_cwd=True),
                _ctx(Sandbox.wrap(backend)),
                'run_command',
                {'command': 'pwd'},
            )
        assert result == f'[stdout]\n{tmp_path}\n'

    @pytest.mark.parametrize(
        ('error', 'expected'),
        [
            (SandboxUnavailableError('gone'), SandboxUnavailableError),
            (RuntimeError('temporary failure'), None),
        ],
    )
    async def test_cwd_capture_read_error_mapping(
        self,
        tmp_path: Path,
        error: RuntimeError,
        expected: type[RuntimeError] | None,
    ) -> None:
        # A dead sandbox propagates; any other capture-read failure is dropped bookkeeping,
        # because the command itself already succeeded and a retry would re-run it.
        async with LocalSandbox(root=tmp_path) as local:
            backend = _RecordingLocalBackend(local)
            backend.fs.read_error = error
            toolset = _toolset(tmp_path, persist_cwd=True)
            ctx = _ctx(Sandbox.wrap(backend))
            if expected is None:
                result = await _call(toolset, ctx, 'run_command', {'command': 'pwd'})
                assert '[stdout]' in result
            else:
                with pytest.raises(expected, match=str(error)):
                    await _call(toolset, ctx, 'run_command', {'command': 'pwd'})

    async def test_timeout_is_returned(self, tmp_path: Path, sandbox: Sandbox) -> None:
        result = await _call(
            _toolset(tmp_path),
            _ctx(sandbox),
            'run_command',
            {'command': 'sleep 10', 'timeout_seconds': 0.05},
        )
        assert result == '[Command timed out after 0.05s]'

    async def test_timeout_includes_partial_output(self, tmp_path: Path) -> None:
        result = await _call(
            _toolset(tmp_path),
            _ctx(Sandbox.wrap(_TimeoutBackend())),
            'run_command',
            {'command': 'slow'},
        )
        assert result == '[stdout]\nbefore\n\n[stderr]\nproblem\n\n[Command timed out after 10.0s]'

    async def test_policy_and_output_cap_apply(self, tmp_path: Path, sandbox: Sandbox) -> None:
        with pytest.raises(ModelRetry, match="Command 'rm' is denied"):
            await _call(_toolset(tmp_path, denied_commands=('rm',)), _ctx(sandbox), 'run_command', {'command': 'rm x'})

        result = await _call(
            _toolset(tmp_path, max_output_chars=24),
            _ctx(sandbox),
            'run_command',
            {'command': "printf 'abcdefghijklmnopqrstuvwxyz'"},
        )
        assert len(result) == 24
        assert result.endswith('tuvwxyz')

    async def test_host_mode_still_executes_on_host(self, tmp_path: Path) -> None:
        result = await _call(_toolset(tmp_path), _ctx(), 'run_command', {'command': 'pwd'})
        assert result == f'[stdout]\n{tmp_path}\n'

    @pytest.mark.parametrize(
        ('error', 'expected'),
        [
            (SandboxUnavailableError('gone'), SandboxUnavailableError),
            (RuntimeError('temporary failure'), ModelRetry),
        ],
    )
    async def test_error_mapping(
        self,
        tmp_path: Path,
        error: RuntimeError,
        expected: type[RuntimeError],
    ) -> None:
        sandbox = Sandbox.wrap(_FailingBackend(error))
        assert await sandbox.working_dir() == '/work'
        with pytest.raises(expected, match=str(error)):
            await _call(_toolset(tmp_path), _ctx(sandbox), 'run_command', {'command': 'echo hello'})


def _background_toolset(tmp_path: Path) -> ShellToolset[None]:
    # A python shim on PATH, unconditionally: macOS ships no setsid binary, and one code
    # path for every platform keeps this file fully covered on all of them.
    setsid = tmp_path / 'setsid'
    setsid.write_text(
        '#!/usr/bin/env python3\nimport os, sys\nos.setsid()\nos.execvp(sys.argv[1], sys.argv[1:])\n',
        encoding='utf-8',
    )
    setsid.chmod(0o755)
    return _toolset(tmp_path, env={'PATH': f'{tmp_path}:{os.environ["PATH"]}'})


class TestBackgroundCommands:
    async def test_short_command_finishes_with_output(self, tmp_path: Path, sandbox: Sandbox) -> None:
        toolset = _background_toolset(tmp_path)
        ctx = _ctx(sandbox)
        command_id = _command_id(await _call(toolset, ctx, 'start_command', {'command': 'printf background; false'}))

        with anyio.fail_after(5):
            while True:
                result = await _call(toolset, ctx, 'check_command', {'command_id': command_id})
                if '[status: finished]' in result:
                    break
                await anyio.sleep(0.02)

        assert result == '[stdout]\nbackground\n[status: finished]\n[exit code: 1]'
        await _call(toolset, ctx, 'stop_command', {'command_id': command_id})

    async def test_stderr_and_junk_exit_capture_are_rendered_as_running(self, tmp_path: Path, sandbox: Sandbox) -> None:
        toolset = _background_toolset(tmp_path)
        ctx = _ctx(sandbox)
        command_id = _command_id(
            await _call(toolset, ctx, 'start_command', {'command': 'printf problem >&2; sleep 30'})
        )
        await sandbox.fs.write_bytes(f'/tmp/harness_{command_id}_ec', b'junk')

        with anyio.fail_after(5):
            while True:
                result = await _call(toolset, ctx, 'check_command', {'command_id': command_id})
                if '[stderr]\nproblem' in result:
                    break
                await anyio.sleep(0.02)
        assert result == '[stderr]\nproblem\n[status: running]'
        await _call(toolset, ctx, 'stop_command', {'command_id': command_id})

    async def test_missing_output_files_are_empty(self, tmp_path: Path, sandbox: Sandbox) -> None:
        toolset = _background_toolset(tmp_path)
        ctx = _ctx(sandbox)
        command_id = _command_id(await _call(toolset, ctx, 'start_command', {'command': 'sleep 30'}))
        await sandbox.fs.remove(f'/tmp/harness_{command_id}_out')
        await sandbox.fs.remove(f'/tmp/harness_{command_id}_err')

        result = await _call(toolset, ctx, 'check_command', {'command_id': command_id})
        assert result == '(no output yet)\n[status: running]'
        await _call(toolset, ctx, 'stop_command', {'command_id': command_id})

    async def test_stop_kills_command_and_removes_record(self, tmp_path: Path, sandbox: Sandbox) -> None:
        toolset = _background_toolset(tmp_path)
        ctx = _ctx(sandbox)
        command_id = _command_id(await _call(toolset, ctx, 'start_command', {'command': 'sleep 30'}))

        result = await _call(toolset, ctx, 'stop_command', {'command_id': command_id})
        assert result == '(no output)\n[stopped]'
        assert await _call(toolset, ctx, 'check_command', {'command_id': command_id}) == (
            f'[Error: unknown command ID {command_id!r}]'
        )

    async def test_exit_cleans_up_unfinished_command(self, tmp_path: Path, sandbox: Sandbox) -> None:
        toolset = _background_toolset(tmp_path)
        ctx = _ctx(sandbox)
        command_id = _command_id(await _call(toolset, ctx, 'start_command', {'command': 'sleep 30'}))
        paths = [Path(f'/tmp/harness_{command_id}_{suffix}') for suffix in ('out', 'err', 'ec')]

        await toolset.__aexit__(None, None, None)

        assert not any(path.exists() for path in paths)
        assert await _call(toolset, ctx, 'check_command', {'command_id': command_id}) == (
            f'[Error: unknown command ID {command_id!r}]'
        )

    async def test_exit_cleans_up_finished_command(self, tmp_path: Path, sandbox: Sandbox) -> None:
        toolset = _background_toolset(tmp_path)
        ctx = _ctx(sandbox)
        command_id = _command_id(await _call(toolset, ctx, 'start_command', {'command': 'true'}))
        with anyio.fail_after(5):
            while True:
                result = await _call(toolset, ctx, 'check_command', {'command_id': command_id})
                if '[status: finished]' in result:
                    break
                await anyio.sleep(0.02)

        await toolset.__aexit__(None, None, None)
        assert await _call(toolset, ctx, 'check_command', {'command_id': command_id}) == (
            f'[Error: unknown command ID {command_id!r}]'
        )

    async def test_exit_swallows_sandbox_cleanup_failure(self, tmp_path: Path) -> None:
        async with LocalSandbox(root=tmp_path) as local:
            backend = _RecordingLocalBackend(local)
            sandbox = Sandbox.wrap(backend)
            toolset = _background_toolset(tmp_path)
            ctx = _ctx(sandbox)
            command_id = _command_id(await _call(toolset, ctx, 'start_command', {'command': 'sleep 30'}))
            backend.raise_after_kill = True
            # The protocol members the shell tools never consult still work through the facade.
            assert sandbox.sandbox_id == local.sandbox_id
            assert await sandbox.working_dir() == str(tmp_path)

            await toolset.__aexit__(None, None, None)

            assert await _call(toolset, ctx, 'check_command', {'command_id': command_id}) == (
                f'[Error: unknown command ID {command_id!r}]'
            )
            for suffix in ('out', 'err', 'ec'):
                try:
                    await backend.fs.remove(f'/tmp/harness_{command_id}_{suffix}')
                except FileNotFoundError:
                    pass

    async def test_unknown_id_messages_are_unchanged(self, tmp_path: Path, sandbox: Sandbox) -> None:
        toolset = _toolset(tmp_path)
        ctx = _ctx(sandbox)
        assert await _call(toolset, ctx, 'check_command', {'command_id': 'missing'}) == (
            "[Error: unknown command ID 'missing']"
        )
        assert await _call(toolset, ctx, 'stop_command', {'command_id': 'missing'}) == (
            "[Error: unknown command ID 'missing']"
        )

    @pytest.mark.parametrize(
        ('backend', 'message'),
        [
            (_ResultBackend('not-a-pid', 'setsid failed'), 'setsid failed'),
            (_ResultBackend(''), 'Sandbox did not return a background process ID.'),
            (_FailingBackend(SandboxUnavailableError('gone')), 'gone'),
            (_FailingBackend(RuntimeError('temporary failure')), 'temporary failure'),
        ],
    )
    async def test_start_error_mapping(self, tmp_path: Path, backend: _FailingBackend, message: str) -> None:
        expected = SandboxUnavailableError if isinstance(backend.error, SandboxUnavailableError) else ModelRetry
        with pytest.raises(expected, match=message):
            await _call(
                _toolset(tmp_path),
                _ctx(Sandbox.wrap(backend)),
                'start_command',
                {'command': 'echo hello'},
            )

    @pytest.mark.parametrize(
        ('error', 'expected'),
        [
            (SandboxUnavailableError('gone'), SandboxUnavailableError),
            (RuntimeError('temporary failure'), ModelRetry),
        ],
    )
    async def test_check_error_mapping(
        self,
        tmp_path: Path,
        error: RuntimeError,
        expected: type[RuntimeError],
    ) -> None:
        async with LocalSandbox(root=tmp_path) as local:
            backend = _RecordingLocalBackend(local)
            sandbox = Sandbox.wrap(backend)
            toolset = _background_toolset(tmp_path)
            ctx = _ctx(sandbox)
            command_id = _command_id(await _call(toolset, ctx, 'start_command', {'command': 'sleep 30'}))
            backend.fs.read_error = error
            with pytest.raises(expected, match=str(error)):
                await _call(toolset, ctx, 'check_command', {'command_id': command_id})
            backend.fs.read_error = None
            await _call(toolset, ctx, 'stop_command', {'command_id': command_id})

    @pytest.mark.parametrize(
        ('error', 'expected'),
        [
            (SandboxUnavailableError('gone'), SandboxUnavailableError),
            (RuntimeError('temporary failure'), ModelRetry),
        ],
    )
    async def test_stop_error_mapping(
        self,
        tmp_path: Path,
        error: RuntimeError,
        expected: type[RuntimeError],
    ) -> None:
        async with LocalSandbox(root=tmp_path) as local:
            backend = _RecordingLocalBackend(local)
            sandbox = Sandbox.wrap(backend)
            toolset = _background_toolset(tmp_path)
            ctx = _ctx(sandbox)
            command_id = _command_id(await _call(toolset, ctx, 'start_command', {'command': 'sleep 30'}))
            backend.run_error = error
            with pytest.raises(expected, match=str(error)):
                await _call(toolset, ctx, 'stop_command', {'command_id': command_id})
            backend.run_error = None
            await _call(toolset, ctx, 'stop_command', {'command_id': command_id})
