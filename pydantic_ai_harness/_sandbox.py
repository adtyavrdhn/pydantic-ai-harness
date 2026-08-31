"""Private helpers for capabilities adopting the run sandbox."""

import os
from pathlib import Path

from pydantic_ai.exceptions import UserError
from pydantic_ai.sandboxes import LocalSandbox, Sandbox


def is_framework_unavailable(sandbox: Sandbox) -> bool:
    """Whether `sandbox` is Pydantic AI's implicit no-sandbox placeholder."""
    return sandbox._is_framework_default()  # pyright: ignore[reportPrivateUsage]


def sandbox_or_local(sandbox: Sandbox) -> Sandbox:
    """Use an explicit local fallback only for Pydantic AI's implicit unavailable sandbox.

    An application-supplied `UnavailableSandbox` is policy, not absence, so its
    configured error must survive. The fallback is POSIX-only because
    `LocalSandbox` deliberately cannot promise process-tree cancellation on Windows.
    """
    if os.name == 'posix' and is_framework_unavailable(sandbox):
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
