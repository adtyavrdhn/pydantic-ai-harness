"""Supabase hosted MCP: `https://mcp.supabase.com/mcp`, scoped by `?project_ref=`.

`?read_only=true` withholds the write tools; both verified 2026-09-08 against
https://supabase.com/docs/guides/ai-tools/mcp.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from os import environ
from typing import Literal
from urllib.parse import urlencode

from httpx import Auth
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import UserError
from pydantic_ai.tools import AgentDepsT

try:
    from pydantic_ai.mcp import MCPToolset
except ImportError as _import_error:  # pragma: no cover
    raise ImportError(
        'MCP support is required for the Supabase capability. Install it with: uv add "pydantic-ai-harness[supabase]"'
    ) from _import_error

_SUPABASE_MCP_URL = 'https://mcp.supabase.com/mcp'
_DEFAULT_DESCRIPTION = 'Inspect and change one Supabase project: its tables, data, migrations, and logs.'


@dataclass(kw_only=True)
class Supabase(AbstractCapability[AgentDepsT]):
    """Connect an agent to one Supabase project through Supabase's hosted MCP server.

    The default connection serves Supabase's write tools, `execute_sql` among them; the token's
    scopes decide what the agent can read or change.
    """

    project_ref: str
    """Supabase project ID. The server scopes the connection to this project and turns off its
    account-wide tools."""

    description: str | None = _DEFAULT_DESCRIPTION
    """Routing description used when the capability is loaded on demand."""

    auth: Auth | Literal['oauth'] | str | None = field(default=None, repr=False)
    """`'oauth'` for browser login, a Supabase personal access token, or a custom `httpx.Auth`.

    Defaults to `$SUPABASE_ACCESS_TOKEN`.
    """

    read_only: bool = False
    """Connect with Supabase's `read_only=true`, so the server runs SQL as a read-only Postgres user
    and withholds its write tools."""

    def get_toolset(self) -> MCPToolset[AgentDepsT]:
        """Build the Supabase MCP connection."""
        if not self.project_ref:
            raise UserError('Supabase needs a project ref: pass project_ref= to scope the connection to one project.')
        auth = self.auth or environ.get('SUPABASE_ACCESS_TOKEN')
        if not auth:
            raise UserError(
                'Supabase needs a token: pass auth= or set SUPABASE_ACCESS_TOKEN. '
                "Pass auth='oauth' to log in through the browser instead."
            )
        query = {'project_ref': self.project_ref} | ({'read_only': 'true'} if self.read_only else {})
        return MCPToolset(
            f'{_SUPABASE_MCP_URL}?{urlencode(query)}',
            id=self.id or 'supabase',
            auth=auth,
            include_instructions=True,
        )

    @classmethod
    def from_spec(
        cls,
        *,
        project_ref: str,
        id: str | None = None,
        description: str | None = _DEFAULT_DESCRIPTION,
        defer_loading: bool = False,
        read_only: bool = False,
    ) -> Supabase[AgentDepsT]:
        """Construct a Supabase capability from serializable options.

        `auth` is absent by design, so a spec file cannot carry a Supabase token: the credential
        comes from `$SUPABASE_ACCESS_TOKEN`.
        """
        return cls(
            project_ref=project_ref,
            id=id,
            description=description,
            defer_loading=defer_loading,
            read_only=read_only,
        )

    @classmethod
    def get_serialization_name(cls) -> str:
        """Return the agent-spec capability name."""
        return 'Supabase'
