"""Atlassian Rovo hosted MCP: `https://mcp.atlassian.com/v2/mcp?tools=all`, the flat tool catalogue.

Unauthenticated `tools/list` returns 401 (2026-09-08), so which tools Atlassian annotates `readOnlyHint` is
unobserved; auth methods: https://developer.atlassian.com/cloud/rovo-mcp/guides/authentication-and-authorization/.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from os import environ
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
        'MCP support is required for the Atlassian capability. Install it with: uv add "pydantic-ai-harness[atlassian]"'
    ) from _import_error

_ATLASSIAN_MCP_URL = 'https://mcp.atlassian.com/v2/mcp?tools=all'
_DEFAULT_DESCRIPTION = 'Use Jira, Confluence, and the other Atlassian apps on your sites.'


@dataclass(kw_only=True)
class Atlassian(AbstractCapability[AgentDepsT]):
    """Connect an agent to Atlassian's hosted Rovo MCP server.

    The default serves Atlassian's write tools too; the credential's scopes and the permission groups
    an organization admin enabled decide what the agent can read or change.
    """

    description: str | None = _DEFAULT_DESCRIPTION
    """Routing description used when the capability is loaded on demand."""

    auth: Auth | Literal['oauth'] | str | None = field(default=None, repr=False)
    """`'oauth'` for browser login, or an Atlassian service account API key, sent as a bearer token.

    Defaults to `$ATLASSIAN_API_KEY`, and to browser login when that is unset. A *personal* API
    token is a different credential, which Atlassian takes over Basic auth: pass it as
    `httpx.BasicAuth(email, personal_api_token)` rather than through the environment variable.
    """

    read_only: bool = False
    """Expose only the tools Atlassian marks `readOnlyHint`, dropping any tool it leaves unmarked."""

    def get_toolset(self) -> AbstractToolset[AgentDepsT]:
        """Build the Atlassian Rovo MCP connection."""
        auth = self.auth or environ.get('ATLASSIAN_API_KEY') or 'oauth'
        toolset: AbstractToolset[AgentDepsT] = MCPToolset(
            _ATLASSIAN_MCP_URL, id=self.id or 'atlassian', auth=auth, include_instructions=True
        )
        if self.read_only:
            return toolset.filtered(lambda _ctx, tool_def: is_read_only(tool_def))
        return toolset

    @classmethod
    def from_spec(
        cls,
        *,
        id: str | None = None,
        description: str | None = _DEFAULT_DESCRIPTION,
        defer_loading: bool = False,
        read_only: bool = False,
    ) -> Atlassian[AgentDepsT]:
        """Construct an Atlassian capability from serializable options.

        `auth` is absent by design, so a spec file cannot carry an Atlassian credential: it comes
        from `$ATLASSIAN_API_KEY`, or from browser sign-in when that is unset.
        """
        return cls(id=id, description=description, defer_loading=defer_loading, read_only=read_only)

    @classmethod
    def get_serialization_name(cls) -> str:
        """Return the agent-spec capability name."""
        return 'Atlassian'
