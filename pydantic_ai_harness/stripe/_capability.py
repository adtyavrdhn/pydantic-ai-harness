"""Stripe hosted MCP capability."""

from __future__ import annotations

from dataclasses import dataclass, field
from os import environ

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import UserError
from pydantic_ai.tools import AgentDepsT
from pydantic_ai.toolsets import AbstractToolset

from pydantic_ai_harness._mcp import MCPAuth, MCPAuthFunc, MCPClientFunc, per_run_auth, per_run_client

try:
    from pydantic_ai.mcp import MCPToolset, MCPToolsetClient
except ImportError as exc:  # pragma: no cover
    raise ImportError('Install Stripe support with: uv add "pydantic-ai-harness[stripe]"') from exc


@dataclass(kw_only=True)
class Stripe(AbstractCapability[AgentDepsT]):
    """Give the agent the tools of Stripe's hosted MCP server, with the permissions of the connected credential."""

    description: str | None = 'Read and change Stripe resources.'
    auth: MCPAuth | MCPAuthFunc[AgentDepsT] | None = field(default=None, repr=False)
    """A Stripe restricted API key, an `httpx.Auth`, or a function of the run context that returns one.

    Unset, it uses `STRIPE_API_KEY`. If the function returns `None`, that run has no Stripe tools.
    """
    include_instructions: bool = True
    """Forward the server's instructions to the agent."""
    client: MCPToolsetClient | MCPClientFunc[AgentDepsT] | None = field(default=None, repr=False)
    """Your own FastMCP client or transport, or a function of the run context that returns one.

    The client owns the URL, authentication, and server settings.
    """
    connected_account: str | None = None
    """The Stripe Connect account to act on, such as `'acct_...'`. Not used with a custom `client`."""

    def get_toolset(self) -> AbstractToolset[AgentDepsT]:
        """Return the Stripe MCP toolset."""
        id = self.id or 'stripe'
        if self.client is not None:
            return per_run_client(self.client, self._from_client, id=id)
        return per_run_auth(self.auth, self._connect, id=id)

    def _from_client(self, client: MCPToolsetClient) -> MCPToolset[AgentDepsT]:
        return MCPToolset(client, id=self.id or 'stripe', include_instructions=self.include_instructions)

    def _connect(self, auth: MCPAuth | None) -> MCPToolset[AgentDepsT]:
        if auth is None:
            auth = environ.get('STRIPE_API_KEY')
        if auth is None:
            raise UserError('Set `STRIPE_API_KEY` or pass `auth` to connect to Stripe.')
        return MCPToolset(
            'https://mcp.stripe.com',
            id=self.id or 'stripe',
            auth=auth,
            headers={'Stripe-Account': self.connected_account} if self.connected_account is not None else None,
            include_instructions=self.include_instructions,
        )
