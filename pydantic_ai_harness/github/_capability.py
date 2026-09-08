"""GitHub hosted MCP: `https://api.githubcopilot.com/mcp/`, and `X-MCP-Readonly: true` for read tools only.

Both verified 2026-09-08 against
https://github.com/github/github-mcp-server/blob/main/docs/remote-server.md.
"""

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
        'MCP support is required for the GitHub capability. Install it with: uv add "pydantic-ai-harness[github]"'
    ) from _import_error

_GITHUB_MCP_URL = 'https://api.githubcopilot.com/mcp/'
_DEFAULT_DESCRIPTION = "Read and change GitHub through GitHub's hosted MCP server."


@dataclass(kw_only=True)
class GitHub(AbstractCapability[AgentDepsT]):
    """Connect an agent to GitHub's hosted MCP server.

    The default serves GitHub's write tools alongside its read tools; the token's scopes decide
    what the agent can actually read or change.
    """

    description: str | None = _DEFAULT_DESCRIPTION
    """Routing description used when the capability is loaded on demand."""

    url: str = _GITHUB_MCP_URL
    """MCP endpoint.

    Override it for GitHub Enterprise Cloud with data residency, which serves the same API from
    `https://copilot-api.<tenant>.ghe.com/mcp/`.
    """

    auth: Auth | Literal['oauth'] | str | None = field(default=None, repr=False)
    """A GitHub token or a custom `httpx.Auth`. Defaults to `$GITHUB_TOKEN`.

    GitHub registers no OAuth clients dynamically, so the `'oauth'` shorthand cannot complete
    browser login; pass a pre-configured `httpx.Auth` instead.
    """

    read_only: bool = False
    """Send GitHub's `X-MCP-Readonly` header, so the server serves only the tools that read."""

    def get_toolset(self) -> AbstractToolset[AgentDepsT]:
        """Build the GitHub MCP connection."""
        auth = self.auth or environ.get('GITHUB_TOKEN')
        if not auth:
            raise UserError('GitHub needs a token. Pass `auth=` or set `GITHUB_TOKEN`.')
        return MCPToolset(
            self.url,
            id=self.id or 'github',
            auth=auth,
            headers={'X-MCP-Readonly': 'true'} if self.read_only else None,
            include_instructions=True,
        )

    @classmethod
    def from_spec(
        cls,
        *,
        id: str | None = None,
        description: str | None = _DEFAULT_DESCRIPTION,
        defer_loading: bool = False,
        url: str = _GITHUB_MCP_URL,
        read_only: bool = False,
    ) -> GitHub[AgentDepsT]:
        """Construct a GitHub capability from serializable options.

        `auth` is absent by design, so a spec file cannot carry a GitHub token: the credential comes
        from `$GITHUB_TOKEN`.
        """
        return cls(id=id, description=description, defer_loading=defer_loading, url=url, read_only=read_only)

    @classmethod
    def get_serialization_name(cls) -> str:
        """Return the agent-spec capability name."""
        return 'GitHub'
