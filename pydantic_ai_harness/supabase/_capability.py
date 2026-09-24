"""Supabase hosted MCP capability."""

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
    raise ImportError('Install Supabase support with: uv add "pydantic-ai-harness[supabase]"') from exc

from urllib.parse import urlencode


@dataclass(kw_only=True)
class Supabase(AbstractCapability[AgentDepsT]):
    """Connect to Supabase using its native project, feature, and read-only settings."""

    description: str | None = 'Use Supabase project and account tools.'
    auth: MCPAuth | MCPAuthFunc[AgentDepsT] | None = field(default=None, repr=False)
    """PAT, `'oauth'`, HTTP authentication, or a callable that returns one for each run.

    Unset, it defaults to `SUPABASE_ACCESS_TOKEN`, then OAuth. A callable receives the run context, so each
    run can connect with its own user's credential from `ctx.deps`; returning `None` omits the tools.
    """
    read_only: bool = False
    """Use the server's native read-only mode. A custom client is filtered by `readOnlyHint` instead."""
    include_instructions: bool = True
    """Forward the server's instructions to the agent."""
    client: MCPToolsetClient | MCPClientFunc[AgentDepsT] | None = field(default=None, repr=False)
    """Override the connection with a caller-configured MCP client or transport, or a callable that returns one for each run.

    The supplied client owns its URL, authentication, and server configuration.
    """
    project_ref: str | None = None
    """Native project selection. Omit to retain account-level tools."""
    features: list[str] | None = None
    """Native feature groups. `None` keeps the server defaults."""

    def get_toolset(self) -> AbstractToolset[AgentDepsT]:
        """Build the Supabase connection and optional read-only selection."""
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
            auth=auth if auth is not None else environ.get('SUPABASE_ACCESS_TOKEN', 'oauth'),
            headers=None,
            include_instructions=self.include_instructions,
        )
