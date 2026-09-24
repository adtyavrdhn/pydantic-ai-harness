"""Stripe hosted MCP capability."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import UserError
from pydantic_ai.tools import AgentDepsT, RunContext
from pydantic_ai.toolsets import AbstractToolset, DynamicToolset

from pydantic_ai_harness._mcp import credential, one_connection

try:
    from pydantic_ai.mcp import MCPToolset, MCPToolsetClient
except ImportError as exc:  # pragma: no cover
    raise ImportError('Install Stripe support with: uv add "pydantic-ai-harness[stripe]"') from exc


_ID = 'stripe'


@dataclass(kw_only=True)
class Stripe(AbstractCapability[AgentDepsT]):
    """Give the agent the tools of Stripe's hosted MCP server, with the permissions of the connected credential."""

    id: str | None = _ID
    """Names this capability in a run, so `defer_loading=True` needs no `id`. Give each `Stripe` on one agent its own."""
    description: str | None = 'Read and change Stripe resources.'
    auth: str | Callable[[RunContext[AgentDepsT]], str | None] | None = field(default=None, repr=False)
    """A Stripe restricted API key, `'oauth'` to sign in through the browser locally, or a function of the run context that returns a key.

    Unset, it uses `STRIPE_API_KEY`. A function never does: if it returns `None` or `''`, that run has no Stripe tools.
    """
    include_instructions: bool = True
    """Forward the server's instructions to the agent."""
    client: MCPToolsetClient | None = field(default=None, repr=False)
    """Your own MCP client or transport, for full control of the connection.

    It cannot be combined with `auth` or `connected_account`.
    """
    connected_account: str | None = None
    """The Stripe Connect account to act on, such as `'acct_...'`."""

    def __post_init__(self) -> None:
        if self.client is not None and (self.auth is not None or self.connected_account is not None):
            raise UserError(
                '`client` owns the connection, so it cannot be combined with `auth` or `connected_account`.'
            )

    @classmethod
    def combine(cls, capabilities: Sequence[AbstractCapability[AgentDepsT]]) -> AbstractCapability[AgentDepsT]:
        """Two `Stripe`s under one `id` are the same connection stated twice, or an error if they differ."""
        return one_connection(capabilities)

    def get_toolset(self) -> AbstractToolset[AgentDepsT]:
        """Return the Stripe MCP toolset."""
        id = self.id or _ID
        if self.client is not None:
            return MCPToolset(self.client, id=id, include_instructions=self.include_instructions)
        if callable(self.auth):
            # Registered once under a fixed `id`, as durable execution requires; filled per run.
            return DynamicToolset(self._connect_for_run, per_run_step=False, id=id)
        return self._connect(self.auth)

    def _connect_for_run(self, ctx: RunContext[AgentDepsT]) -> MCPToolset[AgentDepsT] | None:
        auth = self.auth(ctx) if callable(self.auth) else self.auth
        if auth == 'oauth':
            # FastMCP reads 'oauth' as "log in through a browser", which would hang a server run.
            raise UserError("The `auth` function must return an API key or token, not 'oauth'.")
        return self._connect(auth) if auth else None

    def _connect(self, auth: str | None) -> MCPToolset[AgentDepsT]:
        return MCPToolset(
            'https://mcp.stripe.com',
            id=self.id or _ID,
            auth=credential(auth, env='STRIPE_API_KEY', service='Stripe'),
            headers={'Stripe-Account': self.connected_account} if self.connected_account is not None else None,
            include_instructions=self.include_instructions,
        )
