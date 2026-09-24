"""Stripe hosted MCP capability."""

from __future__ import annotations

from dataclasses import dataclass, field
from os import environ

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.tools import AgentDepsT
from pydantic_ai.toolsets import AbstractToolset

from pydantic_ai_harness._mcp import MCPAuth, MCPAuthFunc, MCPClientFunc, per_run_auth, per_run_client

try:
    from pydantic_ai.mcp import MCPToolset, MCPToolsetClient
except ImportError as exc:  # pragma: no cover
    raise ImportError('Install Stripe support with: uv add "pydantic-ai-harness[stripe]"') from exc


@dataclass(kw_only=True)
class Stripe(AbstractCapability[AgentDepsT]):
    """Use Stripe's hosted tools with the permissions of the connected credential."""

    description: str | None = 'Read and change Stripe resources.'
    auth: MCPAuth | MCPAuthFunc[AgentDepsT] | None = field(default=None, repr=False)
    """Restricted API key, `'oauth'`, HTTP authentication, or a callable that returns one for each run.

    Unset, it defaults to `STRIPE_API_KEY`, then OAuth. A callable receives the run context, so each
    run can connect with its own user's credential from `ctx.deps`; returning `None` omits the tools.
    """
    include_instructions: bool = True
    """Forward the server's instructions to the agent."""
    client: MCPToolsetClient | MCPClientFunc[AgentDepsT] | None = field(default=None, repr=False)
    """Override the connection with a caller-configured MCP client or transport, or a callable that returns one for each run.

    The supplied client owns its URL, authentication, and server configuration.
    """
    connected_account: str | None = None
    """Stripe Connect account sent through the native `Stripe-Account` header."""

    def get_toolset(self) -> AbstractToolset[AgentDepsT]:
        """Build the Stripe connection."""
        id = self.id or 'stripe'
        if self.client is not None:
            return per_run_client(self.client, self._from_client, id=id)
        return per_run_auth(self.auth, self._connect, id=id)

    def _from_client(self, client: MCPToolsetClient) -> MCPToolset[AgentDepsT]:
        return MCPToolset(client, id=self.id or 'stripe', include_instructions=self.include_instructions)

    def _connect(self, auth: MCPAuth | None) -> MCPToolset[AgentDepsT]:
        return MCPToolset(
            'https://mcp.stripe.com',
            id=self.id or 'stripe',
            auth=auth if auth is not None else environ.get('STRIPE_API_KEY', 'oauth'),
            headers={'Stripe-Account': self.connected_account} if self.connected_account is not None else None,
            include_instructions=self.include_instructions,
        )
