"""Atlassian hosted MCP capability."""

from __future__ import annotations

from dataclasses import dataclass, field
from os import environ

from httpx import Auth
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.tools import AgentDepsT
from pydantic_ai.toolsets import AbstractToolset

from pydantic_ai_harness._mcp import is_read_only

try:
    from pydantic_ai.mcp import MCPToolset, MCPToolsetClient
except ImportError as exc:  # pragma: no cover
    raise ImportError('Install Atlassian support with: uv add "pydantic-ai-harness[atlassian]"') from exc


@dataclass(kw_only=True)
class Atlassian(AbstractCapability[AgentDepsT]):
    """Use Atlassian's hosted tools with the permissions of the connected user."""

    description: str | None = 'Use Jira, Confluence, and other Atlassian tools.'
    auth: Auth | str | None = field(default=None, repr=False)
    """Bearer token, `'oauth'`, or HTTP authentication. Defaults to `ATLASSIAN_API_KEY`, then OAuth."""
    read_only: bool = False
    """Expose only tools the server marks read-only; unmarked tools are omitted."""
    include_instructions: bool = True
    """Forward the server's instructions to the agent."""
    client: MCPToolsetClient | None = field(default=None, repr=False)
    """Override the connection with a caller-configured MCP client or transport.

    The supplied client owns its URL, authentication, and server configuration.
    """

    def get_toolset(self) -> AbstractToolset[AgentDepsT]:
        """Build the Atlassian connection and optional read-only selection."""
        if self.client is not None:
            toolset: AbstractToolset[AgentDepsT] = MCPToolset(
                self.client, id=self.id or 'atlassian', include_instructions=self.include_instructions
            )
        else:
            toolset = MCPToolset(
                'https://mcp.atlassian.com/v2/mcp?tools=all',
                id=self.id or 'atlassian',
                auth=self.auth if self.auth is not None else environ.get('ATLASSIAN_API_KEY', 'oauth'),
                headers=None,
                include_instructions=self.include_instructions,
            )
        if self.read_only:
            return toolset.filtered(lambda _ctx, tool: is_read_only(tool))
        return toolset
