"""Cloudflare managed MCP capability.

Provider contract, verified 2026-09-08 by an unauthenticated `tools/list`: Cloudflare runs one
Streamable HTTP server per product area, each at its own URL; `https://docs.mcp.cloudflare.com/mcp`
is the public documentation server, needs no credential, and annotates every tool `readOnlyHint`.
The servers that act on an account answer 401 unauthenticated, so their annotations are unverified.

Source: https://developers.cloudflare.com/agents/model-context-protocol/cloudflare/servers-for-cloudflare/.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal

from httpx import Auth
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.tools import AgentDepsT
from pydantic_ai.toolsets import AbstractToolset

from pydantic_ai_harness._mcp import is_read_only

try:
    from pydantic_ai.mcp import MCPToolset
except ImportError as _import_error:  # pragma: no cover
    raise ImportError(
        'MCP support is required for the Cloudflare capability. '
        'Install it with: uv add "pydantic-ai-harness[cloudflare]"'
    ) from _import_error

_CLOUDFLARE_DOCS_MCP_URL = 'https://docs.mcp.cloudflare.com/mcp'
_DEFAULT_DESCRIPTION = 'Use an official Cloudflare managed MCP server.'


@dataclass(kw_only=True)
class Cloudflare(AbstractCapability[AgentDepsT]):
    """Connect an agent to one of Cloudflare's managed MCP servers.

    The default is the public documentation server, which needs no credential. Point `url` at
    another managed server to reach its product tools; every tool that server publishes is
    exposed, including the ones that create, update, and delete resources, so the API token's
    scopes are the real boundary.
    """

    description: str | None = _DEFAULT_DESCRIPTION
    """Routing description used when the capability is loaded on demand."""

    url: str = _CLOUDFLARE_DOCS_MCP_URL
    """Managed server to connect to, such as `https://dns-analytics.mcp.cloudflare.com/mcp`."""

    auth: Auth | Literal['oauth'] | str | None = field(default=None, repr=False)
    """`'oauth'` for browser login, a Cloudflare API token, or a custom `httpx.Auth`.

    Defaults to `$CLOUDFLARE_API_TOKEN`.
    """

    read_only: bool = False
    """Expose only the tools the server marks `readOnlyHint`, so a tool Cloudflare left unannotated
    is hidden too."""

    def get_toolset(self) -> AbstractToolset[AgentDepsT]:
        """Build the Cloudflare MCP connection."""
        toolset: AbstractToolset[AgentDepsT] = MCPToolset(
            self.url,
            id=self.id or 'cloudflare',
            auth=self.auth or os.environ.get('CLOUDFLARE_API_TOKEN'),
            include_instructions=True,
        )
        return toolset.filtered(lambda _ctx, tool_def: is_read_only(tool_def)) if self.read_only else toolset

    @classmethod
    def from_spec(
        cls,
        *,
        id: str | None = None,
        description: str | None = _DEFAULT_DESCRIPTION,
        defer_loading: bool = False,
        url: str = _CLOUDFLARE_DOCS_MCP_URL,
        read_only: bool = False,
    ) -> Cloudflare[AgentDepsT]:
        """Construct from spec options, which exclude `auth` so a spec file cannot carry a token."""
        return cls(id=id, description=description, defer_loading=defer_loading, url=url, read_only=read_only)

    @classmethod
    def get_serialization_name(cls) -> str:
        """Return the agent-spec capability name."""
        return 'Cloudflare'
