"""Notion hosted MCP capability."""

from __future__ import annotations

from dataclasses import dataclass, field
from os import environ

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.tools import AgentDepsT
from pydantic_ai.toolsets import AbstractToolset

from pydantic_ai_harness._mcp import MCPAuth, MCPAuthFunc, MCPClientFunc, is_read_only, per_run_auth, per_run_client

try:
    from fastmcp.client.auth import OAuth
    from pydantic_ai.mcp import MCPToolset, MCPToolsetClient
except ImportError as exc:  # pragma: no cover
    raise ImportError('Install Notion support with: uv add "pydantic-ai-harness[notion]"') from exc


@dataclass(kw_only=True)
class Notion(AbstractCapability[AgentDepsT]):
    """Give the agent the tools of Notion's hosted MCP server, with the permissions of the connected user."""

    description: str | None = 'Search and change Notion workspace content.'
    auth: MCPAuth | MCPAuthFunc[AgentDepsT] | None = field(default=None, repr=False)
    """A Notion OAuth access token, `'oauth'`, an `httpx.Auth`, or a function of the run context that returns the current user's credential.

    Unset, it uses `NOTION_ACCESS_TOKEN`, then browser login. If the function returns `None`, that run has no Notion tools.
    """
    read_only: bool = False
    """Keep only the tools the server marks as read-only."""
    include_instructions: bool = True
    """Forward the server's instructions to the agent."""
    client: MCPToolsetClient | MCPClientFunc[AgentDepsT] | None = field(default=None, repr=False)
    """Your own FastMCP client or transport, or a function of the run context that returns one.

    The client owns the URL, authentication, and server settings.
    """

    def get_toolset(self) -> AbstractToolset[AgentDepsT]:
        """Return the Notion MCP toolset."""
        id = self.id or 'notion'
        if self.client is not None:
            toolset: AbstractToolset[AgentDepsT] = per_run_client(self.client, self._from_client, id=id)
        else:
            toolset = per_run_auth(self.auth, self._connect, id=id)
        if self.read_only:
            return toolset.filtered(lambda _ctx, tool: is_read_only(tool))
        return toolset

    def _from_client(self, client: MCPToolsetClient) -> MCPToolset[AgentDepsT]:
        return MCPToolset(client, id=self.id or 'notion', include_instructions=self.include_instructions)

    def _connect(self, auth: MCPAuth | None) -> MCPToolset[AgentDepsT]:
        resolved = auth if auth is not None else environ.get('NOTION_ACCESS_TOKEN', 'oauth')
        return MCPToolset(
            'https://mcp.notion.com/mcp',
            id=self.id or 'notion',
            auth=OAuth(additional_client_metadata={'token_endpoint_auth_method': 'none'})
            if resolved == 'oauth'
            else resolved,
            headers=None,
            include_instructions=self.include_instructions,
        )
