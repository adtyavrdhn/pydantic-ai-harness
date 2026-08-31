"""Modal sandbox capability: gives agents an isolated cloud sandbox to work in.

`ModalSandbox` is the supported entry point; build an agent with it and add tools
that consume `ctx.sandbox`.
`ModalSandboxBackend` is the Modal implementation of Pydantic AI's sandbox backend protocol,
public for applications that want to create or attach to a sandbox themselves and pass it to
a run as `sandbox=`.
"""

from pydantic_ai_harness.modal_sandbox._backend import (
    ModalSandboxBackend,
    ModalSandboxCommandTimeoutError,
)
from pydantic_ai_harness.modal_sandbox._capability import ModalSandbox
from pydantic_ai_harness.modal_sandbox._session import (
    ModalSandboxAuthError,
    ModalSandboxError,
    ModalSandboxExecResult,
    ModalSandboxSession,
    ModalSandboxTerminalError,
    ModalSandboxUnavailableError,
)

__all__ = [
    'ModalSandbox',
    'ModalSandboxAuthError',
    'ModalSandboxBackend',
    'ModalSandboxCommandTimeoutError',
    'ModalSandboxError',
    'ModalSandboxExecResult',
    'ModalSandboxSession',
    'ModalSandboxTerminalError',
    'ModalSandboxUnavailableError',
]
