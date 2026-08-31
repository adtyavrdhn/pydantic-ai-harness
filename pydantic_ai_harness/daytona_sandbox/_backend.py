"""Daytona implementation of Pydantic AI's sandbox protocols."""

from __future__ import annotations

import asyncio
import functools
import math
import posixpath
import shlex
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from typing_extensions import Self

from pydantic_ai_harness.daytona_sandbox._session import (
    DEFAULT_AUTO_STOP_MINUTES,
    DaytonaSandboxCommandTimeoutError,
    DaytonaSandboxError,
    DaytonaSandboxProcess,
    DaytonaSandboxSession,
    DaytonaSandboxUnavailableError,
    _delete_sandbox,  # pyright: ignore[reportPrivateUsage]
)

if TYPE_CHECKING:
    from pydantic_ai.sandboxes import (
        SandboxBackend,
        SandboxCommand,
        SandboxFilesystem,
        SandboxProcess,
        SupportsFilesystem,
        SupportsStart,
    )

PROVIDER = 'daytona'
_PROCESS_IO_TIMEOUT = 30


def _absolute_path(name: str, value: str | None) -> str | None:
    if value is None:
        return None
    if not posixpath.isabs(value):
        raise ValueError(f'{name} must be an absolute sandbox path or None, got {value!r}.')
    return posixpath.normpath(value)


@dataclass(frozen=True)
class _DaytonaResult:
    exit_code: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class _DaytonaFileEntry:
    name: str
    path: str
    is_dir: bool
    size: int | None


def _command_line(command: SandboxCommand, shell: bool) -> str:
    if shell:
        if not isinstance(command, str):
            raise TypeError('an argv sequence cannot be combined with shell=True; pass a single command string')
        return command
    if isinstance(command, str):
        raise TypeError('a string command requires shell=True; pass an argv sequence otherwise')
    if not command:
        raise TypeError('a command needs at least the program to run; the argv sequence is empty')
    return shlex.join(command)


def _command_context(command: str, cwd: str | None, env: Mapping[str, str] | None) -> str:
    if env:
        assignments = ' '.join(shlex.quote(f'{name}={value}') for name, value in env.items())
        command = f'env -- {assignments} sh -c {shlex.quote(command)}'
    if cwd is not None:
        command = f'cd -- {shlex.quote(cwd)} && {command}'
    return command


class _DaytonaProcess:
    def __init__(
        self,
        process: DaytonaSandboxProcess,
        *,
        stdout: list[str],
        stderr: list[str],
        deadline: float | None,
    ) -> None:
        self._process = process
        self._stdout = stdout
        self._stderr = stderr
        self._deadline = deadline
        self._lock = asyncio.Lock()
        self._outcome: _DaytonaResult | Exception | None = None

    @property
    def pid(self) -> int | None:
        return None

    async def wait(self) -> _DaytonaResult:
        async with self._lock:
            if self._outcome is None:
                try:
                    self._outcome = await self._settle()
                except Exception as error:
                    self._outcome = error
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome

    async def _settle(self) -> _DaytonaResult:
        remaining = None if self._deadline is None else self._deadline - time.monotonic()
        if remaining is not None and remaining <= 0:
            try:
                await self.kill()
            except Exception:
                pass
            raise DaytonaSandboxCommandTimeoutError('Daytona command timed out and cleanup was requested.')
        try:
            exit_code = await self._process.wait(timeout=remaining)
        except TimeoutError as error:
            try:
                await self.kill()
            except Exception:
                pass
            raise DaytonaSandboxCommandTimeoutError('Daytona command timed out and cleanup was requested.') from error
        try:
            await self.kill()
        except Exception:
            # The command completed successfully. Keep the tracked process identity so
            # backend close can retry cleanup without turning success into a rerunnable failure.
            pass
        return _DaytonaResult(exit_code=exit_code, stdout=''.join(self._stdout), stderr=''.join(self._stderr))

    async def kill(self) -> None:
        await self._process.close()


class _DaytonaFilesystem:
    def __init__(self, session: DaytonaSandboxSession) -> None:
        self._session = session

    async def read_bytes(self, path: str) -> bytes:
        return await self._session.read_bytes(path)

    async def write_bytes(self, path: str, data: bytes) -> None:
        await self._session.write_bytes(path, data)

    async def stat(self, path: str) -> _DaytonaFileEntry:
        return _DaytonaFileEntry(*await self._session.file_info(path))

    async def list_dir(self, path: str) -> Sequence[_DaytonaFileEntry]:
        return [_DaytonaFileEntry(*entry) for entry in await self._session.list_entries(path)]

    async def make_dir(self, path: str) -> None:
        await self._session.make_dir(path)

    async def remove(self, path: str) -> None:
        await self._session.remove(path)

    async def exists(self, path: str) -> bool:
        return await self._session.exists(path)


class DaytonaSandboxBackend:
    """A Daytona sandbox behind Pydantic AI's `SandboxBackend` protocol.

    The backend implements filesystem and background-process opt-ins. Daytona
    exposes separate stdout and stderr callbacks, which are collected separately.
    Complete command results are buffered in memory; model-facing tools are
    responsible for their own output budget.
    """

    provider = PROVIDER

    def __init__(self, session: DaytonaSandboxSession, *, working_dir: str | None = None) -> None:
        sandbox_id = session.sandbox_id
        if sandbox_id is None:
            raise DaytonaSandboxError('The Daytona sandbox session is not open.')
        self._session = session
        self._sandbox_id = sandbox_id
        self._working_dir = _absolute_path('working_dir', working_dir)
        self._created_here = False
        self.fs = _DaytonaFilesystem(session)

    @property
    def sandbox_id(self) -> str:
        return self._sandbox_id

    @classmethod
    async def create(
        cls,
        *,
        name: str | None = None,
        snapshot: str | None = None,
        auto_stop_minutes: int = DEFAULT_AUTO_STOP_MINUTES,
        working_dir: str | None = None,
        env: Mapping[str, str] | None = None,
        network_block_all: bool = False,
    ) -> Self:
        session = DaytonaSandboxSession(
            sandbox_name=name,
            snapshot=snapshot,
            auto_stop_minutes=auto_stop_minutes,
            workdir=working_dir,
            env=env,
            network_block_all=network_block_all,
        )
        await session.__aenter__()
        backend = cls(session, working_dir=working_dir)
        backend._created_here = True
        return backend

    @classmethod
    async def _create_or_connect(
        cls,
        *,
        name: str,
        snapshot: str | None = None,
        auto_stop_minutes: int = DEFAULT_AUTO_STOP_MINUTES,
        working_dir: str | None = None,
        env: Mapping[str, str] | None = None,
        network_block_all: bool = False,
    ) -> Self:
        try:
            return await cls.connect(name, working_dir=working_dir)
        except DaytonaSandboxUnavailableError:
            pass
        try:
            return await cls.create(
                name=name,
                snapshot=snapshot,
                auto_stop_minutes=auto_stop_minutes,
                working_dir=working_dir,
                env=env,
                network_block_all=network_block_all,
            )
        except DaytonaSandboxError as create_error:
            try:
                return await cls.connect(name, working_dir=working_dir)
            except DaytonaSandboxUnavailableError:
                raise create_error

    @classmethod
    async def connect(cls, sandbox_id_or_name: str, *, working_dir: str | None = None) -> Self:
        session = DaytonaSandboxSession(sandbox_id=sandbox_id_or_name, workdir=working_dir)
        await session.__aenter__()
        return cls(session, working_dir=working_dir)

    async def close(self, *, terminate: bool) -> None:
        await self._session.close(terminate=terminate)

    async def _cleanup_failed_acquisition(self) -> None:
        if not self._created_here:
            await self.close(terminate=False)
        elif self._session.sandbox_id is not None:
            await self.close(terminate=True)
        else:
            await _delete_sandbox(self.sandbox_id)

    @functools.cached_property
    def _working_dir_lock(self) -> asyncio.Lock:
        return asyncio.Lock()

    async def working_dir(self) -> str:
        if self._working_dir is None:
            async with self._working_dir_lock:
                if self._working_dir is None:
                    discovered = await self._session.get_work_dir()
                    if not posixpath.isabs(discovered):
                        raise DaytonaSandboxError(
                            f'Could not determine the working directory of Daytona sandbox {self.sandbox_id!r}.'
                        )
                    self._working_dir = posixpath.normpath(discovered)
        return self._working_dir

    async def run(
        self,
        command: SandboxCommand,
        *,
        shell: bool = False,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> _DaytonaResult:
        process = await self.start(command, shell=shell, cwd=cwd, env=env, timeout=timeout)
        try:
            return await process.wait()
        except BaseException as error:
            try:
                await process.kill()
            except Exception as cleanup_error:
                if isinstance(error, (asyncio.CancelledError, DaytonaSandboxCommandTimeoutError)):
                    raise error
                raise cleanup_error from error
            raise

    async def start(
        self,
        command: SandboxCommand,
        *,
        shell: bool = False,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> _DaytonaProcess:
        if timeout is not None and (not math.isfinite(timeout) or timeout <= 0):
            raise ValueError(f'timeout must be a positive finite number or None, got {timeout!r}.')
        cwd = _absolute_path('cwd', cwd)
        started = time.monotonic()
        stdout: list[str] = []
        stderr: list[str] = []
        inner = self._session.process(
            f'pydantic-ai-{uuid.uuid4().hex}',
            _command_context(_command_line(command, shell), cwd, env),
            on_stdout=stdout.append,
            on_stderr=stderr.append,
            max_input_bytes=1,
            io_timeout=_PROCESS_IO_TIMEOUT,
        )
        try:
            if timeout is None:
                await inner.__aenter__()
            else:
                await asyncio.wait_for(inner.__aenter__(), timeout)
        except DaytonaSandboxCommandTimeoutError:
            try:
                await inner.close()
            except Exception:
                pass
            raise
        except (TimeoutError, asyncio.TimeoutError) as error:
            try:
                await inner.close()
            except Exception:
                pass
            raise DaytonaSandboxCommandTimeoutError('Daytona command setup timed out.') from error
        except BaseException:
            try:
                await inner.close()
            except Exception:
                pass
            raise
        deadline = None if timeout is None else started + timeout
        return _DaytonaProcess(inner, stdout=stdout, stderr=stderr, deadline=deadline)


if TYPE_CHECKING:
    _backend_conforms: SandboxBackend = DaytonaSandboxBackend.__new__(DaytonaSandboxBackend)
    _filesystem_backend_conforms: SupportsFilesystem = DaytonaSandboxBackend.__new__(DaytonaSandboxBackend)
    _start_conforms: SupportsStart = DaytonaSandboxBackend.__new__(DaytonaSandboxBackend)
    _filesystem_conforms: SandboxFilesystem = _DaytonaFilesystem.__new__(_DaytonaFilesystem)
    _process_conforms: SandboxProcess = _DaytonaProcess.__new__(_DaytonaProcess)
