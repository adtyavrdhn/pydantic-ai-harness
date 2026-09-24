"""AWS hosted MCP capability."""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import UserError
from pydantic_ai.tools import AgentDepsT
from pydantic_ai.toolsets import AbstractToolset

from pydantic_ai_harness._mcp import MCPAuth, MCPAuthFunc, MCPClientFunc, credential, is_read_only, per_run

try:
    from pydantic_ai.mcp import MCPToolset, MCPToolsetClient
except ImportError as exc:  # pragma: no cover
    raise ImportError('Install AWS support with: uv add "pydantic-ai-harness[aws]"') from exc

from typing import Literal


@dataclass(kw_only=True)
class AWS(AbstractCapability[AgentDepsT]):
    """Use AWS's managed MCP server with the permissions of the connected identity."""

    description: str | None = 'Use AWS knowledge and account tools.'
    auth: MCPAuth | MCPAuthFunc[AgentDepsT] | None = field(default=None, repr=False)
    """An access token, an `httpx.Auth`, or a function of the run context that returns one.

    Unset, pass `client` instead. If the function returns `None`, that run has no AWS tools.
    """
    read_only: bool = False
    """Keep only the tools the server marks as read-only."""
    include_instructions: bool = True
    """Forward the server's instructions to the agent."""
    client: MCPToolsetClient | MCPClientFunc[AgentDepsT] | None = field(default=None, repr=False)
    """Your own MCP client or transport, or a function that returns one for each run.

    It replaces `region` and `auth`.
    """
    region: Literal['us-east-1', 'eu-central-1'] = 'us-east-1'
    """Region of the MCP endpoint. It does not limit which regions the tools act on."""

    def get_toolset(self) -> AbstractToolset[AgentDepsT]:
        """Return the AWS tools."""
        id = self.id or 'aws'
        if self.client is not None:
            toolset: AbstractToolset[AgentDepsT] = per_run(self.client, self._from_client, id=id)
        else:
            toolset = per_run(self.auth, self._connect, id=id)
        if self.read_only:
            return toolset.filtered(lambda _ctx, tool: is_read_only(tool))
        return toolset

    def _from_client(self, client: MCPToolsetClient) -> MCPToolset[AgentDepsT]:
        return MCPToolset(client, id=self.id or 'aws', include_instructions=self.include_instructions)

    def _connect(self, auth: MCPAuth | None) -> MCPToolset[AgentDepsT]:
        if auth is None:
            raise UserError('Pass `auth` or `client` to connect to AWS.')
        return MCPToolset(
            f'https://aws-mcp.{self.region}.api.aws/mcp',
            id=self.id or 'aws',
            auth=credential(auth, env=None, service='AWS'),
            headers=None,
            include_instructions=self.include_instructions,
        )
