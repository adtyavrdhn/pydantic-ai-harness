"""Capability that supplies an E2B sandbox to an agent run."""

from __future__ import annotations

import posixpath
from collections.abc import Mapping
from dataclasses import dataclass

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.sandboxes import SandboxBackend, SandboxRef
from pydantic_ai.tools import AgentDepsT, RunContext

from pydantic_ai_harness.e2b_sandbox._backend import (
    DEFAULT_SANDBOX_TIMEOUT,
    PROVIDER,
    E2BSandboxBackend,
)

_RUN_ID_METADATA_KEY = 'pydantic-ai-run-id'


@dataclass(kw_only=True)
class E2BSandbox(AbstractCapability[AgentDepsT]):
    """Supply an isolated [E2B](https://e2b.dev) sandbox through `ctx.sandbox`.

    Owned acquisition records the logical run ID as E2B metadata and reconnects
    to an existing match before creating. Set `sandbox_id` to attach to an
    environment managed elsewhere without taking ownership of its lifetime.

    This capability supplies execution only. Compose it with tools or
    capabilities that consume
    [`RunContext.sandbox`][pydantic_ai.tools.RunContext.sandbox].
    """

    template: str | None = None
    """E2B template name or ID for an owned sandbox."""

    sandbox_id: str | None = None
    """Existing E2B sandbox ID to attach to without killing it."""

    sandbox_timeout: int = DEFAULT_SANDBOX_TIMEOUT
    """Server-side lifetime backstop for an owned sandbox, in seconds."""

    workdir: str | None = None
    """Absolute working directory for commands and relative filesystem paths."""

    env: Mapping[str, str] | None = None
    """Environment variables configured on an owned sandbox."""

    metadata: Mapping[str, str] | None = None
    """Metadata added to an owned sandbox."""

    allow_internet_access: bool = True
    """Whether an owned sandbox may reach the internet."""

    def __post_init__(self) -> None:
        if type(self.sandbox_timeout) is not int or self.sandbox_timeout <= 0:
            raise ValueError(f'sandbox_timeout must be a positive integer, got {self.sandbox_timeout!r}.')
        if self.workdir is not None and not posixpath.isabs(self.workdir):
            raise ValueError(f'workdir must be an absolute sandbox path or None, got {self.workdir!r}.')
        if self.workdir is not None:
            self.workdir = posixpath.normpath(self.workdir)
        if type(self.allow_internet_access) is not bool:
            raise ValueError(f'allow_internet_access must be a boolean, got {self.allow_internet_access!r}.')
        if self.env is not None:
            self.env = dict(self.env)
        if self.metadata is not None:
            self.metadata = dict(self.metadata)
            if _RUN_ID_METADATA_KEY in self.metadata:
                raise ValueError(f'metadata key {_RUN_ID_METADATA_KEY!r} is reserved for retry-safe acquisition.')
        if self.sandbox_id is None:
            return
        conflicts = [
            name
            for name, value, default in (
                ('template', self.template, None),
                ('sandbox_timeout', self.sandbox_timeout, DEFAULT_SANDBOX_TIMEOUT),
                ('env', self.env, None),
                ('metadata', self.metadata, None),
                ('allow_internet_access', self.allow_internet_access, True),
            )
            if value != default
        ]
        if conflicts:
            raise ValueError(
                f'{", ".join(conflicts)} only apply when creating a sandbox, but `sandbox_id` '
                'attaches to an existing one.'
            )

    async def acquire_sandbox(self, ctx: RunContext[AgentDepsT]) -> SandboxRef:
        """Create or reuse the sandbox for this logical run."""
        if self.sandbox_id is not None:
            return SandboxRef(provider=PROVIDER, sandbox_id=self.sandbox_id)
        if ctx.run_id is None:  # pragma: no cover - core assigns it before acquisition
            raise RuntimeError('E2B sandbox acquisition requires a run ID.')
        metadata = dict(self.metadata or ())
        metadata[_RUN_ID_METADATA_KEY] = ctx.run_id
        backend = await E2BSandboxBackend._create_or_connect(  # pyright: ignore[reportPrivateUsage]
            identity={_RUN_ID_METADATA_KEY: ctx.run_id},
            template=self.template,
            sandbox_timeout=self.sandbox_timeout,
            working_dir=self.workdir,
            env=self.env,
            metadata=metadata,
            allow_internet_access=self.allow_internet_access,
        )
        return SandboxRef(provider=PROVIDER, sandbox_id=backend.sandbox_id)

    async def get_sandbox(self, ctx: RunContext[AgentDepsT], ref: SandboxRef | None) -> SandboxBackend | None:
        """Reconnect to a referenced E2B sandbox without provisioning one."""
        if ref is None or ref.provider != PROVIDER:
            return None
        return await E2BSandboxBackend.connect(ref.sandbox_id, working_dir=self.workdir)

    async def release_sandbox(self, ctx: RunContext[AgentDepsT], ref: SandboxRef) -> None:
        """Kill an owned sandbox; leave an attached sandbox to its owner."""
        if self.sandbox_id is not None or ref.provider != PROVIDER:
            return
        await E2BSandboxBackend._kill_by_id(ref.sandbox_id)  # pyright: ignore[reportPrivateUsage]
