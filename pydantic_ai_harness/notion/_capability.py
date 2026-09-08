"""Notion hosted MCP: `https://mcp.notion.com/mcp`.

Verified 2026-09-08 against https://developers.notion.com/guides/mcp/mcp-supported-tools.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from httpx import Auth
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.tools import AgentDepsT
from pydantic_ai.toolsets import AbstractToolset

try:
    from pydantic_ai.mcp import MCPToolset
except ImportError as _import_error:  # pragma: no cover
    raise ImportError(
        'MCP support is required for the Notion capability. Install it with: uv add "pydantic-ai-harness[notion]"'
    ) from _import_error

_NOTION_MCP_URL = 'https://mcp.notion.com/mcp'

_DEFAULT_DESCRIPTION = "Search, read, and change the connected user's Notion workspace."

_READ_TOOL_NAMES = frozenset(
    {
        'notion-ai-search',
        'notion-download-attachment',
        'notion-fetch',
        'notion-get-async-task',
        'notion-get-comments',
        'notion-get-session-status',
        'notion-get-teams',
        'notion-get-users',
        'notion-list-agents',
        'notion-list-session-events',
        'notion-query-data-sources',
        'notion-query-meeting-notes',
        'notion-query-sessions',
        'notion-read-session-event',
        'notion-search',
        'notion-search-agents',
        'notion-search-sessions',
        'notion-search-skills',
    }
)


@dataclass(kw_only=True)
class Notion(AbstractCapability[AgentDepsT]):
    """Connect an agent to Notion's hosted MCP server.

    The default exposes every tool the connection can use, including the ones that create, update,
    and move pages. The OAuth grant decides which workspace content those tools reach.
    """

    description: str | None = _DEFAULT_DESCRIPTION
    """Routing description used when the capability is loaded on demand."""

    auth: Auth | Literal['oauth'] | str = field(default='oauth', repr=False)
    """`'oauth'` for browser login, a Notion OAuth access token, or a custom `httpx.Auth`."""

    read_only: bool = False
    """Expose only the tools this package classifies as reads; Notion does not classify its own, so a
    tool it adds later stays hidden until that list is updated here."""

    def get_toolset(self) -> AbstractToolset[AgentDepsT]:
        """Build the Notion MCP connection."""
        toolset: AbstractToolset[AgentDepsT] = MCPToolset(
            _NOTION_MCP_URL, id=self.id or 'notion', auth=self.auth, include_instructions=True
        )
        if self.read_only:
            return toolset.filtered(lambda _ctx, tool_def: tool_def.name in _READ_TOOL_NAMES)
        return toolset

    @classmethod
    def from_spec(
        cls,
        *,
        id: str | None = None,
        description: str | None = _DEFAULT_DESCRIPTION,
        defer_loading: bool = False,
        read_only: bool = False,
    ) -> Notion[AgentDepsT]:
        """Construct a Notion capability from serializable options.

        `auth` is absent by design, so a spec file cannot carry a Notion token: the credential comes
        from the browser OAuth grant, or from `auth=` in Python.
        """
        return cls(id=id, description=description, defer_loading=defer_loading, read_only=read_only)

    @classmethod
    def get_serialization_name(cls) -> str:
        """Return the agent-spec capability name."""
        return 'Notion'
