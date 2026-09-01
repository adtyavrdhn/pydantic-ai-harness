"""Capability that supplies a Daytona sandbox to an agent run."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.sandboxes import SandboxBackend, SandboxRef
from pydantic_ai.tools import AgentDepsT, RunContext

from pydantic_ai_harness._sandbox_provider import absolute_path
from pydantic_ai_harness.daytona_sandbox._backend import (
    DEFAULT_AUTO_STOP_MINUTES,
    PROVIDER,
    DaytonaSandboxBackend,
)


def _sandbox_name(run_id: str) -> str:
    return f'pydantic-ai-{hashlib.sha256(run_id.encode()).hexdigest()[:32]}'


@dataclass(kw_only=True)
class DaytonaSandbox(AbstractCapability[AgentDepsT]):
    """Supply an isolated Daytona sandbox through `ctx.sandbox`.

    Owned acquisition uses a deterministic name derived from the logical run ID,
    making retries reconnect instead of provisioning another sandbox. Set
    `sandbox_id` to attach without taking ownership of the sandbox lifetime.
    """

    sandbox_id: str | None = None
    """Existing Daytona sandbox ID or name to attach to without deleting it."""

    snapshot: str | None = None
    """Daytona snapshot used for an owned sandbox."""

    auto_stop_minutes: int = DEFAULT_AUTO_STOP_MINUTES
    """Idle minutes before Daytona stops an owned sandbox."""

    workdir: str | None = None
    """Absolute working directory for commands."""

    env: Mapping[str, str] | None = None
    """Environment variables configured on an owned sandbox."""

    network_block_all: bool = False
    """Whether to block outbound traffic from an owned sandbox."""

    def __post_init__(self) -> None:
        if self.auto_stop_minutes <= 0:
            raise ValueError(f'auto_stop_minutes must be a positive integer, got {self.auto_stop_minutes!r}.')
        self.workdir = absolute_path('workdir', self.workdir)
        if self.env is not None:
            self.env = dict(self.env)
        if self.sandbox_id is None:
            return
        conflicts = [
            name
            for name, value, default in (
                ('snapshot', self.snapshot, None),
                ('auto_stop_minutes', self.auto_stop_minutes, DEFAULT_AUTO_STOP_MINUTES),
                ('env', self.env, None),
                ('network_block_all', self.network_block_all, False),
            )
            if value != default
        ]
        if conflicts:
            raise ValueError(
                f'{", ".join(conflicts)} only apply when creating a sandbox, but `sandbox_id` '
                'attaches to an existing one.'
            )

    async def acquire_sandbox(self, ctx: RunContext[AgentDepsT]) -> SandboxRef:
        if self.sandbox_id is not None:
            return SandboxRef(provider=PROVIDER, sandbox_id=self.sandbox_id)
        if ctx.run_id is None:  # pragma: no cover - core assigns it before acquisition
            raise RuntimeError('Daytona sandbox acquisition requires a run ID.')
        backend = await DaytonaSandboxBackend.create_or_connect(
            name=_sandbox_name(ctx.run_id),
            snapshot=self.snapshot,
            auto_stop_minutes=self.auto_stop_minutes,
            working_dir=self.workdir,
            env=self.env,
            network_block_all=self.network_block_all,
        )
        ref = SandboxRef(provider=PROVIDER, sandbox_id=backend.sandbox_id)
        await backend.close(terminate=False)
        return ref

    async def get_sandbox(self, ctx: RunContext[AgentDepsT], ref: SandboxRef | None) -> SandboxBackend | None:
        if ref is None or ref.provider != PROVIDER:
            return None
        return await DaytonaSandboxBackend.connect(ref.sandbox_id, working_dir=self.workdir)

    async def release_sandbox(self, ctx: RunContext[AgentDepsT], ref: SandboxRef) -> None:
        if self.sandbox_id is not None or ref.provider != PROVIDER:
            return
        await DaytonaSandboxBackend.delete_by_id(ref.sandbox_id)
