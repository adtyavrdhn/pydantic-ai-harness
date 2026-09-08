"""Linear hosted MCP capability."""

from __future__ import annotations

from dataclasses import dataclass, field
from os import environ
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


@dataclass(kw_only=True)
class Linear(AbstractCapability[AgentDepsT]):
    """Connect an agent to Linear's hosted MCP server.

    The default endpoint serves Linear's write tools; the token's scopes decide what the agent can
    read or change.
    """

    description: str | None = _DEFAULT_DESCRIPTION
    """Routing description used when the capability is loaded on demand."""

    auth: Auth | Literal['oauth'] | str | None = field(default=None, repr=False)
    """`'oauth'` for browser login, a Linear API key or OAuth token, or a custom `httpx.Auth`.

    Defaults to `$LINEAR_ACCESS_TOKEN`.
    """

    read_only: bool = False
    """Connect to Linear's read-only endpoint, which only ever exposes read tools."""

    def get_toolset(self) -> MCPToolset[AgentDepsT]:
        """Build the Linear MCP connection."""
        auth = self.auth or environ.get('LINEAR_ACCESS_TOKEN')
        if not auth:
            raise UserError('Linear needs a token: pass auth= or set LINEAR_ACCESS_TOKEN.')
        url = _LINEAR_READ_ONLY_MCP_URL if self.read_only else _LINEAR_MCP_URL
        return MCPToolset(url, id=self.id or 'linear', auth=auth, include_instructions=True)

    @classmethod
    def from_spec(
        cls,
        *,
        id: str | None = None,
        description: str | None = _DEFAULT_DESCRIPTION,
        defer_loading: bool = False,
        read_only: bool = False,
    ) -> Linear[AgentDepsT]:
        """Construct a Linear capability from serializable options.

        `auth` is absent by design, so a spec file cannot carry a Linear token: the credential comes
        from `$LINEAR_ACCESS_TOKEN`.
        """
        return cls(id=id, description=description, defer_loading=defer_loading, read_only=read_only)

    @classmethod
    def get_serialization_name(cls) -> str:
        """Return the agent-spec capability name."""
        return 'Linear'
