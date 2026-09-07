"""Linear hosted MCP capability.

Provider contract, verified 2026-09-07:

- `https://mcp.linear.app/mcp` is the read-write Streamable HTTP endpoint.
- `https://mcp.linear.app/mcp/readonly` is the read-only endpoint.
- Both endpoints require OAuth or bearer-token authentication.

Source: https://linear.app/docs/mcp. Re-check these assumptions there before
changing endpoint or authentication behavior.
"""

from __future__ import annotations

from dataclasses import KW_ONLY, dataclass, field
from typing import Literal

from pydantic_ai.capabilities import AbstractCapability
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

    The default uses Linear's server-enforced read-only endpoint. Set
    `read_only=False` to use the read-write endpoint.
    """

    _: KW_ONLY

    id: str | None = None
    """Capability ID. The toolset defaults to `linear`."""

    description: str | None = _DEFAULT_DESCRIPTION
    """Routing description used when the capability is loaded on demand."""

    read_only: bool = True
    """Use Linear's server-enforced read-only endpoint."""

    auth: Literal['oauth'] | str = field(repr=False)
    """Use interactive OAuth or a bearer token."""

    def get_toolset(self) -> MCPToolset[AgentDepsT]:
        """Build the Linear MCP connection."""
        url = _LINEAR_READ_ONLY_MCP_URL if self.read_only else _LINEAR_MCP_URL
        toolset_id = self.id if self.id is not None else 'linear'
        return MCPToolset(url, id=toolset_id, auth=self.auth)

    @classmethod
    def from_spec(
        cls,
        *,
        id: str | None = None,
        description: str | None = _DEFAULT_DESCRIPTION,
        defer_loading: bool = False,
        read_only: bool = True,
        auth: Literal['oauth'] | str,
    ) -> Linear[AgentDepsT]:
        """Construct a Linear capability from serializable options."""
        return cls(
            id=id,
            description=description,
            defer_loading=defer_loading,
            read_only=read_only,
            auth=auth,
        )

    @classmethod
    def get_serialization_name(cls) -> str:
        """Return the agent-spec capability name."""
        return 'Linear'
