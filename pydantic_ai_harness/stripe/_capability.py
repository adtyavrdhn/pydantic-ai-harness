"""Stripe hosted MCP capability."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.tools import AgentDepsT, RunContext
from pydantic_ai.toolsets import AbstractToolset, DynamicToolset

from pydantic_ai_harness._mcp import credential

try:
    from pydantic_ai.mcp import MCPToolset, MCPToolsetClient
except ImportError as exc:  # pragma: no cover
    raise ImportError('Install Stripe support with: uv add "pydantic-ai-harness[stripe]"') from exc


@dataclass(kw_only=True)
class Stripe(AbstractCapability[AgentDepsT]):
    """Give the agent the tools of Stripe's hosted MCP server, with the permissions of the connected credential."""

    description: str | None = 'Read and change Stripe resources.'
    auth: str | Callable[[RunContext[AgentDepsT]], str | None] | None = field(default=None, repr=False)
    """A Stripe restricted API key or a function of the run context that returns one.

    Unset, it uses `STRIPE_API_KEY`. If the function returns `None`, that run has no Stripe tools.
    """
    include_instructions: bool = True
    """Forward the server's instructions to the agent."""
    client: MCPToolsetClient | None = field(default=None, repr=False)
    """Your own MCP client or transport, which then owns the URL and authentication."""
    connected_account: str | None = None
    """The Stripe Connect account to act on, such as `'acct_...'`. Not used with a custom `client`."""

    def get_toolset(self) -> AbstractToolset[AgentDepsT]:
        """Return the Stripe MCP toolset."""
        id = self.id or 'stripe'
        if self.client is not None:
            return MCPToolset(self.client, id=id, include_instructions=self.include_instructions)
        if callable(self.auth):
            # Registered once under a fixed `id`, as durable execution requires; filled per run.
            return DynamicToolset(self._connect_for_run, per_run_step=False, id=id)
        return self._connect(self.auth)

    def _connect_for_run(self, ctx: RunContext[AgentDepsT]) -> MCPToolset[AgentDepsT] | None:
        auth = self.auth(ctx) if callable(self.auth) else self.auth
        return None if auth is None else self._connect(auth)

    def _connect(self, auth: str | None) -> MCPToolset[AgentDepsT]:
        return MCPToolset(
            'https://mcp.stripe.com',
            id=self.id or 'stripe',
            auth=credential(auth, env='STRIPE_API_KEY', service='Stripe'),
            headers={'Stripe-Account': self.connected_account} if self.connected_account is not None else None,
            include_instructions=self.include_instructions,
        )
