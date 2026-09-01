"""An E2B sandbox behind Pydantic AI's [`SandboxBackend`][pydantic_ai.sandboxes.SandboxBackend] protocol.

This is the mechanism layer: every E2B-specific operation (create, connect, command execution,
file access, working-directory discovery, teardown) lives here, behind the protocol the rest of
Pydantic AI already speaks. The capability in `_capability.py` owns the lifecycle; tools and
other capabilities consume the resulting `ctx.sandbox`.

External assumptions last verified 2026-08-31 against E2B Python SDK 2.34.0 (the package floor):

* `AsyncSandbox.create` / `connect` / `kill` provide the owned and attached lifecycle, and
  `connect` resumes a paused sandbox and substitutes its 300-second default when `timeout=None`:
  https://github.com/e2b-dev/E2B/blob/main/packages/python-sdk/e2b/sandbox_async/main.py
* `AsyncSandbox.list` accepts `query`, `limit`, and `next_token` but not `order`, and each
  `SandboxInfo` exposes `started_at`:
  https://github.com/e2b-dev/E2B/blob/main/packages/python-sdk/e2b/sandbox_async/main.py
* `commands.run` starts every command as `/bin/bash -l -c <string>`, its `timeout` bounds the
  event stream rather than killing the command, and `commands.kill(pid)` sends SIGKILL:
  https://github.com/e2b-dev/E2B/blob/main/packages/python-sdk/e2b/sandbox_async/commands/command.py
* a command handle accumulates decoded output as it arrives and `wait()` raises
  `CommandExitException` on a non-zero exit:
  https://github.com/e2b-dev/E2B/blob/main/packages/python-sdk/e2b/sandbox_async/commands/command_handle.py
* the filesystem API raises `FileNotFoundException` for a missing path:
  https://github.com/e2b-dev/E2B/blob/main/packages/python-sdk/e2b/sandbox_async/filesystem/filesystem.py

Re-check these sources before changing lifecycle, command, or filesystem assumptions.
"""

from __future__ import annotations

import functools
import math
import posixpath
import shlex
import time
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import anyio
from pydantic_ai.sandboxes import CommandResult, FileEntry, SandboxTimeoutError, SandboxUnavailableError
from typing_extensions import Self

from pydantic_ai_harness._sandbox_provider import absolute_path, cleanup_call, raise_after_cleanup

if TYPE_CHECKING:
    import e2b
    from pydantic_ai.sandboxes import (
        SandboxBackend,
        SandboxCommand,
        SandboxFilesystem,
        SandboxProcess,
        SupportsFilesystem,
        SupportsStart,
    )

__all__ = (
    'E2BSandboxAuthError',
    'E2BSandboxBackend',
    'E2BSandboxError',
    'E2BSandboxUnavailableError',
)

# Defaults shared by `E2BSandboxBackend.create` and the `E2BSandbox` capability (which imports
# them), so the two cannot drift: a setting is "left at its default" iff it equals the constant
# here.
DEFAULT_SANDBOX_TIMEOUT = 300

PROVIDER = 'e2b'
"""The `provider` half of a `SandboxRef` this backend answers to."""

_MISSING_E2B = 'The \'e2b\' package is required for E2BSandbox. Install it with `uv add "pydantic-ai-harness[e2b]"`.'

_AUTH_MESSAGE = 'E2B rejected the credentials. Set a valid E2B_API_KEY in the environment.'

# Bound the sandbox-create call so a wedged control plane cannot hang acquisition.
_CREATE_TIMEOUT = 120

# Teardown runs shielded from cancellation, so an unreachable E2B control plane could otherwise
# hang the caller forever. Bound the kill so a stalled request gives up rather than wedging the
# process; an owned sandbox is still reaped server-side by its own `sandbox_timeout`.
_TEARDOWN_TIMEOUT = 30

# Bounds the internal `pwd` probe behind `working_dir()` and the best-effort kills.
_INTERNAL_EXEC_TIMEOUT = 10

# E2B's own command `timeout` bounds the event stream and leaves the command running, so it is
# switched off (0 is the SDK's "no limit") and the deadline is enforced client-side instead,
# with a kill at expiry. See `_E2BProcess._settle`.
_SDK_STREAM_UNBOUNDED = 0


async def _kill_sandbox(sandbox_id: str, kill: Callable[[], Awaitable[object]]) -> None:
    """Run an E2B kill to completion without letting cleanup replace cancellation."""
    import e2b

    error = await cleanup_call(kill, timeout=_TEARDOWN_TIMEOUT)
    if error is None or isinstance(error, e2b.SandboxNotFoundException):
        return
    if isinstance(error, e2b.AuthenticationException):
        translated = E2BSandboxAuthError(_AUTH_MESSAGE)
    elif isinstance(error, TimeoutError):
        translated = E2BSandboxError(
            f'Timed out after {_TEARDOWN_TIMEOUT}s while trying to kill E2B sandbox {sandbox_id!r}.'
        )
    else:
        translated = E2BSandboxError(f'Could not kill E2B sandbox {sandbox_id!r}: {type(error).__name__}: {error}')
    await raise_after_cleanup(translated)


class E2BSandboxError(RuntimeError):
    """A recoverable E2B provider operation failed."""


class E2BSandboxUnavailableError(E2BSandboxError, SandboxUnavailableError):
    """The sandbox no longer exists: killed, or expired at its `sandbox_timeout`.

    Every later command against it would fail the same way, so it is terminal. For an owned
    sandbox this is what a run outliving the sandbox lifetime looks like; raise
    `sandbox_timeout` (or shorten the work) if runs legitimately need longer.
    """


class E2BSandboxAuthError(E2BSandboxError, SandboxUnavailableError):
    """E2B rejected the credentials, so no sandbox operation can succeed.

    Fixing this is an operator action (configure `E2B_API_KEY`), not something a retry or a
    new run can do, which is why it is terminal.
    """


def _command_line(command: SandboxCommand, shell: bool) -> str:
    """Turn a protocol command into the single string E2B executes.

    E2B has no argv form: `commands.run` hands its string to `/bin/bash -l -c`, so an argv
    sequence is quoted with `shlex.join` first. The shell still parses the result, but the
    quoting makes each element exactly one word, which is the guarantee argv callers rely on.
    """
    if shell:
        if not isinstance(command, str):
            raise TypeError('an argv sequence cannot be combined with shell=True; pass a single command string')
        return command
    if isinstance(command, str):
        raise TypeError('a string command requires shell=True; pass an argv sequence otherwise')
    if not command:
        raise TypeError('a command needs at least the program to run; the argv sequence is empty')
    return shlex.join(command)


class _E2BProcess:
    """A command running inside an E2B sandbox, as returned by `E2BSandboxBackend.start`.

    Deliberately not a `SupportsStream`: E2B delivers live output through `on_stdout` /
    `on_stderr` callbacks that the SDK's own event pump awaits, and it exposes no async
    iterator over them. Bridging the callbacks into a queue would either block that pump when
    nobody consumes the stream (breaking the protocol's promise that skipping `stream()` never
    changes `wait()`) or buffer the whole output, which is not streaming. `wait()` returns the
    complete result instead.
    """

    def __init__(
        self,
        handle: e2b.AsyncCommandHandle,
        *,
        backend: E2BSandboxBackend,
        deadline: float | None,
        started: float,
    ) -> None:
        self._handle = handle
        self._backend = backend
        self._deadline = deadline
        self._started = started
        self._lock = anyio.Lock()
        self._outcome: CommandResult | Exception | None = None

    @property
    def pid(self) -> int | None:
        return self._handle.pid

    async def wait(self) -> CommandResult:
        """Wait for the command and return its result, the same one on every call."""
        # The deadline verdict below can only be reached once, so the first call's verdict is
        # the command's verdict: caching it is what makes repeated and concurrent waits agree.
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
        import e2b

        # Measured from `start()`, as the protocol requires, so a caller that waits late does
        # not get a fresh window.
        remaining = None if self._deadline is None else self._deadline - (time.monotonic() - self._started)
        result: e2b.CommandResult | None = None
        try:
            with anyio.move_on_after(remaining):
                result = await self._handle.wait()
        except e2b.CommandExitException as e:
            # E2B raises on a non-zero exit; the protocol calls that a normal result, so it is
            # unwrapped rather than propagated.
            return CommandResult(exit_code=e.exit_code, stdout=e.stdout, stderr=e.stderr)
        except Exception as e:
            raise await self._backend.operation_error(
                e, 'Could not read the command result (the command may still be running)'
            ) from e
        if result is None:
            # The deadline is ours, so the kill is ours too: E2B's own `timeout` would abandon
            # the stream and leave the command running.
            await _kill_quietly(self)
            assert self._deadline is not None
            raise SandboxTimeoutError(
                f'Command timed out after {self._deadline:g} seconds and was killed.',
                # The handle accumulates decoded output as it arrives, so this is what the
                # command printed before the kill.
                stdout=self._handle.stdout,
                stderr=self._handle.stderr,
                timeout=self._deadline,
            )
        return CommandResult(exit_code=result.exit_code, stdout=result.stdout, stderr=result.stderr)

    async def kill(self) -> None:
        """Send SIGKILL to the command.

        Only the command's own process is signalled. A process it started in the background
        is not reached, and lives on until the sandbox itself is torn down.
        """
        pid = self._handle.pid
        try:
            await self._backend.sandbox.commands.kill(pid)
        except Exception as e:
            raise await self._backend.operation_error(e, f'Could not kill command {pid}') from e


async def _kill_quietly(process: _E2BProcess) -> None:
    """Kill on a path that already has an outcome to report, so a failed kill must not replace it.

    Shielded and bounded: this runs while a deadline or a cancellation is unwinding, where an
    unbounded request would wedge the caller. A failure leaves the sandbox's own lifetime as
    the backstop, and an owned sandbox is killed outright when the run ends.
    """
    with anyio.CancelScope(shield=True):
        with anyio.move_on_after(_INTERNAL_EXEC_TIMEOUT):
            try:
                await process.kill()
            except Exception:
                pass


class _E2BFilesystem:
    """E2B's sandbox filesystem API behind the `SandboxFilesystem` protocol."""

    def __init__(self, backend: E2BSandboxBackend) -> None:
        self._backend = backend

    @asynccontextmanager
    async def _translated(self, path: str) -> AsyncGenerator[None]:
        """Map E2B's filesystem exceptions onto the ones the protocol promises.

        A missing path is the builtin `FileNotFoundError` every backend raises. Everything
        else goes through the shared classification, which can still find a dead sandbox or
        rejected credentials behind it.
        """
        import e2b

        try:
            yield
        except e2b.FileNotFoundException as e:
            raise FileNotFoundError(f'No such file or directory in the E2B sandbox: {path!r}') from e
        except Exception as e:
            raise await self._backend.operation_error(e, f'Could not access {path!r} in the sandbox') from e

    async def read_bytes(self, path: str) -> bytes:
        async with self._translated(path):
            return bytes(await self._backend.sandbox.files.read(path, 'bytes'))

    async def write_bytes(self, path: str, data: bytes) -> None:
        # E2B creates missing parents and replaces existing contents. Its `write` accepts a
        # file-like object through an untyped `IO`, so pyright reports the member as partially
        # unknown; only the `bytes` half is used here.
        async with self._translated(path):
            await self._backend.sandbox.files.write(path, data)  # pyright: ignore[reportUnknownMemberType]

    async def stat(self, path: str) -> FileEntry:
        async with self._translated(path):
            return _file_entry(await self._backend.sandbox.files.get_info(path))

    async def list_dir(self, path: str) -> Sequence[FileEntry]:
        async with self._translated(path):
            # `depth=1` is E2B's non-recursive listing.
            entries = await self._backend.sandbox.files.list(path, depth=1)
        return [_file_entry(entry) for entry in entries]

    async def make_dir(self, path: str) -> None:
        # E2B creates missing parents and reports an existing directory by returning False
        # rather than raising, which is the `mkdir -p` behavior the protocol asks for.
        async with self._translated(path):
            await self._backend.sandbox.files.make_dir(path)

    async def remove(self, path: str) -> None:
        # One call covers both halves of the protocol's `remove`: E2B deletes a file or a
        # directory with its contents.
        async with self._translated(path):
            await self._backend.sandbox.files.remove(path)

    async def exists(self, path: str) -> bool:
        async with self._translated(path):
            return await self._backend.sandbox.files.exists(path)


def _file_entry(entry: e2b.EntryInfo) -> FileEntry:
    import e2b

    is_dir = entry.type is e2b.FileType.DIR
    # A directory's reported size is an implementation detail of the underlying filesystem
    # rather than a content length, so report none for it, like the built-in backends.
    return FileEntry(name=entry.name, path=entry.path, is_dir=is_dir, size=None if is_dir else entry.size)


class E2BSandboxBackend:
    """An [E2B](https://e2b.dev) sandbox as a Pydantic AI [`SandboxBackend`][pydantic_ai.sandboxes.SandboxBackend].

    Commands and file operations run inside an E2B microVM, so the host is never exposed.
    Build one with [`create`][pydantic_ai_harness.e2b_sandbox.E2BSandboxBackend.create] or
    attach to an existing environment with
    [`connect`][pydantic_ai_harness.e2b_sandbox.E2BSandboxBackend.connect]; the `E2BSandbox`
    capability does both for you.

    One process opt-in is implemented: background commands (`SupportsStart`).

    Every command runs through `/bin/bash -l -c`, so an argv sequence is quoted into a single
    shell word string first and login startup files run before the command does. E2B's own
    command `timeout` abandons the output stream and leaves the command running, so the
    deadline is enforced client-side instead and the command is killed with SIGKILL when it
    expires or when the caller is cancelled. That kill signals the command's own process; a
    process the command started in the background outlives it until the sandbox is torn down.

    Deliberately no base class: it conforms to the protocol structurally, like any third-party
    backend would.

    Args:
        sandbox: A live `e2b.AsyncSandbox`. Whoever created it owns killing it.
        working_dir: Directory commands run in and relative paths resolve against. E2B has no
            create-time working directory, so this is applied per command; `None` uses the
            sandbox's own default, discovered with `pwd` on first use.
        sandbox_timeout: The lifetime an owned sandbox was created with, used to explain an
            expired sandbox. `None` for a sandbox this process did not create.
    """

    provider = PROVIDER

    def __init__(
        self,
        sandbox: e2b.AsyncSandbox,
        *,
        working_dir: str | None = None,
        sandbox_timeout: int | None = None,
    ) -> None:
        working_dir = absolute_path('working_dir', working_dir)
        self.sandbox = sandbox
        """The underlying `e2b.AsyncSandbox`, for provider-specific functionality."""
        self.fs = _E2BFilesystem(self)
        self._working_dir = working_dir
        self._sandbox_timeout = sandbox_timeout

    @property
    def sandbox_id(self) -> str:
        return self.sandbox.sandbox_id

    @classmethod
    async def create(
        cls,
        *,
        template: str | None = None,
        sandbox_timeout: int = DEFAULT_SANDBOX_TIMEOUT,
        working_dir: str | None = None,
        env: Mapping[str, str] | None = None,
        metadata: Mapping[str, str] | None = None,
        allow_internet_access: bool = True,
    ) -> Self:
        """Provision a fresh E2B sandbox. The caller owns killing it with `close`.

        Args:
            template: E2B template name or id the sandbox runs; E2B's default when `None`.
            sandbox_timeout: How long E2B keeps the sandbox alive, in seconds.
            working_dir: Directory commands run in; the sandbox's own default when `None`.
            env: Environment variables set for the whole sandbox.
            metadata: E2B metadata recorded on the sandbox.
            allow_internet_access: Whether the sandbox may reach the internet.
        """
        try:
            import e2b
        except ImportError as e:
            raise E2BSandboxError(_MISSING_E2B) from e

        working_dir = absolute_path('working_dir', working_dir)

        try:
            # Cancellation can orphan a sandbox until `sandbox_timeout` reaps it. Metadata
            # search makes a durable retry reconnect to that sandbox instead of creating another.
            with anyio.fail_after(_CREATE_TIMEOUT):
                sandbox = await e2b.AsyncSandbox.create(
                    template=template,
                    timeout=sandbox_timeout,
                    metadata=dict(metadata) if metadata is not None else None,
                    envs=dict(env) if env is not None else None,
                    secure=True,
                    allow_internet_access=allow_internet_access,
                )
        except TimeoutError as error:
            raise E2BSandboxError(
                f'E2B sandbox creation did not complete within {_CREATE_TIMEOUT}s; '
                'the E2B control plane may be unreachable.'
            ) from error
        except e2b.AuthenticationException as e:
            raise E2BSandboxAuthError(_AUTH_MESSAGE) from e
        except Exception as e:
            raise E2BSandboxError(f'Could not start E2B sandbox: {type(e).__name__}: {e}') from e
        return cls(sandbox, working_dir=working_dir, sandbox_timeout=sandbox_timeout)

    @classmethod
    async def create_or_connect(
        cls,
        *,
        identity: Mapping[str, str],
        template: str | None = None,
        sandbox_timeout: int = DEFAULT_SANDBOX_TIMEOUT,
        working_dir: str | None = None,
        env: Mapping[str, str] | None = None,
        metadata: Mapping[str, str] | None = None,
        allow_internet_access: bool = True,
    ) -> Self:
        """Reconnect by metadata or create an owned sandbox once.

        This is the retry-safe acquisition API used by the capability and applications that
        manage durable sandbox lifecycle themselves. E2B does not enforce metadata uniqueness,
        so a post-create query keeps the oldest match and kills a racing duplicate.
        """
        existing_id = await cls._find_id(identity)
        if existing_id is not None:
            return await cls.connect(existing_id, working_dir=working_dir)
        created = await cls.create(
            template=template,
            sandbox_timeout=sandbox_timeout,
            working_dir=working_dir,
            env=env,
            metadata=metadata,
            allow_internet_access=allow_internet_access,
        )
        canonical_id = await cls._find_id(identity)
        if canonical_id is None or canonical_id == created.sandbox_id:
            return created
        await created.close(terminate=True)
        return await cls.connect(canonical_id, working_dir=working_dir)

    @classmethod
    async def _find_id(cls, metadata: Mapping[str, str]) -> str | None:
        """Return the oldest running or paused sandbox ID matching metadata."""
        try:
            import e2b
        except ImportError as error:
            raise E2BSandboxError(_MISSING_E2B) from error
        try:
            paginator = e2b.AsyncSandbox.list(query=e2b.SandboxQuery(metadata=dict(metadata)))
            oldest: e2b.SandboxInfo | None = None
            while paginator.has_next:
                for match in await paginator.next_items():
                    if oldest is None or match.started_at < oldest.started_at:
                        oldest = match
        except e2b.AuthenticationException as error:
            raise E2BSandboxAuthError(_AUTH_MESSAGE) from error
        except Exception as error:
            raise E2BSandboxError(f'Could not list E2B sandboxes: {type(error).__name__}: {error}') from error
        return oldest.sandbox_id if oldest is not None else None

    @classmethod
    async def connect(cls, sandbox_id: str, *, timeout: int | None = None, working_dir: str | None = None) -> Self:
        """Attach to an E2B sandbox that already exists, without taking over its lifecycle.

        E2B resumes a paused sandbox on connect, so attaching to one that was paused restarts
        it; a sandbox that is gone raises `E2BSandboxUnavailableError` instead of resolving to
        a dead environment.

        Args:
            sandbox_id: The E2B sandbox to attach to.
            timeout: Extends the sandbox's remaining lifetime to this many seconds when it is
                longer than what remains. E2B substitutes its 300-second default when `None`,
                so connecting extends a shorter remaining lifetime to at least 300 seconds.
            working_dir: Directory commands run in; the sandbox's own default when `None`.
        """
        try:
            import e2b
        except ImportError as e:
            raise E2BSandboxError(_MISSING_E2B) from e

        try:
            sandbox = await e2b.AsyncSandbox.connect(sandbox_id, timeout=timeout)
        except e2b.AuthenticationException as e:
            raise E2BSandboxAuthError(_AUTH_MESSAGE) from e
        except e2b.SandboxNotFoundException as e:
            raise E2BSandboxUnavailableError(_attached_gone_message(sandbox_id)) from e
        except Exception as e:
            raise E2BSandboxError(f'Could not connect to E2B sandbox {sandbox_id!r}: {type(e).__name__}: {e}') from e
        return cls(sandbox, working_dir=working_dir)

    async def close(self, *, terminate: bool) -> None:
        """Release this handle, killing the sandbox with it when we own its lifetime.

        Runs shielded from cancellation, since a run that is being torn down must still get its
        kill request out, bounded so a stalled control plane cannot wedge the caller. E2B has
        no client-side connection to release, so releasing an attached sandbox does nothing.
        """
        if not terminate:
            return
        await _kill_sandbox(self.sandbox_id, self.sandbox.kill)

    @staticmethod
    async def kill_by_id(sandbox_id: str) -> None:
        """Kill a sandbox by ID without reconnecting to it first.

        This is the retry-safe release API used by the capability and applications that manage
        durable sandbox lifecycle themselves; avoiding reconnect also avoids resuming a paused sandbox.
        """
        try:
            import e2b
        except ImportError as error:
            raise E2BSandboxError(_MISSING_E2B) from error
        await _kill_sandbox(sandbox_id, functools.partial(e2b.AsyncSandbox.kill, sandbox_id))

    async def working_dir(self) -> str:
        """The sandbox's default working directory (absolute POSIX path)."""
        # E2B exposes no API for a sandbox's working directory -- it is the template's unless
        # this backend was given one -- so ask the environment itself. It cannot change, so the
        # probe is an idempotent read: overlapping first calls may each run their own `pwd`,
        # get the same answer, and the cache converges. No lock needed.
        if self._working_dir is None:
            result = await self.run(['pwd'], timeout=_INTERNAL_EXEC_TIMEOUT)
            printed = result.stdout.strip()
            # Only an absolute path is an answer. Caching whatever else the environment
            # printed would hand every later `resolve()` a working directory that is not
            # one, mis-resolving relative paths with no error.
            if result.exit_code != 0 or not posixpath.isabs(printed):
                raise E2BSandboxError(
                    f'Could not determine the working directory of E2B sandbox {self.sandbox_id!r}: '
                    f'`pwd` exited {result.exit_code} and printed {result.stdout!r}. Use absolute paths.'
                )
            self._working_dir = printed
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
        """Execute a command and wait for it to complete.

        A cancelled wait kills the command rather than leaving it running, which is the
        protocol's cancellation contract; the kill is best effort, and the sandbox's own
        lifetime remains the backstop.
        """
        process = await self.start(command, shell=shell, cwd=cwd, env=env, timeout=timeout)
        try:
            return await process.wait()
        except SandboxTimeoutError:
            # The deadline path already killed it; a second request would only be noise.
            raise
        except BaseException:
            # Cancellation, and any failure to read the result, both leave a command that may
            # still be running.
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
    ) -> _E2BProcess:
        """Start a command without waiting, returning a handle to the running process."""
        line = _command_line(command, shell)
        cwd = absolute_path('cwd', cwd)
        if timeout is not None and (not math.isfinite(timeout) or timeout <= 0):
            raise ValueError(f'timeout must be a positive finite number or None, got {timeout!r}.')
        # Stamped before the call, so the deadline the protocol promises is measured from
        # `start()` rather than from the moment E2B confirms the command started.
        started = time.monotonic()
        try:
            handle = await self.sandbox.commands.run(
                line,
                background=True,
                envs=dict(env) if env is not None else None,
                cwd=cwd if cwd is not None else self._working_dir,
                timeout=_SDK_STREAM_UNBOUNDED,
            )
        except Exception as e:
            raise await self.operation_error(e, 'Command could not run in the sandbox') from e
        return _E2BProcess(handle, backend=self, deadline=timeout, started=started)

    def _unavailable_message(self) -> str:
        if self._sandbox_timeout is None:
            return _attached_gone_message(self.sandbox_id)
        return (
            'The E2B sandbox is no longer running (it may have reached its '
            f'sandbox_timeout of {self._sandbox_timeout}s, or been killed). '
            'Start a new run, or raise sandbox_timeout for longer work.'
        )

    async def operation_error(self, e: Exception, context: str) -> E2BSandboxError:
        """Map an exception raised while using the sandbox.

        Rejected credentials and a sandbox E2B cannot find are terminal. A `TimeoutException`
        is ambiguous -- E2B raises it both for a request the sandbox never answered and for one
        aborted because the sandbox died -- so it is classified by asking whether the sandbox is
        still running. Everything else stays a recoverable `E2BSandboxError`.
        """
        import e2b

        if isinstance(e, e2b.AuthenticationException):
            return E2BSandboxAuthError(_AUTH_MESSAGE)
        if isinstance(e, e2b.SandboxNotFoundException):
            return E2BSandboxUnavailableError(self._unavailable_message())
        if isinstance(e, e2b.TimeoutException):
            return await self._probe_ambiguous(e, context)
        if isinstance(e, e2b.SandboxException):
            return E2BSandboxError(f'{context}: {e}')
        return E2BSandboxError(f'{context}: {type(e).__name__}: {e}')

    async def _probe_ambiguous(self, e: Exception, context: str) -> E2BSandboxError:
        """Classify an E2B error that may mask sandbox death by probing the sandbox.

        E2B maps an unanswered envd request to `TimeoutException` whether the sandbox is alive
        and slow or gone; its health probe recovers the distinction. Probing only after an
        error keeps the extra round trip off successful operations.
        """
        try:
            running = await self.sandbox.is_running()
        except Exception:
            # The classifying probe can itself fail, including with a raw transport error; fall
            # back to the original error rather than letting the probe abort the run.
            return E2BSandboxError(f'{context}: {e}')
        if not running:
            return E2BSandboxUnavailableError(self._unavailable_message())
        return E2BSandboxError(f'{context}: {e}')


def _attached_gone_message(sandbox_id: str) -> str:
    return (
        f'The E2B sandbox {sandbox_id!r} is no longer running '
        '(it does not exist, was killed, or expired at its configured lifetime). '
        'Attach to a live sandbox, or create a new one.'
    )


if TYPE_CHECKING:
    # Pins full structural conformance -- signatures included -- which `isinstance` cannot
    # check. `__new__` rather than a call, because neither SDK object can be constructed
    # without a live sandbox behind it; this block never runs. There is deliberately no
    # `SupportsStream` pin: see `_E2BProcess`.
    _sandbox = e2b.AsyncSandbox.__new__(e2b.AsyncSandbox)
    _handle = e2b.AsyncCommandHandle.__new__(e2b.AsyncCommandHandle)
    _backend = E2BSandboxBackend(_sandbox)
    _process = _E2BProcess(_handle, backend=_backend, deadline=None, started=0.0)
    _backend_conforms: SandboxBackend = _backend
    _filesystem_backend_conforms: SupportsFilesystem = _backend
    _start_conforms: SupportsStart = _backend
    _filesystem_conforms: SandboxFilesystem = _backend.fs
    _process_conforms: SandboxProcess = _process
