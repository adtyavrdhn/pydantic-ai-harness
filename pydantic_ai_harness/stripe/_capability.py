"""Stripe hosted MCP capability.

Wire contract, verified 2026-09-04:

- `https://mcp.stripe.com` serves MCP over HTTP and accepts a restricted API key as a bearer token.
- Restricted keys use `rk_test_` for sandboxes and `rk_live_` for live mode. Objects do not cross modes.
- `Stripe-Account: acct_...` scopes every MCP call to one connected account; connected-account MCP does not support
  OAuth.
- `stripe_api_read` performs supported `GET` methods. `stripe_api_write` performs supported `POST`, `PATCH`, `PUT`,
  and `DELETE` methods. Stripe recommends human confirmation for MCP tools.

Sources: https://docs.stripe.com/mcp and https://docs.stripe.com/keys. Re-check the endpoint, authentication,
connected-account section, tool table, and key prefixes before changing this boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlsplit

from pydantic import AnyUrl
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import UserError
from pydantic_ai.tools import AgentDepsT
from pydantic_ai.toolsets import AbstractToolset

try:
    from pydantic_ai.mcp import MCPToolset, MCPToolsetClient
except ImportError as _import_error:  # pragma: no cover
    raise ImportError(
        'MCP support is required for the Stripe capability. Install it with: uv add "pydantic-ai-slim[mcp]"'
    ) from _import_error

StripeMode = Literal['sandbox', 'live']

_STRIPE_MCP_URL = 'https://mcp.stripe.com'
_READ_TOOL_NAMES = frozenset(
    {
        'get_stripe_account_info',
        'search_stripe_documentation',
        'stripe_api_details',
        'stripe_api_read',
        'stripe_api_search',
    }
)
_WRITE_TOOL_NAME = 'stripe_api_write'
_DEFAULT_INSTRUCTIONS = (
    'Use the Stripe tools for account data and Stripe API guidance. This connection is read-only. '
    'Use `stripe_api_search` and `stripe_api_details` before `stripe_api_read` when the API method is unclear.'
)
_WRITE_INSTRUCTIONS = (
    'Use the Stripe tools for account data and Stripe API guidance. Read before writing. '
    '`stripe_api_write` requires approval for every call; request it only after the user clearly specifies the change.'
)


def _validate_https_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme.lower() != 'https' or parsed.hostname is None:
        raise UserError('`client` URL values must be absolute HTTPS URLs.')


def _validate_api_key(api_key: str, mode: StripeMode) -> None:
    if mode not in ('sandbox', 'live'):
        raise UserError('`mode` must be `sandbox` or `live`.')
    expected_prefix = 'rk_test_' if mode == 'sandbox' else 'rk_live_'
    if not api_key.startswith(('rk_test_', 'rk_live_')):
        raise UserError('Stripe MCP requires a restricted API key beginning with `rk_test_` or `rk_live_`.')
    if not api_key.startswith(expected_prefix):
        raise UserError(f'The Stripe API key does not match `mode={mode!r}`.')


def _validate_connected_account(connected_account: str | None) -> None:
    if connected_account is None:
        return
    suffix = connected_account.removeprefix('acct_')
    if not connected_account.startswith('acct_') or not suffix or not suffix.isascii() or not suffix.isalnum():
        raise UserError('`connected_account` must be a Stripe account ID beginning with `acct_`.')


@dataclass
class Stripe(AbstractCapability[AgentDepsT]):
    """Account-scoped Stripe API tools through Stripe's hosted MCP server.

    The default exposes only Stripe's read, API-discovery, account-information, and documentation tools. Set
    `enable_writes=True` to also expose `stripe_api_write`; every write call then uses Pydantic AI's tool approval
    flow. The API key must be restricted and must match `mode`.

    Args:
        api_key: Caller-owned Stripe restricted API key.
        mode: `sandbox` for `rk_test_` keys or `live` for `rk_live_` keys.
        connected_account: Optional `acct_...` Connect account applied to every request.
        enable_writes: Expose `stripe_api_write`, with approval required for every call.
        include_instructions: Add concise Stripe tool guidance to the agent.
        client: Replacement MCP client accepted by `MCPToolset`. URL values receive the configured authentication
            and connected-account headers. Non-URL clients own their transport, authentication, and account scope.
    """

    api_key: str = field(repr=False)
    mode: StripeMode = 'sandbox'
    connected_account: str | None = field(default=None, repr=False)
    enable_writes: bool = False
    include_instructions: bool = True
    client: MCPToolsetClient | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        _validate_api_key(self.api_key, self.mode)
        _validate_connected_account(self.connected_account)

    def get_instructions(self) -> str | None:
        """Return account-safe usage guidance without embedding the key or account ID."""
        if not self.include_instructions:
            return None
        return _WRITE_INSTRUCTIONS if self.enable_writes else _DEFAULT_INSTRUCTIONS

    def get_toolset(self) -> AbstractToolset[AgentDepsT]:
        """Build a filtered Stripe MCP toolset and approval-gate the optional write tool."""
        resolved: MCPToolsetClient = self.client if self.client is not None else _STRIPE_MCP_URL
        headers: dict[str, str] | None = None
        is_url = isinstance(resolved, AnyUrl) or (
            isinstance(resolved, str) and urlsplit(resolved).scheme.lower() in ('http', 'https')
        )
        if is_url:
            _validate_https_url(str(resolved))
            headers = {'Authorization': f'Bearer {self.api_key}'}
            if self.connected_account is not None:
                headers['Stripe-Account'] = self.connected_account

        toolset = MCPToolset[AgentDepsT](resolved, id=self.id, headers=headers)
        allowed = _READ_TOOL_NAMES
        if self.enable_writes:
            allowed = allowed | frozenset((_WRITE_TOOL_NAME,))
        filtered = toolset.filtered(lambda _ctx, tool_def: tool_def.name in allowed)
        if self.enable_writes:
            return filtered.approval_required(lambda _ctx, tool_def, _args: tool_def.name == _WRITE_TOOL_NAME)
        return filtered

    @classmethod
    def get_serialization_name(cls) -> None:
        """Keep credentials and account identity out of agent spec files."""
        return None
