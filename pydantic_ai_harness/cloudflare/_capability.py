"""Cloudflare hosted MCP capability."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from os import environ

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import UserError
from pydantic_ai.tools import AgentDepsT, RunContext
from pydantic_ai.toolsets import AbstractToolset, DynamicToolset

from pydantic_ai_harness._mcp import credential, is_read_only

try:
    from pydantic_ai.mcp import MCPToolset, MCPToolsetClient
except ImportError as exc:  # pragma: no cover
    raise ImportError('Install Cloudflare support with: uv add "pydantic-ai-harness[cloudflare]"') from exc

from enum import Enum


class CloudflareServer(str, Enum):
    """Which of Cloudflare's hosted MCP servers to use."""

    API = 'api'
    DOCS = 'docs'
    AGENTS_SDK_DOCS = 'agents_sdk_docs'
    WORKERS_BINDINGS = 'workers_bindings'
    WORKERS_BUILDS = 'workers_builds'
    OBSERVABILITY = 'observability'
    CONTAINERS = 'containers'
    BROWSER = 'browser'
    LOGPUSH = 'logpush'
    AI_GATEWAY = 'ai_gateway'
    AUDIT_LOGS = 'audit_logs'
    DNS_ANALYTICS = 'dns_analytics'
    DEX = 'dex'
    CASB = 'casb'
    DEVELOPER_STACK = 'developer_stack'
    BLOG = 'blog'
    DEMO_DAY = 'demo_day'


_URLS: dict[CloudflareServer, str] = {
    CloudflareServer.API: 'https://mcp.cloudflare.com/mcp',
    CloudflareServer.DOCS: 'https://docs.mcp.cloudflare.com/mcp',
    CloudflareServer.AGENTS_SDK_DOCS: 'https://agents.cloudflare.com/mcp',
    CloudflareServer.WORKERS_BINDINGS: 'https://bindings.mcp.cloudflare.com/mcp',
    CloudflareServer.WORKERS_BUILDS: 'https://builds.mcp.cloudflare.com/mcp',
    CloudflareServer.OBSERVABILITY: 'https://observability.mcp.cloudflare.com/mcp',
    CloudflareServer.CONTAINERS: 'https://containers.mcp.cloudflare.com/mcp',
    CloudflareServer.BROWSER: 'https://browser.mcp.cloudflare.com/mcp',
    CloudflareServer.LOGPUSH: 'https://logs.mcp.cloudflare.com/mcp',
    CloudflareServer.AI_GATEWAY: 'https://ai-gateway.mcp.cloudflare.com/mcp',
    CloudflareServer.AUDIT_LOGS: 'https://auditlogs.mcp.cloudflare.com/mcp',
    CloudflareServer.DNS_ANALYTICS: 'https://dns-analytics.mcp.cloudflare.com/mcp',
    CloudflareServer.DEX: 'https://dex.mcp.cloudflare.com/mcp',
    CloudflareServer.CASB: 'https://casb.mcp.cloudflare.com/mcp',
    CloudflareServer.DEVELOPER_STACK: 'https://stack.mcp.cloudflare.com/mcp',
    CloudflareServer.BLOG: 'https://blog.mcp.cloudflare.com/mcp',
    CloudflareServer.DEMO_DAY: 'https://demo-day.mcp.cloudflare.com/mcp',
}
_PUBLIC_SERVERS = frozenset(
    {
        CloudflareServer.DOCS,
        CloudflareServer.AGENTS_SDK_DOCS,
        CloudflareServer.DEVELOPER_STACK,
        CloudflareServer.BLOG,
        CloudflareServer.DEMO_DAY,
    }
)


@dataclass(kw_only=True)
class Cloudflare(AbstractCapability[AgentDepsT]):
    """Use a Cloudflare hosted MCP server with the permissions of the connected credential."""

    id: str | None = None
    """Stable capability and toolset ID, derived from `server` when not given.

    One server is one set of tools, so the server is what identifies this capability -- the same way an MCP server
    is identified by its URL. Deriving it rather than fixing it to `'cloudflare'` is what lets one agent use two
    servers: their ids differ, so they stay two capabilities. Two for the same server are a mistake, and collide.
    """
    description: str | None = 'Use Cloudflare API, product, and documentation tools.'
    auth: str | Callable[[RunContext[AgentDepsT]], str | None] | None = field(default=None, repr=False)
    """A Cloudflare API token, `'oauth'` to sign in through the browser locally, or a function of the run context that returns a token.

    Unset, it uses `CLOUDFLARE_API_TOKEN`. A function never does: if it returns `None` or `''`, that run has no
    Cloudflare tools. Public servers connect without a credential when neither is set.
    """
    read_only: bool = False
    """Keep only the tools the server marks as read-only."""
    include_instructions: bool = True
    """Forward the server's instructions to the agent."""
    client: MCPToolsetClient | None = field(default=None, repr=False)
    """Your own MCP client or transport, for full control of the connection. It cannot be combined with `auth` or `server`."""
    server: CloudflareServer = CloudflareServer.DOCS
    """The server to use. Public ones, such as the documentation server, need no credential."""

    def __post_init__(self) -> None:
        if self.client is not None and (self.auth is not None or self.server != CloudflareServer.DOCS):
            raise UserError('`client` owns the connection, so it cannot be combined with `auth` or `server`.')
        self.id = self._derived_id()

    def get_toolset(self) -> AbstractToolset[AgentDepsT]:
        """Return the Cloudflare tools."""
        id = self._derived_id()
        if self.client is not None:
            toolset: AbstractToolset[AgentDepsT] = MCPToolset(
                self.client, id=id, include_instructions=self.include_instructions
            )
        elif callable(self.auth):
            # Registered once under a fixed `id`, as durable execution requires; filled per run.
            toolset = DynamicToolset(self._connect_for_run, per_run_step=False, id=id)
        else:
            toolset = self._connect(self.auth)
        if self.read_only:
            return toolset.filtered(lambda _ctx, tool: is_read_only(tool))
        return toolset

    def _derived_id(self) -> str:
        """This capability's `id`, falling back to the one the server names."""
        return self.id if self.id is not None else f'cloudflare-{self.server.value}'

    def _connect_for_run(self, ctx: RunContext[AgentDepsT]) -> MCPToolset[AgentDepsT] | None:
        auth = self.auth(ctx) if callable(self.auth) else self.auth
        if auth == 'oauth':
            # FastMCP reads 'oauth' as "log in through a browser", which would hang a server run.
            raise UserError("The `auth` function must return an API key or token, not 'oauth'.")
        return self._connect(auth) if auth else None

    def _connect(self, auth: str | None) -> MCPToolset[AgentDepsT]:
        # Public servers need no credential, but still receive one when it is set.
        if not auth and self.server in _PUBLIC_SERVERS and not environ.get('CLOUDFLARE_API_TOKEN'):
            connect_auth = None
        else:
            connect_auth = credential(auth, env='CLOUDFLARE_API_TOKEN', service='Cloudflare')
        return MCPToolset(
            _URLS[self.server],
            id=self._derived_id(),
            auth=connect_auth,
            headers=None,
            include_instructions=self.include_instructions,
        )
