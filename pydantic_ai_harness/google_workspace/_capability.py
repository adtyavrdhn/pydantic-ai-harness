"""Google Workspace hosted MCP capability."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import KW_ONLY, dataclass, field
from typing import Literal

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import UserError
from pydantic_ai.tools import AgentDepsT
from pydantic_ai.toolsets import AbstractToolset, CombinedToolset

from pydantic_ai_harness._mcp import MCPAuth, MCPAuthFunc, credential, is_read_only, per_run

try:
    from pydantic_ai.mcp import MCPToolset
except ImportError as _import_error:  # pragma: no cover
    raise ImportError(
        'MCP support is required for the Google Workspace capability. '
        'Install it with: uv add "pydantic-ai-harness[google-workspace]"'
    ) from _import_error

GoogleWorkspaceService = Literal['gmail', 'drive', 'docs', 'sheets', 'slides', 'calendar', 'chat', 'people']
"""A Google Workspace product Google serves over MCP."""

_MCP_URLS: dict[str, str] = {
    'gmail': 'https://gmailmcp.googleapis.com/mcp/v1',
    'drive': 'https://drivemcp.googleapis.com/mcp/v1',
    'docs': 'https://docsmcp.googleapis.com/mcp/v1',
    'sheets': 'https://sheetsmcp.googleapis.com/mcp/v1',
    'slides': 'https://slidesmcp.googleapis.com/mcp/v1',
    'calendar': 'https://calendarmcp.googleapis.com/mcp/v1',
    'chat': 'https://chatmcp.googleapis.com/mcp/v1',
    'people': 'https://people.googleapis.com/mcp/v1',
}

_DEFAULT_DESCRIPTION = 'Use Gmail, Calendar, Drive, and the other Google Workspace products.'


@dataclass
class GoogleWorkspace(AbstractCapability[AgentDepsT]):
    """Give an agent the tools of Google's hosted Workspace MCP servers for the selected products.

    This includes tools that send, change, and delete; the token's scopes decide what they can reach.
    """

    services: GoogleWorkspaceService | Sequence[GoogleWorkspaceService]
    """Workspace products to expose, such as `'gmail'` or `['gmail', 'calendar']`."""

    _: KW_ONLY

    description: str | None = _DEFAULT_DESCRIPTION
    """Describes the capability when the agent loads it on demand."""

    auth: MCPAuth | MCPAuthFunc[AgentDepsT] | None = field(default=None, repr=False)
    """A Google access token, an `httpx.Auth`, or a function of the run context that returns one.

    Unset, it uses `GOOGLE_ACCESS_TOKEN`. If the function returns `None`, that run has no Google Workspace tools.
    """

    read_only: bool = False
    """Keep only the tools Google marks as read-only."""

    include_instructions: bool = True
    """Pass the servers' own instructions to the agent."""

    def __post_init__(self) -> None:
        """Normalize `services` to a tuple of products that have an endpoint."""
        self.services = (self.services,) if isinstance(self.services, str) else tuple(dict.fromkeys(self.services))
        if not self.services:
            raise UserError('Google Workspace needs at least one service.')
        for service in self.services:
            if service not in _MCP_URLS:
                raise UserError(f'Unknown Google Workspace service {service!r}; expected one of {sorted(_MCP_URLS)}.')

    def get_toolset(self) -> AbstractToolset[AgentDepsT]:
        """Return the tools for the selected products, with names prefixed by product."""
        toolset = per_run(self.auth, self._connect, id=self.id or 'google-workspace')
        return toolset.filtered(lambda _ctx, tool_def: is_read_only(tool_def)) if self.read_only else toolset

    def _connect(self, auth: MCPAuth | None) -> AbstractToolset[AgentDepsT]:
        auth = credential(auth, env='GOOGLE_ACCESS_TOKEN', service='Google Workspace')
        prefix = self.id or 'google-workspace'
        return CombinedToolset(
            [
                MCPToolset[AgentDepsT](
                    _MCP_URLS[service],
                    id=f'{prefix}-{service}',
                    auth=auth,
                    include_instructions=self.include_instructions,
                ).prefixed(service)
                for service in self.services
            ]
        )
