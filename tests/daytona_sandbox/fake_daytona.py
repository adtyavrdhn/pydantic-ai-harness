"""Controllable fake for the Daytona SDK boundary."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Protocol

from daytona import DaytonaNotFoundError


class CreateParams(Protocol):
    name: str | None
    snapshot: str | None
    auto_stop_interval: int | None
    auto_delete_interval: int | None
    env_vars: dict[str, str] | None
    network_block_all: bool | None


@dataclass
class ExecCall:
    command: str
    cwd: str | None
    env: dict[str, str] | None
    timeout: int | None


class FakeProcess:
    def __init__(self, owner: FakeSandbox) -> None:
        self.owner = owner

    async def exec(
        self,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: int | None = None,
    ) -> SimpleNamespace:
        self.owner.exec_calls.append(ExecCall(command, cwd, env, timeout))
        if self.owner.exec_error is not None:
            raise self.owner.exec_error
        assert command.startswith('mkdir -p -- ')
        return SimpleNamespace(result='', exit_code=self.owner.mkdir_exit_code)

    async def create_session(self, session_id: str, request_timeout: float | None = None) -> None:
        self.owner.process_calls.append(('create', session_id, request_timeout))
        self.owner.process_create_started.set()
        if self.owner.process_create_error is not None:
            raise self.owner.process_create_error
        if self.owner.process_create_gate is not None:
            await self.owner.process_create_gate.wait()
        self.owner.process_sessions.add(session_id)

    async def execute_session_command(
        self,
        session_id: str,
        request: object,
        timeout: int | None = None,
    ) -> SimpleNamespace:
        self.owner.process_calls.append(('execute', session_id, timeout))
        if self.owner.exec_error is not None:
            raise self.owner.exec_error
        self.owner.process_command = getattr(request, 'command')
        self.owner.process_run_async = getattr(request, 'run_async')
        self.owner.process_suppress_input_echo = getattr(request, 'suppress_input_echo')
        if not self.owner.process_stdout and not self.owner.process_stderr and not self.owner.process_waits_for_input:
            output, self.owner.process_exit_code = self.owner.responder(self.owner.process_command, timeout)
            self.owner.process_stdout = [output]
        return SimpleNamespace(cmd_id='cmd-1')

    async def get_session_command_logs_async(
        self,
        session_id: str,
        command_id: str,
        on_stdout: Callable[[str], Awaitable[None] | None],
        on_stderr: Callable[[str], Awaitable[None] | None],
    ) -> None:
        self.owner.process_calls.append(('logs', session_id, command_id))
        for handler, chunks in (
            (on_stdout, self.owner.process_stdout),
            (on_stderr, self.owner.process_stderr),
        ):
            for chunk in chunks:
                result = handler(chunk)
                if inspect.isawaitable(result):
                    await result
        if self.owner.process_waits_for_input:
            await self.owner.process_input_event.wait()
        for handler, chunks in (
            (on_stdout, self.owner.process_stdout_after_input),
            (on_stderr, self.owner.process_stderr_after_input),
        ):
            for chunk in chunks:
                result = handler(chunk)
                if inspect.isawaitable(result):
                    await result

    async def send_session_command_input(
        self,
        session_id: str,
        command_id: str,
        data: str,
        request_timeout: float | None = None,
    ) -> None:
        self.owner.process_calls.append(('input', session_id, command_id, data, request_timeout))
        if self.owner.process_input_error is not None:
            raise self.owner.process_input_error
        self.owner.process_input_event.set()

    async def get_session_command(
        self,
        session_id: str,
        command_id: str,
        request_timeout: float | None = None,
    ) -> SimpleNamespace:
        self.owner.process_calls.append(('status', session_id, command_id, request_timeout))
        if self.owner.process_status_error is not None:
            raise self.owner.process_status_error
        return SimpleNamespace(exit_code=self.owner.process_exit_code)

    async def delete_session(self, session_id: str, request_timeout: float | None = None) -> None:
        self.owner.process_calls.append(('delete', session_id, request_timeout))
        if self.owner.process_delete_error is not None:
            raise self.owner.process_delete_error
        self.owner.process_sessions.discard(session_id)
        self.owner.process_input_event.set()


class FakeFileSystem:
    def __init__(self, owner: FakeSandbox) -> None:
        self.owner = owner

    async def get_file_info(self, path: str) -> SimpleNamespace:
        self._raise_if_needed()
        if path in self.owner.directories:
            return SimpleNamespace(size=0, is_dir=True)
        data = self.owner.files.get(path)
        if data is None:
            raise DaytonaNotFoundError(f'no file: {path}')
        size = self.owner.reported_sizes.get(path, len(data))
        return SimpleNamespace(size=size, is_dir=False)

    async def download_file(self, path: str) -> bytes:
        self._raise_if_needed()
        data = self.owner.files.get(path)
        if data is None:
            raise DaytonaNotFoundError(f'no file: {path}')
        return data

    async def upload_file(self, data: bytes, path: str) -> None:
        self._raise_if_needed()
        self.owner.files[path] = data

    async def list_files(self, path: str) -> list[SimpleNamespace]:
        self._raise_if_needed()
        prefix = '' if path in ('', '.') else path.rstrip('/') + '/'
        entries: dict[str, bool] = {}
        for candidate in self.owner.files:
            if not candidate.startswith(prefix):
                continue
            relative = candidate[len(prefix) :]
            name, separator, _ = relative.partition('/')
            if name:
                entries[name] = bool(separator) or entries.get(name, False)
        for directory in self.owner.directories:
            if not directory.startswith(prefix):
                continue
            relative = directory[len(prefix) :]
            name, separator, _ = relative.partition('/')
            if name:
                entries[name] = bool(separator) or True
        return [
            SimpleNamespace(
                name=name,
                is_dir=is_dir,
                size=0 if is_dir else len(self.owner.files[prefix + name]),
            )
            for name, is_dir in entries.items()
        ]

    async def create_folder(self, path: str, mode: str) -> None:
        self._raise_if_needed()
        assert mode == '755'
        self.owner.directories.add(path)

    async def delete_file(self, path: str, recursive: bool = False) -> None:
        self._raise_if_needed()
        self.owner.files.pop(path, None)
        self.owner.directories.discard(path)
        if recursive:  # pragma: no branch - the sandbox protocol always requests recursive removal
            prefix = path.rstrip('/') + '/'
            self.owner.files = {key: value for key, value in self.owner.files.items() if not key.startswith(prefix)}
            self.owner.directories = {key for key in self.owner.directories if not key.startswith(prefix)}

    def _raise_if_needed(self) -> None:
        if self.owner.fs_error is not None:
            raise self.owner.fs_error


class FakeSandbox:
    def __init__(self, sandbox_id: str, name: str | None = None) -> None:
        self.id = sandbox_id
        self.name = name or sandbox_id
        self.deleted = False
        self.started = False
        self.start_calls: list[float | None] = []
        self.start_error: Exception | None = None
        self.files: dict[str, bytes] = {}
        self.directories: set[str] = set()
        self.reported_sizes: dict[str, int] = {}
        self.exec_calls: list[ExecCall] = []
        self.exec_error: Exception | None = None
        self.fs_error: Exception | None = None
        self.mkdir_exit_code = 0
        self.responder: Callable[[str, int | None], tuple[str, int]] = lambda command, timeout: ('', 0)
        self.process_calls: list[tuple[object, ...]] = []
        self.process_sessions: set[str] = set()
        self.process_command = ''
        self.process_run_async: bool | None = None
        self.process_suppress_input_echo: bool | None = None
        self.process_stdout: list[str] = []
        self.process_stderr: list[str] = []
        self.process_stdout_after_input: list[str] = []
        self.process_stderr_after_input: list[str] = []
        self.process_waits_for_input = False
        self.process_input_event = asyncio.Event()
        self.process_exit_code: int | None = 0
        self.process_delete_error: Exception | None = None
        self.process_create_error: Exception | None = None
        self.process_input_error: Exception | None = None
        self.process_status_error: Exception | None = None
        self.process_create_gate: asyncio.Event | None = None
        self.process_create_started = asyncio.Event()
        self.process = FakeProcess(self)
        self.fs = FakeFileSystem(self)

    async def start(self, timeout: float | None = 60) -> None:
        self.start_calls.append(timeout)
        if self.start_error is not None:
            raise self.start_error
        self.started = True


class FakeClient:
    def __init__(self, owner: FakeDaytona) -> None:
        self.owner = owner
        self.closed = False

    async def create(self, params: CreateParams) -> FakeSandbox:
        self.owner.create_started.set()
        if self.owner.create_gate is not None:
            await self.owner.create_gate.wait()
        if self.owner.create_error is not None:
            raise self.owner.create_error
        sandbox = FakeSandbox(f'sb-{len(self.owner.sandboxes) + 1}', params.name)
        self.owner.sandboxes.append(sandbox)
        self.owner.create_params.append(params)
        return sandbox

    async def get(self, sandbox_id: str) -> FakeSandbox:
        self.owner.get_calls.append(sandbox_id)
        if self.owner.get_error is not None:
            raise self.owner.get_error
        for sandbox in self.owner.sandboxes:
            if sandbox.id == sandbox_id or sandbox.name == sandbox_id:
                return sandbox
        raise DaytonaNotFoundError(f'no sandbox: {sandbox_id}')

    async def delete(self, sandbox: FakeSandbox, timeout: float, wait: bool) -> None:
        self.owner.delete_calls.append((sandbox.id, timeout, wait))
        if self.owner.delete_error is not None:
            raise self.owner.delete_error
        sandbox.deleted = True

    async def close(self) -> None:
        if self.owner.close_error is not None:
            raise self.owner.close_error
        self.closed = True
        self.owner.closed_clients += 1


class FakeDaytona:
    def __init__(self) -> None:
        self.sandboxes: list[FakeSandbox] = []
        self.create_params: list[CreateParams] = []
        self.delete_calls: list[tuple[str, float, bool]] = []
        self.get_calls: list[str] = []
        self.closed_clients = 0
        self.create_error: Exception | None = None
        self.get_error: Exception | None = None
        self.delete_error: Exception | None = None
        self.close_error: Exception | None = None
        self.create_gate: asyncio.Event | None = None
        self.create_started = asyncio.Event()

    def client(self) -> FakeClient:
        return FakeClient(self)

    def sandbox(self, sandbox_id: str = 'sb-existing') -> FakeSandbox:
        sandbox = FakeSandbox(sandbox_id)
        self.sandboxes.append(sandbox)
        return sandbox
