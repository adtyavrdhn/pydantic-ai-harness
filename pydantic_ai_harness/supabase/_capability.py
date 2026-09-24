"""Supabase hosted MCP capability."""

from __future__ import annotations

from dataclasses import dataclass, field
from os import environ

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import UserError
from pydantic_ai.tools import AgentDepsT
from pydantic_ai.toolsets import AbstractToolset

from pydantic_ai_harness._mcp import MCPAuth, MCPAuthFunc, MCPClientFunc, is_read_only, per_run_auth, per_run_client

try:
    from pydantic_ai.mcp import MCPToolset, MCPToolsetClient
except ImportError as exc:  # pragma: no cover
    raise ImportError('Install Supabase support with: uv add "pydantic-ai-harness[supabase]"') from exc

from urllib.parse import urlencode


@dataclass(kw_only=True)
class Supabase(AbstractCapability[AgentDepsT]):
    """Give the agent the tools of Supabase's hosted MCP server."""

    description: str | None = 'Use Supabase project and account tools.'
    auth: MCPAuth | MCPAuthFunc[AgentDepsT] | None = field(default=None, repr=False)
    """A Supabase personal access token, an `httpx.Auth`, or a function of the run context that returns one.

    Unset, it uses `SUPABASE_ACCESS_TOKEN`. If the function returns `None`, that run has no Supabase tools.
    """
    read_only: bool = False
    """Turn on Supabase's read-only mode. With a custom `client`, keep only the tools the server marks as read-only."""
    include_instructions: bool = True
    """Forward the server's instructions to the agent."""
    client: MCPToolsetClient | MCPClientFunc[AgentDepsT] | None = field(default=None, repr=False)
    """Your own FastMCP client or transport, or a function of the run context that returns one.

    The client owns the URL, authentication, and server settings.
    """
    project_ref: str | None = None
    """The project to limit the agent to. Leave it out to keep the account-level tools."""
    features: list[str] | None = None
    """Supabase tool groups to enable. `None` keeps Supabase's defaults."""

    def get_toolset(self) -> AbstractToolset[AgentDepsT]:
        """Return the Supabase MCP toolset."""
        id = self.id or 'supabase'
        if self.client is not None:
            toolset: AbstractToolset[AgentDepsT] = per_run_client(self.client, self._from_client, id=id)
        else:
            toolset = per_run_auth(self.auth, self._connect, id=id)
        if self.read_only and self.client is not None:
            return toolset.filtered(lambda _ctx, tool: is_read_only(tool))
        return toolset

    def _from_client(self, client: MCPToolsetClient) -> MCPToolset[AgentDepsT]:
        return MCPToolset(client, id=self.id or 'supabase', include_instructions=self.include_instructions)

    def _connect(self, auth: MCPAuth | None) -> MCPToolset[AgentDepsT]:
        if auth is None:
            auth = environ.get('SUPABASE_ACCESS_TOKEN')
        if auth is None:
            raise UserError('Set `SUPABASE_ACCESS_TOKEN` or pass `auth` to connect to Supabase.')
        query: dict[str, str] = {}
        if self.project_ref is not None:
            query['project_ref'] = self.project_ref
        if self.features is not None:
            query['features'] = ','.join(self.features)
        if self.read_only:
            query['read_only'] = 'true'
        return MCPToolset(
            'https://mcp.supabase.com/mcp' + ('?' + urlencode(query) if query else ''),
            id=self.id or 'supabase',
            auth=auth,
            headers=None,
            include_instructions=self.include_instructions,
        )
