"""GitHub hosted MCP capability."""

from __future__ import annotations

from dataclasses import dataclass, field
from os import environ

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import UserError
from pydantic_ai.tools import AgentDepsT
from pydantic_ai.toolsets import AbstractToolset

from pydantic_ai_harness._mcp import MCPAuth, MCPAuthFunc, MCPClientFunc, is_read_only, per_run_auth, per_run_client

try:
    from pydantic_ai.mcp import MCPToolset, MCPToolsetClient
except ImportError as exc:  # pragma: no cover
    raise ImportError('Install GitHub support with: uv add "pydantic-ai-harness[github]"') from exc


GITHUB_MCP_URL = 'https://api.githubcopilot.com/mcp/'


@dataclass(kw_only=True)
class GitHub(AbstractCapability[AgentDepsT]):
    """Use GitHub's hosted tools with the permissions of the connected credential."""

    description: str | None = 'Read and change GitHub resources.'
    auth: MCPAuth | MCPAuthFunc[AgentDepsT] | None = field(default=None, repr=False)
    """A GitHub token, an `httpx.Auth`, or a function of the run context that returns one.

    Unset, `GITHUB_TOKEN` is used. If the function returns `None`, that run has no GitHub tools.
    """
    read_only: bool = False
    """Offer only read tools. With a custom `client`, keep only the tools the server marks as read-only."""
    include_instructions: bool = True
    """Forward the server's instructions to the agent."""
    client: MCPToolsetClient | MCPClientFunc[AgentDepsT] | None = field(default=None, repr=False)
    """Your own MCP client or transport, or a function that returns one for each run.

    It replaces `url`, `auth`, and `toolsets`.
    """
    url: str = GITHUB_MCP_URL
    """The MCP server URL, for example a GitHub Enterprise Cloud endpoint."""
    toolsets: list[str] | None = None
    """GitHub tool groups to offer, such as `'repos'`. `None` keeps the server's defaults."""

    def get_toolset(self) -> AbstractToolset[AgentDepsT]:
        """Return the GitHub tools."""
        id = self.id or 'github'
        if self.client is not None:
            toolset = per_run_client(self.client, self._from_client, id=id)
            if self.read_only:
                return toolset.filtered(lambda _ctx, tool: is_read_only(tool))
            return toolset
        return per_run_auth(self.auth, self._connect, id=id)

    def _from_client(self, client: MCPToolsetClient) -> MCPToolset[AgentDepsT]:
        return MCPToolset(client, id=self.id or 'github', include_instructions=self.include_instructions)

    def _connect(self, auth: MCPAuth | None) -> MCPToolset[AgentDepsT]:
        if auth is None:
            auth = environ.get('GITHUB_TOKEN')
        if auth is None:
            raise UserError('Set `GITHUB_TOKEN` or pass `auth` to connect to GitHub.')
        headers = {'X-MCP-Readonly': 'true'} if self.read_only else {}
        if self.toolsets is not None:
            headers['X-MCP-Toolsets'] = ','.join(self.toolsets)
        return MCPToolset(
            self.url,
            id=self.id or 'github',
            auth=auth,
            headers=headers,
            include_instructions=self.include_instructions,
        )
