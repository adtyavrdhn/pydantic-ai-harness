"""Private helpers for capabilities adopting the run sandbox."""

from pathlib import Path

from pydantic_ai.exceptions import UserError
from pydantic_ai.sandboxes import LocalSandbox, Sandbox, UnavailableSandbox


def sandbox_or_local(sandbox: Sandbox) -> Sandbox:
    """Preserve a capability's existing host behavior when no run sandbox is attached."""
    try:
        backend = sandbox.backend
    except UserError:
        # A ref or provider-backed facade has not connected yet. Its first async
        # operation performs that connection, so it must remain unchanged.
        return sandbox
    if isinstance(backend, UnavailableSandbox):
        return Sandbox.wrap(LocalSandbox(root=Path.cwd()))
    return sandbox
