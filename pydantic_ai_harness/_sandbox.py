"""Private helpers for capabilities adopting the run sandbox."""

import os
from pathlib import Path

from pydantic_ai.exceptions import UserError
from pydantic_ai.sandboxes import LocalSandbox, Sandbox, UnavailableSandbox

_FRAMEWORK_UNAVAILABLE_REASONS = (
    (
        'No sandbox is attached: this `RunContext` was created outside an agent run. '
        'Sandboxes are attached when a run starts \N{EM DASH} pass `sandbox=` to the run method or supply one '
        "from a capability's `acquire_sandbox`."
    ),
    (
        'No sandbox is attached to this run. Pass `sandbox=LocalSandbox()` to the run method to use the '
        'local machine (unsafe: commands and file operations run with the full permissions of this process), '
        'attach a capability that supplies a sandbox through its `acquire_sandbox` hook, or pass a `SandboxRef` '
        'to connect to an existing environment. See https://ai.pydantic.dev/sandbox/ for details.'
    ),
)


def is_framework_unavailable(sandbox: Sandbox) -> bool:
    """Whether `sandbox` is Pydantic AI's implicit no-sandbox placeholder."""
    try:
        backend = sandbox.backend
    except UserError:  # pragma: no cover - deferred provider facades connect on first async operation
        return False
    return isinstance(backend, UnavailableSandbox) and backend.reason in _FRAMEWORK_UNAVAILABLE_REASONS


def sandbox_or_local(sandbox: Sandbox, *, preserve_host_behavior: bool) -> Sandbox:
    """Use an explicit local fallback only for Pydantic AI's implicit unavailable sandbox.

    An application-supplied `UnavailableSandbox` is policy, not absence, so its
    configured error must survive. The fallback is POSIX-only because
    `LocalSandbox` deliberately cannot promise process-tree cancellation on Windows.
    """
    if preserve_host_behavior and os.name == 'posix' and is_framework_unavailable(sandbox):
        return Sandbox.wrap(LocalSandbox(root=Path.cwd()))
    return sandbox


def sandbox_path(path: Path, *, sandbox: Sandbox, original: Sandbox) -> str:
    """Return a sandbox spelling, preserving legacy `~` only for host fallback."""
    if path.parts and path.parts[0].startswith('~'):
        if sandbox is original:
            raise UserError(
                f'Sandbox paths do not expand `~`: {path!s}. '
                'Use an absolute path inside the sandbox or a path relative to its working directory.'
            )
        path = path.expanduser()
    return path.as_posix()
