"""Managed AWS MCP: `https://aws-mcp.us-east-1.api.aws/mcp`, or `eu-central-1` to stay in the EU.

Both endpoints verified 2026-09-08 against https://docs.aws.amazon.com/general/latest/gr/aws-mcp.html
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
        'MCP support is required for the AWS capability. Install it with: uv add "pydantic-ai-harness[aws]"'
    ) from _import_error

_AWS_MCP_URL = 'https://aws-mcp.us-east-1.api.aws/mcp'
_DEFAULT_DESCRIPTION = 'Use AWS documentation and operate AWS resources.'


@dataclass(kw_only=True)
class AWS(AbstractCapability[AgentDepsT]):
    """Connect an agent to the managed AWS MCP Server.

    The default serves every tool AWS publishes, including the ones that create and change
    resources. AWS enforces access: the credential's IAM permissions decide what the agent can read
    or change.
    """

    description: str | None = _DEFAULT_DESCRIPTION
    """Routing description used when the capability is loaded on demand."""

    url: str = _AWS_MCP_URL
    """Managed endpoint. Use `https://aws-mcp.eu-central-1.api.aws/mcp` to keep requests in the EU."""

    auth: Auth | Literal['oauth'] | str | None = field(default=None, repr=False)
    """`'oauth'` for AWS Sign-In, an AWS Sign-In token, or a custom `httpx.Auth`.

    Defaults to `$AWS_MCP_TOKEN`; leave both unset for the tools AWS answers without a credential.
    """

    read_only: bool = False
    """Expose only the tools AWS marks read-only, so the ones that create and change resources are
    hidden."""

    def get_toolset(self) -> AbstractToolset[AgentDepsT]:
        """Build the managed AWS MCP connection."""
        auth = self.auth or environ.get('AWS_MCP_TOKEN')
        toolset: AbstractToolset[AgentDepsT] = MCPToolset(
            self.url, id=self.id or 'aws', auth=auth, include_instructions=True
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
        url: str = _AWS_MCP_URL,
        read_only: bool = False,
    ) -> AWS[AgentDepsT]:
        """Construct an AWS capability from serializable options.

        `auth` is absent by design, so a spec file cannot carry an AWS token: the credential comes
        from `$AWS_MCP_TOKEN`.
        """
        return cls(id=id, description=description, defer_loading=defer_loading, url=url, read_only=read_only)

    @classmethod
    def get_serialization_name(cls) -> str:
        """Return the agent-spec capability name."""
        return 'AWS'
