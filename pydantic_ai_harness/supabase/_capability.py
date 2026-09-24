"""Supabase hosted MCP capability."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.tools import AgentDepsT, RunContext
from pydantic_ai.toolsets import AbstractToolset, DynamicToolset

from pydantic_ai_harness._mcp import credential, is_read_only

try:
    from pydantic_ai.mcp import MCPToolset, MCPToolsetClient
except ImportError as exc:  # pragma: no cover
    raise ImportError('Install Supabase support with: uv add "pydantic-ai-harness[supabase]"') from exc

from urllib.parse import urlencode


@dataclass(kw_only=True)
class Supabase(AbstractCapability[AgentDepsT]):
    """Give the agent the tools of Supabase's hosted MCP server."""

    description: str | None = 'Use Supabase project and account tools.'
    auth: str | Callable[[RunContext[AgentDepsT]], str | None] | None = field(default=None, repr=False)
    """A Supabase personal access token or a function of the run context that returns one.

    Unset, it uses `SUPABASE_ACCESS_TOKEN`. A function never does: if it returns `None` or `''`, that run has no
    Supabase tools.
    """
    read_only: bool = False
    """Turn on Supabase's read-only mode. With a custom `client`, keep only the tools the server marks as read-only."""
    include_instructions: bool = True
    """Forward the server's instructions to the agent."""
    client: MCPToolsetClient | None = field(default=None, repr=False)
    """Your own FastMCP client or transport, which then owns the URL, authentication, and server settings."""
    project_ref: str | None = None
    """The project to limit the agent to. Leave it out to keep the account-level tools."""
    features: list[str] | None = None
    """Supabase tool groups to enable. `None` keeps Supabase's defaults."""

    def get_toolset(self) -> AbstractToolset[AgentDepsT]:
        """Return the Supabase MCP toolset."""
        id = self.id or 'supabase'
        if self.client is not None:
            toolset: AbstractToolset[AgentDepsT] = MCPToolset(
                self.client, id=id, include_instructions=self.include_instructions
            )
            if self.read_only:
                return toolset.filtered(lambda _ctx, tool: is_read_only(tool))
            return toolset
        if callable(self.auth):
            return DynamicToolset(self._connect_for_run, per_run_step=False, id=id)
        return self._connect(self.auth)

    def _connect_for_run(self, ctx: RunContext[AgentDepsT]) -> MCPToolset[AgentDepsT] | None:
        auth = self.auth(ctx) if callable(self.auth) else self.auth
        return self._connect(auth) if auth else None

    def _connect(self, auth: str | None) -> MCPToolset[AgentDepsT]:
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
            auth=credential(auth, env='SUPABASE_ACCESS_TOKEN', service='Supabase'),
            headers=None,
            include_instructions=self.include_instructions,
        )
