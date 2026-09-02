"""A Daytona sandbox behind Pydantic AI's sandbox protocols.

External assumptions last verified 2026-09-01 against Daytona Python SDK 0.198.0:

* `AsyncDaytona.create`, `get`, `delete(wait=True)`, and `close` own sandbox lifecycle;
  `get` accepts a sandbox ID or name:
  https://www.daytona.io/docs/en/python-sdk/async/async-daytona/
* process sessions provide asynchronous execution, separate stdout and stderr callbacks,
  exit status, and deletion as the per-command kill mechanism:
  https://www.daytona.io/docs/en/python-sdk/async/async-process/
* `sandbox.fs` provides metadata, byte upload/download, and directory operations, while
  `sandbox.get_work_dir()` reports the configured working directory:
  https://www.daytona.io/docs/en/python-sdk/async/async-file-system/
* `auto_stop_interval` together with `auto_delete_interval=0` provides the server-side
  backstop for abandoned owned sandboxes:
  https://www.daytona.io/docs/en/python-sdk/async/async-daytona/

Re-check those sources and the installed 0.198.0 signatures before changing lifecycle,
command, or filesystem handling.
"""

from __future__ import annotations

import asyncio
import functools
import math
import posixpath
import shlex
import time
import uuid
from collections.abc import AsyncGenerator, Mapping, Sequence
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import anyio
from pydantic_ai.sandboxes import (
    CommandResult,
    FileEntry,
    SandboxBackend,
    SandboxTimeoutError,
    SandboxUnavailableError,
)
from typing_extensions import Self

from pydantic_ai_harness._sandbox_provider import absolute_path, cleanup_call, raise_after_cleanup

if TYPE_CHECKING:
    from daytona import AsyncDaytona, AsyncSandbox

    # Not re-exported at the package root; typing-only, so the private path never runs.
    from daytona._async.process import AsyncProcess
    from pydantic_ai.sandboxes import (
        SandboxCommand,
        SandboxFilesystem,
        SandboxProcess,
        SupportsFilesystem,
        SupportsStart,
    )

__all__ = (
    'DaytonaSandboxAuthError',
    'DaytonaSandboxBackend',
    'DaytonaSandboxError',
    'DaytonaSandboxUnavailableError',
)

DEFAULT_AUTO_STOP_MINUTES = 60

_MISSING_DAYTONA = (
    'The `daytona` package is required for DaytonaSandbox. Install it with `uv add "pydantic-ai-harness[daytona]"`.'
)
_AUTH_MESSAGE = 'Daytona rejected the credentials. Set DAYTONA_API_KEY and try again.'
# Bound sandbox acquisition so a wedged control plane cannot hang creation or connection.
_CREATE_TIMEOUT = 120
# Bound routine SDK requests so a stalled control plane cannot hang an operation.
_REQUEST_TIMEOUT = 30
# Bound provider lifecycle RPCs such as create, start, and delete.
_LIFECYCLE_TIMEOUT = 60.0
# Bound cleanup RPCs so teardown cannot wedge the caller.
_TEARDOWN_TIMEOUT = 30.0


class DaytonaSandboxError(RuntimeError):
    """A recoverable Daytona provider operation failed."""


class DaytonaSandboxUnavailableError(DaytonaSandboxError, SandboxUnavailableError):
    """The referenced Daytona sandbox is no longer available."""


class DaytonaSandboxAuthError(DaytonaSandboxError, SandboxUnavailableError):
    """Daytona rejected the configured credentials."""


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
    """Apply command-local settings that Daytona's session request cannot represent."""
    if env:
        assignments = ' '.join(shlex.quote(f'{name}={value}') for name, value in env.items())
        command = f'env -- {assignments} sh -c {shlex.quote(command)}'
    if cwd is not None:
        command = f'cd -- {shlex.quote(cwd)} && {command}'
    return command


class _DaytonaProcess:
    """A command running in a Daytona process session."""

    def __init__(
        self,
        process: AsyncProcess,
        *,
        backend: DaytonaSandboxBackend,
        session_id: str,
        command_id: str,
        stdout: list[str],
        stderr: list[str],
        logs: asyncio.Task[None],
        deadline: float | None,
        started: float,
    ) -> None:
        self._process = process
        self._backend = backend
        self._session_id = session_id
        self._command_id = command_id
        self._stdout = stdout
        self._stderr = stderr
        self._logs = logs
        self._deadline = deadline
        self._started = started
        self._lock = anyio.Lock()
        self._outcome: CommandResult | Exception | None = None

    @property
    def pid(self) -> int | None:
        return None

    async def wait(self) -> CommandResult:
        """Wait for the command and return the same outcome on every call."""
        # `_settle` is effectful (its timeout path kills the session and cancels the shared
        # logs task), so the protocol's promise that repeated and concurrent waits agree is
        # kept by settling once under the lock and handing every later caller the cached
        # outcome.
        async with self._lock:
            if self._outcome is None:
                try:
                    self._outcome = await self._settle()
                except Exception as error:
                    self._outcome = error
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome

    async def _settle(self) -> CommandResult:
        completed = False
        try:
            # `fail_after` would make its timeout indistinguishable from an SDK `TimeoutError` translated below.
            with anyio.move_on_after(self._remaining()):
                await self._logs
                completed = True
        except Exception as error:
            raise self._backend.operation_error(error, 'Could not read the command output', unavailable=True) from error
        if not completed:
            raise await self._timeout_error()
        # The exit-status RPC shares the command's deadline: a wedged status lookup after the
        # logs completed must not extend the promised bound.
        command = None
        try:
            with anyio.move_on_after(self._remaining()):
                command = await self._process.get_session_command(
                    self._session_id, self._command_id, request_timeout=_REQUEST_TIMEOUT
                )
        except Exception as error:
            raise self._backend.operation_error(error, 'Could not read the command result', unavailable=True) from error
        if command is None:
            raise await self._timeout_error()
        if command.exit_code is None:
            raise DaytonaSandboxError('Daytona closed the command output before reporting an exit status.')
        result = CommandResult(
            exit_code=command.exit_code,
            stdout=''.join(self._stdout),
            stderr=''.join(self._stderr),
        )
        await _kill_quietly(self)
        return result

    def _remaining(self) -> float | None:
        return None if self._deadline is None else self._deadline - (time.monotonic() - self._started)

    async def _timeout_error(self) -> SandboxTimeoutError:
        await _kill_quietly(self)
        assert self._deadline is not None
        return SandboxTimeoutError(
            f'Command timed out after {self._deadline:g} seconds and was killed.',
            stdout=''.join(self._stdout),
            stderr=''.join(self._stderr),
            timeout=self._deadline,
        )

    async def kill(self) -> None:
        """Delete the Daytona process session, which kills its command."""
        from daytona import DaytonaNotFoundError

        error = await cleanup_call(
            functools.partial(self._process.delete_session, self._session_id, request_timeout=_REQUEST_TIMEOUT),
            timeout=_TEARDOWN_TIMEOUT,
        )
        if error is None or isinstance(error, DaytonaNotFoundError):
            if not self._logs.done():
                self._logs.cancel()
            return
        await raise_after_cleanup(
            self._backend.operation_error(
                error, f'Could not kill command session {self._session_id!r}', unavailable=True
            )
        )


async def _kill_quietly(process: _DaytonaProcess) -> None:
    """Best-effort kill whose failure must not mask the outcome being raised."""
    try:
        await process.kill()
    except Exception:
        pass


class _DaytonaFilesystem:
    """Daytona's filesystem API behind the sandbox filesystem protocol."""

    def __init__(self, backend: DaytonaSandboxBackend) -> None:
        self._backend = backend

    @asynccontextmanager
    async def _translated(self, path: str) -> AsyncGenerator[None]:
        from daytona import DaytonaNotFoundError

        try:
            yield
        except DaytonaNotFoundError as error:
            raise FileNotFoundError(f'No such file or directory in the Daytona sandbox: {path!r}') from error
        except Exception as error:
            raise self._backend.operation_error(error, f'Could not access {path!r} in the sandbox') from error

    async def read_bytes(self, path: str) -> bytes:
        async with self._translated(path):
            return await self._backend.sandbox.fs.download_file(path, _REQUEST_TIMEOUT)

    async def write_bytes(self, path: str, data: bytes) -> None:
        parent = posixpath.dirname(path)
        async with self._translated(path):
            if parent not in ('', '.', '/'):
                mkdir = await self._backend.sandbox.process.exec(
                    f'mkdir -p -- {shlex.quote(parent)}', timeout=_REQUEST_TIMEOUT
                )
                if mkdir.exit_code != 0:
                    raise DaytonaSandboxError(mkdir.result or f'Could not create {parent!r}.')
            await self._backend.sandbox.fs.upload_file(data, path, timeout=_REQUEST_TIMEOUT)

    async def stat(self, path: str) -> FileEntry:
        async with self._translated(path):
            entry = await self._backend.sandbox.fs.get_file_info(path, request_timeout=_REQUEST_TIMEOUT)
        return FileEntry(
            name=posixpath.basename(path.rstrip('/')),
            path=path,
            is_dir=entry.is_dir,
            size=None if entry.is_dir else entry.size,
        )

    async def list_dir(self, path: str) -> Sequence[FileEntry]:
        async with self._translated(path):
            entries = await self._backend.sandbox.fs.list_files(path, request_timeout=_REQUEST_TIMEOUT)
        return [
            FileEntry(
                name=entry.name,
                path=posixpath.join(path, entry.name),
                is_dir=entry.is_dir,
                size=None if entry.is_dir else entry.size,
            )
            for entry in entries
        ]

    async def make_dir(self, path: str) -> None:
        async with self._translated(path):
            await self._backend.sandbox.fs.create_folder(path, '755', request_timeout=_REQUEST_TIMEOUT)

    async def remove(self, path: str) -> None:
        async with self._translated(path):
            await self._backend.sandbox.fs.delete_file(path, recursive=True, request_timeout=_REQUEST_TIMEOUT)

    async def exists(self, path: str) -> bool:
        from daytona import DaytonaNotFoundError

        try:
            await self._backend.sandbox.fs.get_file_info(path, request_timeout=_REQUEST_TIMEOUT)
        except DaytonaNotFoundError:
            return False
        except Exception as error:
            raise self._backend.operation_error(error, f'Could not access {path!r} in the sandbox') from error
        return True


class DaytonaSandboxBackend(SandboxBackend):
    """A Daytona sandbox behind Pydantic AI's `SandboxBackend` protocol.

    The backend owns its `AsyncDaytona` client and implements filesystem and background
    process support. Daytona delivers output through callbacks, so complete command results
    are buffered while a command runs; live `SupportsStream` output is not exposed.

    The protocol is structural, but subclassing it here makes a signature drift fail the type
    check on this class instead of at a distant `Sandbox.wrap` call.
    """

    sandbox: AsyncSandbox
    """The underlying Daytona sandbox, for provider-specific functionality."""

    def __init__(
        self,
        client: AsyncDaytona,
        sandbox: AsyncSandbox,
        *,
        owned: bool,
        working_dir: str | None = None,
    ) -> None:
        self._client = client
        self.sandbox = sandbox
        self._owned = owned
        self._working_dir = absolute_path('working_dir', working_dir)
        self._closed = False
        self.fs = _DaytonaFilesystem(self)

    @property
    def sandbox_id(self) -> str:
        return self.sandbox.id

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
        """Create an owned sandbox with Daytona's automatic stop and delete backstop."""
        try:
            from daytona import AsyncDaytona, CreateSandboxFromSnapshotParams
        except ImportError as error:
            raise DaytonaSandboxError(_MISSING_DAYTONA) from error
        working_dir = absolute_path('working_dir', working_dir)
        client = AsyncDaytona()
        try:
            # Cancellation can orphan a sandbox until the paired auto-stop and immediate
            # auto-delete settings reap it. A stable name lets a retry reconnect meanwhile.
            with anyio.fail_after(_CREATE_TIMEOUT):
                sandbox = await client.create(
                    CreateSandboxFromSnapshotParams(
                        name=name,
                        snapshot=snapshot,
                        env_vars=dict(env) if env is not None else None,
                        auto_stop_interval=auto_stop_minutes,
                        auto_delete_interval=0,
                        network_block_all=network_block_all,
                    ),
                    timeout=_LIFECYCLE_TIMEOUT,
                )
        except BaseException as error:
            await cleanup_call(client.close, timeout=_TEARDOWN_TIMEOUT)
            if isinstance(error, TimeoutError):
                raise DaytonaSandboxError(
                    f'Daytona sandbox creation did not complete within {_CREATE_TIMEOUT}s.'
                ) from error
            if isinstance(error, Exception):
                raise cls.operation_error(error, 'Could not create Daytona sandbox') from error
            raise  # pragma: no cover - cancellation propagates after bounded client cleanup
        return cls(client, sandbox, owned=True, working_dir=working_dir)

    @classmethod
    async def connect(cls, sandbox_id_or_name: str, *, working_dir: str | None = None) -> Self:
        """Connect by Daytona sandbox ID or name; references produced by Harness use IDs."""
        try:
            from daytona import AsyncDaytona
        except ImportError as error:
            raise DaytonaSandboxError(_MISSING_DAYTONA) from error
        working_dir = absolute_path('working_dir', working_dir)
        client = AsyncDaytona()
        try:
            with anyio.fail_after(_CREATE_TIMEOUT):
                sandbox = await client.get(sandbox_id_or_name, request_timeout=_REQUEST_TIMEOUT)
                await sandbox.start(timeout=_LIFECYCLE_TIMEOUT)
        except BaseException as error:
            await cleanup_call(client.close, timeout=_TEARDOWN_TIMEOUT)
            if isinstance(error, TimeoutError):
                raise DaytonaSandboxError(
                    f'Daytona sandbox connection did not complete within {_CREATE_TIMEOUT}s.'
                ) from error
            if isinstance(error, Exception):
                raise cls.operation_error(
                    error, f'Could not connect to Daytona sandbox {sandbox_id_or_name!r}', unavailable=True
                ) from error
            raise  # pragma: no cover - cancellation propagates after bounded client cleanup
        return cls(client, sandbox, owned=False, working_dir=working_dir)

    @classmethod
    async def create_or_connect(
        cls,
        *,
        name: str,
        snapshot: str | None = None,
        auto_stop_minutes: int = DEFAULT_AUTO_STOP_MINUTES,
        working_dir: str | None = None,
        env: Mapping[str, str] | None = None,
        network_block_all: bool = False,
    ) -> Self:
        """Connect by stable name, create if absent, then reconnect after a lost race."""
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

    async def close(self, *, terminate: bool) -> None:
        """Close the SDK client, deleting the sandbox first when this backend owns it."""
        if self._closed:
            return
        deletion_error: Exception | None = None
        if terminate and self._owned:
            deletion_error = await cleanup_call(
                functools.partial(self._client.delete, self.sandbox, timeout=_LIFECYCLE_TIMEOUT, wait=True),
                timeout=_TEARDOWN_TIMEOUT,
            )
            if self._is_not_found(deletion_error):
                deletion_error = None
        close_error = await cleanup_call(self._client.close, timeout=_TEARDOWN_TIMEOUT)
        self._closed = close_error is None
        error = deletion_error or close_error
        if error is not None:
            await raise_after_cleanup(
                self.operation_error(error, f'Could not close Daytona sandbox {self.sandbox_id!r}')
            )

    @staticmethod
    async def delete_by_id(sandbox_id: str) -> None:
        """Delete a sandbox by ID without starting it."""
        try:
            from daytona import AsyncDaytona
        except ImportError as error:
            raise DaytonaSandboxError(_MISSING_DAYTONA) from error
        client = AsyncDaytona()
        operation_error: Exception | None = None
        try:
            with anyio.fail_after(_REQUEST_TIMEOUT):
                sandbox = await client.get(sandbox_id, request_timeout=_REQUEST_TIMEOUT)
            operation_error = await cleanup_call(
                functools.partial(client.delete, sandbox, timeout=_LIFECYCLE_TIMEOUT, wait=True),
                timeout=_TEARDOWN_TIMEOUT,
            )
        except Exception as error:
            operation_error = error
        if DaytonaSandboxBackend._is_not_found(operation_error):
            operation_error = None
        close_error = await cleanup_call(client.close, timeout=_TEARDOWN_TIMEOUT)
        error = operation_error or close_error
        if error is not None:
            await raise_after_cleanup(
                DaytonaSandboxBackend.operation_error(error, f'Could not delete Daytona sandbox {sandbox_id!r}')
            )

    async def working_dir(self) -> str:
        """Return the sandbox's native absolute working directory."""
        # The probe is an idempotent read: overlapping first calls may each ask, get the
        # same answer, and the cache converges. No lock needed.
        if self._working_dir is None:
            try:
                discovered = await self.sandbox.get_work_dir()
            except Exception as error:
                raise self.operation_error(error, 'Could not determine the working directory') from error
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
    ) -> CommandResult:
        process = await self.start(command, shell=shell, cwd=cwd, env=env, timeout=timeout)
        try:
            return await process.wait()
        except SandboxTimeoutError:
            # The timeout path already killed the session, so the generic handler must not kill it twice.
            raise
        except BaseException:
            await _kill_quietly(process)
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
        from daytona import SessionExecuteRequest

        line = _command_context(_command_line(command, shell), absolute_path('cwd', cwd), env)
        if timeout is not None and (not math.isfinite(timeout) or timeout <= 0):
            raise ValueError(f'timeout must be a positive finite number or None, got {timeout!r}.')
        started = time.monotonic()
        session_id = f'pydantic-ai-{uuid.uuid4().hex}'
        process = self.sandbox.process
        created = False
        try:
            with anyio.fail_after(_REQUEST_TIMEOUT):
                await process.create_session(session_id, request_timeout=_REQUEST_TIMEOUT)
                created = True
                response = await process.execute_session_command(
                    session_id,
                    SessionExecuteRequest(command=line, run_async=True),
                    timeout=_REQUEST_TIMEOUT,
                )
        except BaseException as error:
            if created:
                await cleanup_call(
                    functools.partial(process.delete_session, session_id, request_timeout=_REQUEST_TIMEOUT),
                    timeout=_TEARDOWN_TIMEOUT,
                )
            if isinstance(error, TimeoutError):
                raise DaytonaSandboxError('Daytona command session setup timed out.') from error
            if isinstance(error, Exception):
                raise self.operation_error(error, 'Could not start command', unavailable=True) from error
            raise  # pragma: no cover - cancellation propagates after bounded session cleanup
        stdout: list[str] = []
        stderr: list[str] = []
        logs = asyncio.create_task(
            process.get_session_command_logs_async(session_id, response.cmd_id, stdout.append, stderr.append)
        )
        return _DaytonaProcess(
            process,
            backend=self,
            session_id=session_id,
            command_id=response.cmd_id,
            stdout=stdout,
            stderr=stderr,
            logs=logs,
            deadline=timeout,
            started=started,
        )

    @staticmethod
    def operation_error(error: Exception, context: str, *, unavailable: bool = False) -> DaytonaSandboxError:
        # Daytona's NotFound is ambiguous: the call site knows whether it asked about a file or a sandbox.
        try:
            from daytona import DaytonaAuthenticationError, DaytonaAuthorizationError, DaytonaNotFoundError
        except ImportError:  # pragma: no cover - an active backend already imported Daytona
            return DaytonaSandboxError(f'{context}: {type(error).__name__}: {error}')
        if isinstance(error, (DaytonaAuthenticationError, DaytonaAuthorizationError)):
            return DaytonaSandboxAuthError(_AUTH_MESSAGE)
        if unavailable and isinstance(error, DaytonaNotFoundError):
            return DaytonaSandboxUnavailableError(f'{context}: the sandbox does not exist or is no longer available.')
        return DaytonaSandboxError(f'{context}: {type(error).__name__}: {error}')

    @staticmethod
    def _is_not_found(error: Exception | None) -> bool:
        if error is None:
            return False
        try:
            from daytona import DaytonaNotFoundError
        except ImportError:  # pragma: no cover - an active backend already imported Daytona
            return False
        return isinstance(error, DaytonaNotFoundError)


if TYPE_CHECKING:
    _client = AsyncDaytona.__new__(AsyncDaytona)
    _sandbox = AsyncSandbox.__new__(AsyncSandbox)
    _backend = DaytonaSandboxBackend(_client, _sandbox, owned=False)
    _process = _DaytonaProcess.__new__(_DaytonaProcess)
    _backend_conforms: SandboxBackend = _backend
    _filesystem_backend_conforms: SupportsFilesystem = _backend
    _start_conforms: SupportsStart = _backend
    _filesystem_conforms: SandboxFilesystem = _backend.fs
    _process_conforms: SandboxProcess = _process
