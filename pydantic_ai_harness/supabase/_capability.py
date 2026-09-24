"""Supabase hosted MCP capability."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import UserError
from pydantic_ai.tools import AgentDepsT, RunContext
from pydantic_ai.toolsets import AbstractToolset, DynamicToolset

from pydantic_ai_harness._mcp import credential, is_read_only, one_connection

try:
    from pydantic_ai.mcp import MCPToolset, MCPToolsetClient
except ImportError as exc:  # pragma: no cover
    raise ImportError('Install Supabase support with: uv add "pydantic-ai-harness[supabase]"') from exc

from urllib.parse import urlencode

_ID = 'supabase'


@dataclass(kw_only=True)
class Supabase(AbstractCapability[AgentDepsT]):
    """Give the agent the tools of Supabase's hosted MCP server."""

    id: str | None = _ID
    """Names this capability in a run, so `defer_loading=True` needs no `id`. Give each `Supabase` on one agent its own."""
    description: str | None = 'Use Supabase project and account tools.'
    auth: str | Callable[[RunContext[AgentDepsT]], str | None] | None = field(default=None, repr=False)
    """A Supabase personal access token, `'oauth'` to sign in through the browser locally, or a function of the run context that returns a token.

    Unset, it uses `SUPABASE_ACCESS_TOKEN`. A function never does: if it returns `None` or `''`, that run has no
    Supabase tools.
    """
    read_only: bool = False
    """Turn on Supabase's read-only mode. With a custom `client`, keep only the tools the server marks as read-only."""
    include_instructions: bool = True
    """Forward the server's instructions to the agent."""
    client: MCPToolsetClient | None = field(default=None, repr=False)
    """Your own FastMCP client or transport, for full control of the connection.

    It cannot be combined with `auth`, `project_ref`, or `features`.
    """
    project_ref: str | None = None
    """The project to limit the agent to. Leave it out to keep the account-level tools."""
    features: list[str] | None = None
    """Supabase tool groups to enable. `None` keeps Supabase's defaults."""

    def __post_init__(self) -> None:
        if self.client is not None and (
            self.auth is not None or self.project_ref is not None or self.features is not None
        ):
            raise UserError(
                '`client` owns the connection, so it cannot be combined with `auth`, `project_ref`, or `features`.'
            )

    @classmethod
    def combine(cls, capabilities: Sequence[AbstractCapability[AgentDepsT]]) -> AbstractCapability[AgentDepsT]:
        """Two `Supabase`s under one `id` are the same connection stated twice, or an error if they differ."""
        return one_connection(capabilities)

    def get_toolset(self) -> AbstractToolset[AgentDepsT]:
        """Return the Supabase MCP toolset."""
        id = self.id or _ID
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
        if auth == 'oauth':
            # FastMCP reads 'oauth' as "log in through a browser", which would hang a server run.
            raise UserError("The `auth` function must return an API key or token, not 'oauth'.")
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
            id=self.id or _ID,
            auth=credential(auth, env='SUPABASE_ACCESS_TOKEN', service='Supabase'),
            headers=None,
            include_instructions=self.include_instructions,
        )
