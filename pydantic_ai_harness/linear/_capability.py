"""Linear hosted MCP capability."""

from __future__ import annotations

from dataclasses import dataclass, field
from os import environ

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.tools import AgentDepsT
from pydantic_ai.toolsets import AbstractToolset

from pydantic_ai_harness._mcp import MCPAuth, MCPAuthFunc, MCPClientFunc, is_read_only, per_run_auth, per_run_client

try:
    from pydantic_ai.mcp import MCPToolset, MCPToolsetClient
except ImportError as exc:  # pragma: no cover
    raise ImportError('Install Linear support with: uv add "pydantic-ai-harness[linear]"') from exc


@dataclass(kw_only=True)
class Linear(AbstractCapability[AgentDepsT]):
    """Use Linear's hosted tools with the permissions of the connected user."""

    description: str | None = 'Use Linear issues, projects, and teams.'
    auth: MCPAuth | MCPAuthFunc[AgentDepsT] | None = field(default=None, repr=False)
    """API key, OAuth token, `'oauth'`, HTTP authentication, or a callable that returns one for each run.

    Unset, it defaults to `LINEAR_ACCESS_TOKEN`, then OAuth. A callable receives the run context, so each
    run can connect with its own user's credential from `ctx.deps`; returning `None` omits the tools.
    """
    read_only: bool = False
    """Use Linear's read-only endpoint. A custom client is filtered by `readOnlyHint` instead."""
    include_instructions: bool = True
    """Forward the server's instructions to the agent."""
    client: MCPToolsetClient | MCPClientFunc[AgentDepsT] | None = field(default=None, repr=False)
    """Override the connection with a caller-configured MCP client or transport, or a callable that returns one for each run.

    The supplied client owns its URL, authentication, and server configuration.
    """

    def get_toolset(self) -> AbstractToolset[AgentDepsT]:
        """Build the Linear connection and optional read-only selection."""
        id = self.id or 'linear'
        if self.client is not None:
            toolset: AbstractToolset[AgentDepsT] = per_run_client(self.client, self._from_client, id=id)
            if self.read_only:
                return toolset.filtered(lambda _ctx, tool: is_read_only(tool))
            return toolset
        return per_run_auth(self.auth, self._connect, id=id)

    def _from_client(self, client: MCPToolsetClient) -> MCPToolset[AgentDepsT]:
        return MCPToolset(client, id=self.id or 'linear', include_instructions=self.include_instructions)

    def _connect(self, auth: MCPAuth | None) -> MCPToolset[AgentDepsT]:
        return MCPToolset(
            'https://mcp.linear.app/mcp/readonly' if self.read_only else 'https://mcp.linear.app/mcp',
            id=self.id or 'linear',
            auth=auth if auth is not None else environ.get('LINEAR_ACCESS_TOKEN', 'oauth'),
            headers=None,
            include_instructions=self.include_instructions,
        )
