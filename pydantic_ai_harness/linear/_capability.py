"""Linear hosted MCP capability.

Provider contract, verified 2026-09-07:

- `https://mcp.linear.app/mcp` is the read-write Streamable HTTP endpoint.
- `https://mcp.linear.app/mcp/readonly` is the read-only endpoint; Linear only registers read tools on it.
- Both endpoints require OAuth or a bearer token (an OAuth token or a Linear API key).

Source: https://linear.app/docs/mcp. Re-check these assumptions there before
changing endpoint or authentication behavior.
"""

from __future__ import annotations

from dataclasses import KW_ONLY, dataclass, field
from typing import Literal

from httpx import Auth
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import UserError
from pydantic_ai.tools import AgentDepsT

try:
    from pydantic_ai.mcp import MCPToolset
except ImportError as _import_error:  # pragma: no cover
    raise ImportError(
        'MCP support is required for the Linear capability. Install it with: uv add "pydantic-ai-harness[linear]"'
    ) from _import_error

_LINEAR_MCP_URL = 'https://mcp.linear.app/mcp'
_LINEAR_READ_ONLY_MCP_URL = 'https://mcp.linear.app/mcp/readonly'
_DEFAULT_DESCRIPTION = 'Use Linear issues, projects, and teams.'


@dataclass
class Linear(AbstractCapability[AgentDepsT]):
    """Connect an agent to Linear's hosted MCP server.

    The default uses Linear's read-only endpoint, so the server decides which tools exist.
    Set `access='write'` to use the read-write endpoint.
    """

    _: KW_ONLY

    id: str | None = None
    """Capability ID. The toolset ID defaults to `linear`, so give two Linear capabilities distinct IDs."""

    description: str | None = _DEFAULT_DESCRIPTION
    """Routing description used when the capability is loaded on demand."""

    access: Literal['read', 'write'] = 'read'
    """`'read'` connects to Linear's read-only endpoint; `'write'` connects to the read-write endpoint."""

    auth: Auth | Literal['oauth'] | str = field(repr=False)
    """`'oauth'` for browser login, a Linear API key or OAuth token, or a custom `httpx.Auth`."""

    def __post_init__(self):
        if self.access not in ('read', 'write'):
            raise UserError('`access` must be `read` or `write`.')

    def get_toolset(self) -> MCPToolset[AgentDepsT]:
        """Build the Linear MCP connection."""
        url = _LINEAR_MCP_URL if self.access == 'write' else _LINEAR_READ_ONLY_MCP_URL
        return MCPToolset(url, id=self.id or 'linear', auth=self.auth)

    @classmethod
    def from_spec(
        cls,
        *,
        id: str | None = None,
        description: str | None = _DEFAULT_DESCRIPTION,
        defer_loading: bool = False,
        access: Literal['read', 'write'] = 'read',
        auth: Literal['oauth'] | str,
    ) -> Linear[AgentDepsT]:
        """Construct a Linear capability from serializable options."""
        return cls(id=id, description=description, defer_loading=defer_loading, access=access, auth=auth)

    @classmethod
    def get_serialization_name(cls) -> str:
        """Return the agent-spec capability name."""
        return 'Linear'
