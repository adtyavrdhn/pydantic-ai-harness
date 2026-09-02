"""Capability that supplies a Modal sandbox to an agent run."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.sandboxes import SandboxBackend, SandboxRef
from pydantic_ai.tools import AgentDepsT, RunContext

from pydantic_ai_harness._sandbox_provider import absolute_path
from pydantic_ai_harness.modal_sandbox._backend import (
    DEFAULT_APP_NAME,
    DEFAULT_IMAGE,
    DEFAULT_SANDBOX_TIMEOUT,
    ModalSandboxBackend,
    ModalSandboxUnavailableError,
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

    def __post_init__(self) -> None:
        if self.sandbox_timeout <= 0:
            raise ValueError(f'sandbox_timeout must be a positive integer, got {self.sandbox_timeout!r}.')
        self.workdir = absolute_path('workdir', self.workdir)
        if self.env is not None:
            self.env = dict(self.env)
        if self.sandbox_id is None:
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
            )

    async def acquire_sandbox(self, ctx: RunContext[AgentDepsT]) -> SandboxRef:
        """Create or reuse the sandbox for this logical run."""
        if self.sandbox_id is not None:
            return SandboxRef(sandbox_id=self.sandbox_id)
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
        ref = SandboxRef(sandbox_id=backend.sandbox_id)
        await backend.close(terminate=False)
        return ref

    async def get_sandbox(self, ctx: RunContext[AgentDepsT], ref: SandboxRef) -> SandboxBackend | None:
        """Reconnect to a referenced Modal sandbox without provisioning one."""
        return await ModalSandboxBackend.connect(ref.sandbox_id)

    async def release_sandbox(self, ctx: RunContext[AgentDepsT], ref: SandboxRef) -> None:
        """Terminate an owned sandbox; leave an attached sandbox to its owner."""
        if self.sandbox_id is not None:
            return
        try:
            backend = await ModalSandboxBackend.connect(ref.sandbox_id)
        except ModalSandboxUnavailableError:
            return
        await backend.close(terminate=True)
