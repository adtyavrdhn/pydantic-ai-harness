"""Capability that supplies a Modal sandbox to an agent run."""

from __future__ import annotations

import hashlib
import math
import posixpath
from collections.abc import Mapping
from dataclasses import dataclass

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.sandboxes import SandboxBackend, SandboxRef
from pydantic_ai.tools import AgentDepsT, RunContext
from pydantic_ai.toolsets import AgentToolset

from pydantic_ai_harness.modal_sandbox._backend import (
    DEFAULT_APP_NAME,
    DEFAULT_IMAGE,
    DEFAULT_SANDBOX_TIMEOUT,
    PROVIDER,
    ModalSandboxBackend,
    ModalSandboxError,
    ModalSandboxUnavailableError,
)
from pydantic_ai_harness.modal_sandbox._session import ModalSandboxSession
from pydantic_ai_harness.modal_sandbox._toolset import ModalSandboxToolset

_DEFAULT_MAX_OUTPUT_BYTES = 50 * 1024
_DEFAULT_MAX_OUTPUT_LINES = 2000
_DEFAULT_MAX_READ_BYTES = 5 * 1024 * 1024

_OWNED_INSTRUCTIONS = (
    'You have a Modal sandbox: an isolated, ephemeral cloud container. Use `run_command` to run '
    'shell commands in it, and `read_file` / `write_file` / `list_directory` to manage files. '
    'Commands run through `sh`, so pipes and redirection work. A command times out after '
    '{default_timeout}s unless you pass `timeout_seconds` (up to {max_timeout}s). The sandbox '
    'is reset between runs, so persist anything important outside it.'
)

_ATTACHED_INSTRUCTIONS = (
    'You have a Modal sandbox: an isolated cloud container. Use `run_command` to run shell '
    'commands in it, and `read_file` / `write_file` / `list_directory` to manage files. '
    'Commands run through `sh`, so pipes and redirection work. A command times out after '
    '{default_timeout}s unless you pass `timeout_seconds` (up to {max_timeout}s). This sandbox '
    'persists across runs, so files from earlier runs can still be present.'
)


def _sandbox_name(run_id: str) -> str:
    """Return a Modal-safe, deterministic name for one logical run."""
    digest = hashlib.sha256(run_id.encode()).hexdigest()[:32]
    return f'pydantic-ai-{digest}'


@dataclass(kw_only=True)
class ModalSandbox(AbstractCapability[AgentDepsT]):
    """Supply an isolated [Modal](https://modal.com) sandbox through `ctx.sandbox`.

    By default, one named sandbox is created or reused for each logical run and
    terminated when that run ends. The deterministic name makes acquisition safe
    to retry across durable workers. Set `sandbox_id` to attach to an existing
    sandbox without taking ownership of its lifetime.

    This capability supplies execution only. Compose it with tools or capabilities
    that consume [`RunContext.sandbox`][pydantic_ai.tools.RunContext.sandbox].
    """

    image: str = DEFAULT_IMAGE
    """Registry image used for an owned sandbox."""

    sandbox_id: str | None = None
    """Existing Modal sandbox ID to attach to without terminating it."""

    session: ModalSandboxSession | None = None
    """An already-open released `ModalSandboxSession` whose lifetime remains caller-owned."""

    app_name: str = DEFAULT_APP_NAME
    """Deployed Modal app that owns named sandboxes."""

    create_app_if_missing: bool = True
    """Whether Modal may create the app when it does not exist."""

    sandbox_timeout: int = DEFAULT_SANDBOX_TIMEOUT
    """Server-side lifetime backstop for an owned sandbox, in seconds."""

    workdir: str | None = None
    """Absolute working directory for an owned sandbox, or Modal's default."""

    env: Mapping[str, str] | None = None
    """Environment variables configured on an owned sandbox."""

    default_command_timeout: float = 60.0
    """Compatibility field for the released model-facing toolset configuration."""

    max_command_timeout: int | None = None
    """Compatibility field for the released model-facing toolset configuration."""

    max_output_bytes: int = _DEFAULT_MAX_OUTPUT_BYTES
    """Compatibility field for the released model-facing toolset configuration."""

    max_output_lines: int = _DEFAULT_MAX_OUTPUT_LINES
    """Compatibility field for the released model-facing toolset configuration."""

    max_read_bytes: int = _DEFAULT_MAX_READ_BYTES
    """Compatibility field for the released model-facing toolset configuration."""

    instructions: str | None = None
    """Optional instructions retained from the released capability API."""

    def __post_init__(self) -> None:
        if type(self.sandbox_timeout) is not int or self.sandbox_timeout <= 0:
            raise ValueError(f'sandbox_timeout must be a positive integer, got {self.sandbox_timeout!r}.')
        if (
            type(self.default_command_timeout) is bool
            or not math.isfinite(self.default_command_timeout)
            or self.default_command_timeout <= 0
        ):
            raise ValueError(
                f'default_command_timeout must be a positive finite number, got {self.default_command_timeout!r}.'
            )
        if self.max_command_timeout is not None and (
            type(self.max_command_timeout) is not int or self.max_command_timeout <= 0
        ):
            raise ValueError(
                f'max_command_timeout must be a positive integer or None, got {self.max_command_timeout!r}.'
            )
        for name in ('max_output_bytes', 'max_output_lines', 'max_read_bytes'):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f'{name} must be a positive integer, got {value!r}.')
        if self.instructions is not None and type(self.instructions) is not str:
            raise ValueError(f'instructions must be a string or None, got {self.instructions!r}.')
        if self.env is not None:
            self.env = dict(self.env)
        if self.workdir is not None and not posixpath.isabs(self.workdir):
            raise ValueError(f'workdir must be an absolute sandbox path or None, got {self.workdir!r}.')
        if self.session is not None:
            conflicts = [
                name
                for name, value, default in (
                    ('sandbox_id', self.sandbox_id, None),
                    ('image', self.image, DEFAULT_IMAGE),
                    ('app_name', self.app_name, DEFAULT_APP_NAME),
                    ('create_app_if_missing', self.create_app_if_missing, True),
                    ('sandbox_timeout', self.sandbox_timeout, DEFAULT_SANDBOX_TIMEOUT),
                    ('workdir', self.workdir, None),
                    ('env', self.env, None),
                )
                if value != default
            ]
            if conflicts:
                raise ValueError(
                    f'{", ".join(conflicts)} cannot be combined with `session`, which already owns '
                    'the sandbox and its configuration.' + self._command_ceiling_hint(conflicts)
                )
            return
        if self.sandbox_id is None:
            ceiling = self.max_command_timeout
            if ceiling is not None and ceiling > self.sandbox_timeout:
                raise ValueError(
                    f'max_command_timeout ({ceiling}) cannot exceed sandbox_timeout '
                    f'({self.sandbox_timeout}) for an owned sandbox: the sandbox is reaped '
                    'before such a command could finish. Raise sandbox_timeout instead.'
                )
            return
        conflicts = [
            name
            for name, value, default in (
                ('image', self.image, DEFAULT_IMAGE),
                ('app_name', self.app_name, DEFAULT_APP_NAME),
                ('create_app_if_missing', self.create_app_if_missing, True),
                ('sandbox_timeout', self.sandbox_timeout, DEFAULT_SANDBOX_TIMEOUT),
                ('workdir', self.workdir, None),
                ('env', self.env, None),
            )
            if value != default
        ]
        if conflicts:
            raise ValueError(
                f'{", ".join(conflicts)} only apply when creating a sandbox, but `sandbox_id` '
                'attaches to an existing one. Remove them, or drop `sandbox_id` to create a sandbox.'
                + self._command_ceiling_hint(conflicts)
            )

    @staticmethod
    def _command_ceiling_hint(rejected: list[str]) -> str:
        if 'sandbox_timeout' not in rejected:
            return ''
        return ' To raise the per-command timeout ceiling on a reused sandbox, set `max_command_timeout`.'

    async def acquire_sandbox(self, ctx: RunContext[AgentDepsT]) -> SandboxRef:
        """Create or reuse the sandbox for this logical run."""
        if self.session is not None:
            sandbox_id = self.session.sandbox_id
            if sandbox_id is None:
                raise ModalSandboxError('`session` must already be entered before the agent run starts.')
            return SandboxRef(provider=PROVIDER, sandbox_id=sandbox_id)
        if self.sandbox_id is not None:
            return SandboxRef(provider=PROVIDER, sandbox_id=self.sandbox_id)
        if ctx.run_id is None:  # pragma: no cover - core assigns it before acquisition
            raise RuntimeError('Modal sandbox acquisition requires a run ID.')
        backend = await ModalSandboxBackend.create_or_connect(
            name=_sandbox_name(ctx.run_id),
            image=self.image,
            app_name=self.app_name,
            create_app_if_missing=self.create_app_if_missing,
            sandbox_timeout=self.sandbox_timeout,
            workdir=self.workdir,
            env=self.env,
        )
        ref = SandboxRef(provider=PROVIDER, sandbox_id=backend.sandbox_id)
        await backend.close(terminate=False)
        return ref

    def get_instructions(self) -> str | None:
        """Explain the released tools unless instructions were overridden or disabled."""
        if self.instructions is not None:
            return self.instructions or None
        reused = self.sandbox_id is not None or self.session is not None
        template = _ATTACHED_INSTRUCTIONS if reused else _OWNED_INSTRUCTIONS
        ceiling = self.max_command_timeout if self.max_command_timeout is not None else self.sandbox_timeout
        default_timeout = min(max(1, math.ceil(self.default_command_timeout)), ceiling)
        return template.format(default_timeout=default_timeout, max_timeout=ceiling)

    def get_toolset(self) -> AgentToolset[AgentDepsT]:
        """Build the released four-tool Modal adapter."""
        return ModalSandboxToolset[AgentDepsT](
            image=self.image,
            sandbox_id=self.sandbox_id,
            app_name=self.app_name,
            create_app_if_missing=self.create_app_if_missing,
            sandbox_timeout=self.sandbox_timeout,
            workdir=self.workdir,
            default_command_timeout=self.default_command_timeout,
            max_command_timeout=self.max_command_timeout,
            max_output_bytes=self.max_output_bytes,
            max_output_lines=self.max_output_lines,
            max_read_bytes=self.max_read_bytes,
            env=self.env,
            session=self.session,
        )

    async def get_sandbox(self, ctx: RunContext[AgentDepsT], ref: SandboxRef | None) -> SandboxBackend | None:
        """Reconnect to a referenced Modal sandbox without provisioning one."""
        if ref is None or ref.provider != PROVIDER:
            return None
        return await ModalSandboxBackend.connect(ref.sandbox_id)

    async def release_sandbox(self, ctx: RunContext[AgentDepsT], ref: SandboxRef) -> None:
        """Terminate an owned sandbox; leave an attached sandbox to its owner."""
        if self.session is not None or self.sandbox_id is not None or ref.provider != PROVIDER:
            return
        try:
            backend = await ModalSandboxBackend.connect(ref.sandbox_id)
        except ModalSandboxUnavailableError:
            return
        await backend.close(terminate=True)
