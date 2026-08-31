"""Small Daytona SDK boundary used by `DaytonaSandbox`.

External assumptions, verified 2026-08-23 against Daytona Python SDK 0.198.0:

- `AsyncDaytona.create`, `get`, `delete(wait=True)`, and `close` own sandbox lifecycle.
- process sessions provide bounded waits, input without echo, streamed stdout
  and stderr, exit status, and explicit deletion.
- `sandbox.fs` provides metadata, byte upload/download, and directory listing.
- `CreateSandboxFromSnapshotParams.network_block_all=True` blocks outbound traffic.

Sources:
https://www.daytona.io/docs/en/python-sdk/async/async-daytona/
https://www.daytona.io/docs/en/python-sdk/async/async-process/
https://www.daytona.io/docs/en/python-sdk/async/async-file-system/
https://www.daytona.io/docs/en/network-limits/

Re-check those signatures against the lowest supported SDK before raising the
dependency ceiling.
"""

from __future__ import annotations

import asyncio
import posixpath
import shlex
from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING, TypeAlias, TypeVar

if TYPE_CHECKING:
    from daytona import AsyncDaytona
    from daytona._async.process import AsyncProcess
    from daytona._async.sandbox import AsyncSandbox


DEFAULT_AUTO_STOP_MINUTES = 60
_DEFAULT_PROCESS_IO_TIMEOUT = 30
_DEFAULT_REQUEST_TIMEOUT = 30.0
_DEFAULT_LIFECYCLE_TIMEOUT = 60.0

ProcessOutputHandler: TypeAlias = Callable[[str], None] | Callable[[str], Awaitable[None]]
_T = TypeVar('_T')


class DaytonaSandboxError(RuntimeError):
    """A Daytona sandbox operation failed."""


class DaytonaSandboxTerminalError(DaytonaSandboxError):
    """A sandbox failure that retrying the same operation cannot fix."""


class DaytonaSandboxCommandTimeoutError(TimeoutError):
    """A Daytona command or its process setup exceeded its deadline."""


class DaytonaSandboxAuthError(DaytonaSandboxTerminalError):
    """Daytona rejected the configured credentials."""


class DaytonaSandboxUnavailableError(DaytonaSandboxTerminalError):
    """The requested Daytona sandbox no longer exists."""


class DaytonaSandboxProcess:
    """One bounded, bidirectional command in an open Daytona sandbox session."""

    def __init__(
        self,
        *,
        process: AsyncProcess,
        process_id: str,
        command: str,
        on_stdout: ProcessOutputHandler,
        on_stderr: ProcessOutputHandler,
        max_input_bytes: int,
        io_timeout: int,
        on_created: Callable[[str], None],
        on_closed: Callable[[str], None],
    ) -> None:
        if not process_id:
            raise ValueError('process_id must not be empty.')
        _positive_int('max_input_bytes', max_input_bytes)
        _positive_int('io_timeout', io_timeout)
        self._process = process
        self._process_id = process_id
        self._command = command
        self._on_stdout = on_stdout
        self._on_stderr = on_stderr
        self._max_input_bytes = max_input_bytes
        self._io_timeout = io_timeout
        self._on_created = on_created
        self._on_closed = on_closed
        self._created = False
        self._command_id: str | None = None
        self._logs: asyncio.Task[None] | None = None

    @property
    def process_id(self) -> str:
        """The caller-supplied identity used for the Daytona process session."""
        return self._process_id

    async def __aenter__(self) -> DaytonaSandboxProcess:
        if self._created:
            raise DaytonaSandboxError('The Daytona process is already open.')

        async def delete_cancelled_session(_: object) -> None:
            try:
                await self._process.delete_session(self._process_id, request_timeout=self._io_timeout)
            except Exception:
                self._created = True
                self._on_created(self._process_id)
                raise

        try:
            await _finish_on_cancellation(
                self._process.create_session(self._process_id, request_timeout=self._io_timeout),
                on_cancel=delete_cancelled_session,
            )
            self._created = True
            self._on_created(self._process_id)
            from daytona import SessionExecuteRequest

            response = await self._process.execute_session_command(
                self._process_id,
                SessionExecuteRequest(
                    command=self._command,
                    run_async=True,
                    suppress_input_echo=True,
                ),
                timeout=self._io_timeout,
            )
        except BaseException as error:
            if self._created:
                try:
                    await _finish_cleanup(
                        self._process.delete_session(self._process_id, request_timeout=self._io_timeout),
                        then=self._clear,
                    )
                except Exception:
                    pass
            if isinstance(error, (TimeoutError, asyncio.TimeoutError)):
                raise DaytonaSandboxCommandTimeoutError('Daytona process setup timed out.') from error
            if isinstance(error, Exception):
                raise _translate_error(error, unavailable=True) from error
            raise
        self._command_id = response.cmd_id
        self._logs = asyncio.create_task(
            self._process.get_session_command_logs_async(
                self._process_id,
                response.cmd_id,
                self._on_stdout,
                self._on_stderr,
            )
        )
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def send(self, data: str, *, timeout: int = _DEFAULT_PROCESS_IO_TIMEOUT) -> None:
        """Send bounded text to the command's standard input."""
        _positive_int('timeout', timeout)
        size = len(data.encode('utf-8'))
        if size > self._max_input_bytes:
            raise ValueError(f'input is {size} bytes, over the {self._max_input_bytes}-byte limit.')
        command_id = self._require_command_id()
        try:
            await self._process.send_session_command_input(
                self._process_id,
                command_id,
                data,
                request_timeout=timeout,
            )
        except Exception as error:
            raise _translate_error(error, unavailable=True) from error

    async def wait(self, *, timeout: float | None) -> int:
        """Wait a bounded time for completion and return the command exit status."""
        if timeout is not None and timeout <= 0:
            raise ValueError(f'timeout must be positive or None, got {timeout!r}.')
        command_id = self._require_command_id()
        logs = self._logs
        if logs is None:  # pragma: no cover - maintained with `_command_id`
            raise DaytonaSandboxError('The Daytona process log stream is unavailable.')
        try:
            await asyncio.wait_for(asyncio.shield(logs), timeout)
            command = await self._process.get_session_command(
                self._process_id,
                command_id,
                request_timeout=self._io_timeout,
            )
        except asyncio.TimeoutError as error:
            # Before Python 3.11, asyncio's timeout exception is not the built-in
            # TimeoutError expected by the sandbox protocol.
            raise TimeoutError from error
        except Exception as error:
            raise _translate_error(error, unavailable=True) from error
        if command.exit_code is None:
            raise DaytonaSandboxError('Daytona closed the process log stream before reporting an exit status.')
        return command.exit_code

    async def close(self) -> None:
        """Terminate the remote process session and its local log stream."""
        if not self._created:
            return
        try:
            await _finish_cleanup(
                self._process.delete_session(self._process_id, request_timeout=self._io_timeout),
                then=self._clear,
            )
        except Exception as error:
            try:
                from daytona import DaytonaNotFoundError
            except ImportError:  # pragma: no cover - an open process already imported Daytona
                raise _translate_error(error, unavailable=True) from error
            if isinstance(error, DaytonaNotFoundError):
                self._clear()
                return
            raise _translate_error(error, unavailable=True) from error

    def _clear(self) -> None:
        logs = self._logs
        if logs is not None and not logs.done():
            logs.cancel()
        self._created = False
        self._command_id = None
        self._logs = None
        self._on_closed(self._process_id)

    def _require_command_id(self) -> str:
        if self._command_id is None:
            raise DaytonaSandboxError('The Daytona process is not open.')
        return self._command_id


class DaytonaSandboxSession:
    """Internal SDK client and sandbox lifetime boundary used by the backend."""

    def __init__(
        self,
        *,
        sandbox_id: str | None = None,
        sandbox_name: str | None = None,
        snapshot: str | None = None,
        auto_stop_minutes: int = DEFAULT_AUTO_STOP_MINUTES,
        workdir: str | None = None,
        env: Mapping[str, str] | None = None,
        network_block_all: bool = False,
    ) -> None:
        if type(auto_stop_minutes) is not int or auto_stop_minutes <= 0:
            raise ValueError(f'auto_stop_minutes must be a positive integer, got {auto_stop_minutes!r}.')
        if sandbox_id is not None and snapshot is not None:
            raise ValueError('snapshot cannot be combined with sandbox_id.')
        if sandbox_id is not None and sandbox_name is not None:
            raise ValueError('sandbox_name cannot be combined with sandbox_id.')
        if sandbox_id is not None and auto_stop_minutes != DEFAULT_AUTO_STOP_MINUTES:
            raise ValueError('auto_stop_minutes cannot be combined with sandbox_id.')
        if type(network_block_all) is not bool:
            raise ValueError(f'network_block_all must be a boolean, got {network_block_all!r}.')
        if sandbox_id is not None and network_block_all:
            raise ValueError('network_block_all cannot configure an attached sandbox.')
        if workdir is not None:
            if not posixpath.isabs(workdir):
                raise ValueError(f'workdir must be an absolute sandbox path or None, got {workdir!r}.')
            workdir = posixpath.normpath(workdir)
        self._requested_id = sandbox_id
        self._sandbox_name = sandbox_name
        self._snapshot = snapshot
        self._auto_stop_minutes = auto_stop_minutes
        self._workdir = workdir
        self._env = dict(env) if env is not None else None
        self._network_block_all = network_block_all
        self._client: AsyncDaytona | None = None
        self._sandbox: AsyncSandbox | None = None
        self._owned_sandbox_deleted = False
        self._process_ids: set[str] = set()

    @property
    def sandbox_id(self) -> str | None:
        """The ID of the open sandbox, or `None` outside the session context."""
        return self._sandbox.id if self._sandbox is not None else None

    async def __aenter__(self) -> DaytonaSandboxSession:
        if self._sandbox is not None:
            raise DaytonaSandboxError(
                'The session is already open; exit it before entering again. '
                'Use a separate session per concurrent context.'
            )
        try:
            from daytona import AsyncDaytona, CreateSandboxFromSnapshotParams
        except ImportError as error:
            raise DaytonaSandboxError(
                'The `daytona` package is required. Install it with `uv add "pydantic-ai-harness[daytona]"`.'
            ) from error

        client = AsyncDaytona()
        self._client = client

        async def delete_cancelled_sandbox(created: AsyncSandbox) -> None:
            try:
                await client.delete(created, timeout=60, wait=True)
            except Exception:
                # Retain the remote identity so cancellation cleanup can be retried explicitly.
                self._sandbox = created
                raise

        try:
            if self._requested_id is not None:
                sandbox = await _finish_on_cancellation(
                    client.get(self._requested_id, request_timeout=_DEFAULT_REQUEST_TIMEOUT)
                )
                await _finish_on_cancellation(sandbox.start(timeout=_DEFAULT_LIFECYCLE_TIMEOUT))
            else:
                params = CreateSandboxFromSnapshotParams(
                    name=self._sandbox_name,
                    snapshot=self._snapshot,
                    env_vars=self._env,
                    auto_stop_interval=self._auto_stop_minutes,
                    auto_delete_interval=0,
                    network_block_all=self._network_block_all,
                )
                sandbox = await _finish_on_cancellation(
                    client.create(params, timeout=_DEFAULT_LIFECYCLE_TIMEOUT),
                    on_cancel=delete_cancelled_sandbox,
                )
        except asyncio.CancelledError:
            if self._sandbox is None:
                try:
                    await _finish_cleanup(asyncio.wait_for(client.close(), _DEFAULT_REQUEST_TIMEOUT), then=self._clear)
                except Exception:
                    pass
            raise
        except (TimeoutError, asyncio.TimeoutError) as error:
            try:
                await _finish_cleanup(asyncio.wait_for(client.close(), _DEFAULT_REQUEST_TIMEOUT))
            except Exception:
                pass
            else:
                self._client = None
            raise DaytonaSandboxCommandTimeoutError('Daytona sandbox setup timed out.') from error
        except Exception as error:
            try:
                await _finish_cleanup(asyncio.wait_for(client.close(), _DEFAULT_REQUEST_TIMEOUT))
            except Exception:
                pass
            else:
                self._client = None
            raise _translate_error(error, unavailable=self._requested_id is not None) from error

        self._sandbox = sandbox
        self._owned_sandbox_deleted = False
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close(terminate=self._requested_id is None)

    async def close(self, *, terminate: bool) -> None:
        """Close the SDK client, optionally deleting the sandbox first."""
        client = self._client
        sandbox = self._sandbox
        if client is None:
            return
        if sandbox is None:
            try:
                await _finish_cleanup(asyncio.wait_for(client.close(), _DEFAULT_REQUEST_TIMEOUT), then=self._clear)
            except Exception as error:
                raise _translate_error(error, unavailable=False) from error
            return

        if terminate and not self._owned_sandbox_deleted:
            try:
                await _finish_cleanup(
                    client.delete(sandbox, timeout=_DEFAULT_LIFECYCLE_TIMEOUT, wait=True),
                    then=self._mark_owned_sandbox_deleted,
                )
            except Exception as error:
                from daytona import DaytonaNotFoundError

                if isinstance(error, DaytonaNotFoundError):
                    self._mark_owned_sandbox_deleted()
                else:
                    # Keep the live client and sandbox handle so the caller can retry deletion.
                    raise _translate_error(error, unavailable=False) from error
        if not terminate:
            for process_id in tuple(self._process_ids):
                try:
                    await _finish_cleanup(
                        sandbox.process.delete_session(process_id, request_timeout=_DEFAULT_PROCESS_IO_TIMEOUT),
                        then=lambda process_id=process_id: self._process_ids.discard(process_id),
                    )
                except Exception as error:
                    from daytona import DaytonaNotFoundError

                    if isinstance(error, DaytonaNotFoundError):
                        self._process_ids.discard(process_id)
                    else:
                        raise _translate_error(error, unavailable=True) from error
        try:
            await _finish_cleanup(asyncio.wait_for(client.close(), _DEFAULT_REQUEST_TIMEOUT), then=self._clear)
        except Exception as error:
            raise _translate_error(error, unavailable=False) from error

    def _mark_owned_sandbox_deleted(self) -> None:
        self._owned_sandbox_deleted = True
        self._process_ids.clear()

    def _clear(self) -> None:
        self._client = None
        self._sandbox = None
        self._owned_sandbox_deleted = False
        self._process_ids.clear()

    def _require_sandbox(self) -> AsyncSandbox:
        if self._sandbox is None:
            raise DaytonaSandboxError('The Daytona sandbox session is not open.')
        return self._sandbox

    async def get_work_dir(self) -> str:
        """Return the sandbox image's configured working directory."""
        try:
            return await asyncio.wait_for(self._require_sandbox().get_work_dir(), _DEFAULT_REQUEST_TIMEOUT)
        except Exception as error:
            raise _translate_error(error, unavailable=False) from error

    def _path(self, path: str) -> str:
        if self._workdir is None or posixpath.isabs(path):
            return path
        return posixpath.normpath(posixpath.join(self._workdir, path))

    def _command(self, command: str) -> str:
        if self._env:
            assignments = ' '.join(shlex.quote(f'{name}={value}') for name, value in self._env.items())
            command = f'env -- {assignments} sh -c {shlex.quote(command)}'
        if self._workdir is not None:
            command = f'cd -- {shlex.quote(self._workdir)} && {command}'
        return command

    def process(
        self,
        process_id: str,
        command: str,
        *,
        on_stdout: ProcessOutputHandler,
        on_stderr: ProcessOutputHandler,
        max_input_bytes: int,
        io_timeout: int = _DEFAULT_PROCESS_IO_TIMEOUT,
    ) -> DaytonaSandboxProcess:
        """Prepare one managed long-running command inside this open session."""
        process = self._require_sandbox().process
        return DaytonaSandboxProcess(
            process=process,
            process_id=process_id,
            command=self._command(command),
            on_stdout=on_stdout,
            on_stderr=on_stderr,
            max_input_bytes=max_input_bytes,
            io_timeout=io_timeout,
            on_created=self._process_ids.add,
            on_closed=self._process_ids.discard,
        )

    async def file_info(self, path: str) -> tuple[str, str, bool, int | None]:
        """Return the protocol-facing metadata for one path."""
        resolved = self._path(path)
        try:
            entry = await self._require_sandbox().fs.get_file_info(resolved, request_timeout=_DEFAULT_REQUEST_TIMEOUT)
        except Exception as error:
            raise _translate_file_error(error, path) from error
        return posixpath.basename(resolved.rstrip('/')), resolved, entry.is_dir, None if entry.is_dir else entry.size

    async def read_bytes(self, path: str) -> bytes:
        try:
            data = await self._require_sandbox().fs.download_file(self._path(path), int(_DEFAULT_REQUEST_TIMEOUT))
        except Exception as error:
            raise _translate_file_error(error, path) from error
        return data

    async def write_bytes(self, path: str, data: bytes) -> None:
        sandbox = self._require_sandbox()
        resolved = self._path(path)
        parent = posixpath.dirname(resolved)
        try:
            if parent not in ('', '.', '/'):
                mkdir = await sandbox.process.exec(f'mkdir -p -- {shlex.quote(parent)}', timeout=30)
                if mkdir.exit_code != 0:
                    raise DaytonaSandboxError(mkdir.result or f'Could not create {parent!r}.')
            await sandbox.fs.upload_file(data, resolved, timeout=int(_DEFAULT_REQUEST_TIMEOUT))
        except DaytonaSandboxError:
            raise
        except Exception as error:
            raise _translate_error(error, unavailable=False) from error

    async def list_entries(self, path: str) -> list[tuple[str, str, bool, int | None]]:
        """Return direct children with their protocol-facing metadata."""
        resolved = self._path(path)
        try:
            entries = await self._require_sandbox().fs.list_files(resolved, request_timeout=_DEFAULT_REQUEST_TIMEOUT)
        except Exception as error:
            raise _translate_file_error(error, path) from error
        return [
            (
                entry.name,
                posixpath.join(resolved, entry.name),
                entry.is_dir,
                None if entry.is_dir else entry.size,
            )
            for entry in entries
        ]

    async def make_dir(self, path: str) -> None:
        try:
            await self._require_sandbox().fs.create_folder(
                self._path(path), '755', request_timeout=_DEFAULT_REQUEST_TIMEOUT
            )
        except Exception as error:
            raise _translate_error(error, unavailable=False) from error

    async def remove(self, path: str) -> None:
        try:
            await self._require_sandbox().fs.delete_file(
                self._path(path), recursive=True, request_timeout=_DEFAULT_REQUEST_TIMEOUT
            )
        except Exception as error:
            raise _translate_file_error(error, path) from error

    async def exists(self, path: str) -> bool:
        try:
            await self._require_sandbox().fs.get_file_info(self._path(path), request_timeout=_DEFAULT_REQUEST_TIMEOUT)
        except Exception as error:
            try:
                from daytona import DaytonaNotFoundError
            except ImportError:  # pragma: no cover - an open session already imported Daytona
                raise _translate_error(error, unavailable=False) from error
            if isinstance(error, DaytonaNotFoundError):
                return False
            raise _translate_error(error, unavailable=False) from error
        return True


def _translate_error(error: Exception, *, unavailable: bool) -> DaytonaSandboxError:
    """Map SDK failures without leaking SDK types through the public API."""
    try:
        from daytona import (
            DaytonaAuthenticationError,
            DaytonaAuthorizationError,
            DaytonaNotFoundError,
        )
    except ImportError:  # pragma: no cover - the session already imported Daytona
        return DaytonaSandboxError(str(error))

    if isinstance(error, (DaytonaAuthenticationError, DaytonaAuthorizationError)):
        return DaytonaSandboxAuthError('Daytona rejected the credentials. Set DAYTONA_API_KEY and try again.')
    if unavailable and isinstance(error, DaytonaNotFoundError):
        return DaytonaSandboxUnavailableError('The Daytona sandbox does not exist or is no longer available.')
    return DaytonaSandboxError(str(error))


def _translate_file_error(error: Exception, path: str) -> Exception:
    try:
        from daytona import DaytonaNotFoundError
    except ImportError:  # pragma: no cover - an open session already imported Daytona
        return DaytonaSandboxError(str(error))
    if isinstance(error, DaytonaNotFoundError):
        return FileNotFoundError(f'No such file or directory in the Daytona sandbox: {path!r}')
    return _translate_error(error, unavailable=False)


async def _finish_on_cancellation(
    operation: Awaitable[_T],
    *,
    on_cancel: Callable[[_T], Awaitable[object]] | None = None,
) -> _T:
    task = asyncio.ensure_future(operation)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            result = await task
        except Exception:
            pass
        else:
            if on_cancel is not None:
                try:
                    await _finish_cleanup(on_cancel(result))
                except Exception:
                    pass
        raise


async def _finish_cleanup(operation: Awaitable[object], *, then: Callable[[], None] | None = None) -> None:
    task = asyncio.ensure_future(operation)
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await task
        except Exception:
            pass
        else:
            if then is not None:
                then()
        raise
    if then is not None:
        then()


async def _delete_sandbox(sandbox_id: str) -> None:  # pyright: ignore[reportUnusedFunction]
    """Delete one sandbox by identity without starting it."""
    try:
        from daytona import AsyncDaytona, DaytonaNotFoundError
    except ImportError as error:
        raise DaytonaSandboxError(
            'The `daytona` package is required. Install it with `uv add "pydantic-ai-harness[daytona]"`.'
        ) from error

    client = AsyncDaytona()

    async def delete(sandbox: AsyncSandbox) -> None:
        await client.delete(sandbox, timeout=_DEFAULT_LIFECYCLE_TIMEOUT, wait=True)

    try:
        sandbox = await _finish_on_cancellation(
            client.get(sandbox_id, request_timeout=_DEFAULT_REQUEST_TIMEOUT), on_cancel=delete
        )
        await _finish_on_cancellation(delete(sandbox))
    except asyncio.CancelledError:
        try:
            await _finish_cleanup(asyncio.wait_for(client.close(), _DEFAULT_REQUEST_TIMEOUT))
        except Exception:
            pass
        raise
    except DaytonaNotFoundError:
        pass
    except Exception as error:
        try:
            await _finish_cleanup(asyncio.wait_for(client.close(), _DEFAULT_REQUEST_TIMEOUT))
        except Exception:
            pass
        raise _translate_error(error, unavailable=False) from error

    try:
        await _finish_cleanup(asyncio.wait_for(client.close(), _DEFAULT_REQUEST_TIMEOUT))
    except Exception as error:
        raise _translate_error(error, unavailable=False) from error


def _positive_int(name: str, value: int) -> None:
    if type(value) is not int or value <= 0:
        raise ValueError(f'{name} must be a positive integer, got {value!r}.')
