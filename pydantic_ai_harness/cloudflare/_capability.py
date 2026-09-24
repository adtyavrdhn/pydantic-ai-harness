"""Cloudflare hosted MCP capability."""

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

    description: str | None = 'Use Cloudflare API, product, and documentation tools.'
    auth: MCPAuth | MCPAuthFunc[AgentDepsT] | None = field(default=None, repr=False)
    """A Cloudflare API token, an `httpx.Auth`, or a function of the run context that returns one.

    Unset, it uses `CLOUDFLARE_API_TOKEN`; public servers need neither. If the function returns `None`, that run
    has no Cloudflare tools.
    """
    read_only: bool = False
    """Keep only the tools the server marks as read-only."""
    include_instructions: bool = True
    """Forward the server's instructions to the agent."""
    client: MCPToolsetClient | MCPClientFunc[AgentDepsT] | None = field(default=None, repr=False)
    """Your own MCP client or transport, or a function that returns one for each run.

    It replaces `server` and `auth`.
    """
    server: CloudflareServer = CloudflareServer.DOCS
    """The server to use. Public ones, such as the documentation server, need no credential."""

    def get_toolset(self) -> AbstractToolset[AgentDepsT]:
        """Return the Cloudflare tools."""
        id = self.id or 'cloudflare'
        if self.client is not None:
            toolset: AbstractToolset[AgentDepsT] = per_run_client(self.client, self._from_client, id=id)
        else:
            toolset = per_run_auth(self.auth, self._connect, id=id)
        if self.read_only:
            return toolset.filtered(lambda _ctx, tool: is_read_only(tool))
        return toolset

    def _from_client(self, client: MCPToolsetClient) -> MCPToolset[AgentDepsT]:
        return MCPToolset(client, id=self.id or 'cloudflare', include_instructions=self.include_instructions)

    def _connect(self, auth: MCPAuth | None) -> MCPToolset[AgentDepsT]:
        if auth is None:
            auth = environ.get('CLOUDFLARE_API_TOKEN')
        if auth is None and self.server not in _PUBLIC_SERVERS:
            raise UserError('Set `CLOUDFLARE_API_TOKEN` or pass `auth` to connect to Cloudflare.')
        return MCPToolset(
            _URLS[self.server],
            id=self.id or 'cloudflare',
            auth=auth,
            headers=None,
            include_instructions=self.include_instructions,
        )
