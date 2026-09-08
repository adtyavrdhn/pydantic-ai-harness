"""Stripe hosted MCP: `https://mcp.stripe.com`, verified 2026-09-08 against https://docs.stripe.com/mcp."""

from __future__ import annotations

from dataclasses import dataclass, field
from os import environ
from typing import Literal

from httpx import Auth
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import UserError
from pydantic_ai.tools import AgentDepsT
from pydantic_ai.toolsets import AbstractToolset

try:
    from pydantic_ai.mcp import MCPToolset
except ImportError as _import_error:  # pragma: no cover
    raise ImportError(
        'MCP support is required for the Stripe capability. Install it with: uv add "pydantic-ai-harness[stripe]"'
    ) from _import_error

_STRIPE_MCP_URL = 'https://mcp.stripe.com'
_DEFAULT_DESCRIPTION = 'Read and change Stripe data through the Stripe API.'

_READ_TOOL_NAMES = frozenset(
    {
        'explain_metric',
        'get_balance_summary',
        'get_stripe_account_info',
        'list_metrics',
        'metric_drilldown',
        'search_stripe_documentation',
        'stripe_api_details',
        'stripe_api_read',
        'stripe_api_search',
    }
)
"""The tools Stripe's published table describes as reads, checked 2026-09-08.

Stripe's endpoint needs a credential to answer `tools/list`, so `read_only` filters on these names
rather than the server's own annotations, and hides every other tool, including any Stripe adds later.
"""


@dataclass(kw_only=True)
class Stripe(AbstractCapability[AgentDepsT]):
    """Connect an agent to Stripe's hosted MCP server.

    The default serves Stripe's write tools alongside its read tools; the credential's permissions
    decide what the agent can read or change.
    """

    description: str | None = _DEFAULT_DESCRIPTION
    """Routing description used when the capability is loaded on demand."""

    auth: Auth | Literal['oauth'] | str | None = field(default=None, repr=False)
    """`'oauth'` for browser login, a Stripe API key, or a custom `httpx.Auth`.

    Defaults to `$STRIPE_API_KEY`.
    """

    connected_account: str | None = None
    """A Connect account (`acct_...`) sent as `Stripe-Account`, so every call acts on that account."""

    read_only: bool = False
    """Expose only the tools Stripe documents as reads, and hide every other tool it serves."""

    def get_toolset(self) -> AbstractToolset[AgentDepsT]:
        """Build the Stripe MCP connection."""
        auth = self.auth or environ.get('STRIPE_API_KEY')
        if not auth:
            raise UserError('Stripe needs an API key: pass auth= or set STRIPE_API_KEY.')
        headers = {'Stripe-Account': self.connected_account} if self.connected_account else None
        toolset: AbstractToolset[AgentDepsT] = MCPToolset(
            _STRIPE_MCP_URL, id=self.id or 'stripe', auth=auth, headers=headers, include_instructions=True
        )
        if self.read_only:
            return toolset.filtered(lambda _ctx, tool_def: tool_def.name in _READ_TOOL_NAMES)
        return toolset

    @classmethod
    def from_spec(
        cls,
        *,
        id: str | None = None,
        description: str | None = _DEFAULT_DESCRIPTION,
        defer_loading: bool = False,
        connected_account: str | None = None,
        read_only: bool = False,
    ) -> Stripe[AgentDepsT]:
        """Construct a Stripe capability from serializable options.

        `auth` is absent by design, so a spec file cannot carry a Stripe API key: the credential
        comes from `$STRIPE_API_KEY`.
        """
        return cls(
            id=id,
            description=description,
            defer_loading=defer_loading,
            connected_account=connected_account,
            read_only=read_only,
        )

    @classmethod
    def get_serialization_name(cls) -> str:
        """Return the agent-spec capability name."""
        return 'Stripe'
