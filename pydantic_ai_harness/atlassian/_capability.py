"""Atlassian hosted MCP capability."""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.tools import AgentDepsT
from pydantic_ai.toolsets import AbstractToolset

from pydantic_ai_harness._mcp import MCPAuth, MCPAuthFunc, credential, per_run

try:
    from pydantic_ai.mcp import MCPToolset, MCPToolsetClient
except ImportError as exc:  # pragma: no cover
    raise ImportError('Install Atlassian support with: uv add "pydantic-ai-harness[atlassian]"') from exc


@dataclass(kw_only=True)
class Atlassian(AbstractCapability[AgentDepsT]):
    """Give an agent the tools of Atlassian's hosted MCP server, with the permissions of the connected user."""

    description: str | None = 'Use Jira, Confluence, and other Atlassian tools.'
    auth: MCPAuth | MCPAuthFunc[AgentDepsT] | None = field(default=None, repr=False)
    """An Atlassian API key or token, an `httpx.Auth`, or a function of the run context that returns one.

    Unset, it uses `ATLASSIAN_API_KEY`. If the function returns `None`, that run has no Atlassian tools.
    """
    include_instructions: bool = True
    """Pass the server's own instructions to the agent."""
    client: MCPToolsetClient | None = field(default=None, repr=False)
    """Your own MCP client or transport, which then owns the URL, authentication, and server settings."""

    def get_toolset(self) -> AbstractToolset[AgentDepsT]:
        """Return the Atlassian tools."""
        id = self.id or 'atlassian'
        if self.client is not None:
            return MCPToolset(self.client, id=id, include_instructions=self.include_instructions)
        return per_run(self.auth, self._connect, id=id)

    def _connect(self, auth: MCPAuth | None) -> MCPToolset[AgentDepsT]:
        return MCPToolset(
            'https://mcp.atlassian.com/v2/mcp?tools=all',
            id=self.id or 'atlassian',
            auth=credential(auth, env='ATLASSIAN_API_KEY', service='Atlassian'),
            headers=None,
            include_instructions=self.include_instructions,
        )
