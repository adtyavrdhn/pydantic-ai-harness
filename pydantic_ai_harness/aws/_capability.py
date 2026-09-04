"""Managed AWS MCP Server capability.

External contract, verified 2026-09-04:

- The managed server is GA in `us-east-1` and `eu-central-1`; SigV4 connections
  can configure a separate default Region for AWS operations.
- Public knowledge tools accept unauthenticated remote connections. Authenticated
  connections use a caller-owned AWS Sign-In OAuth 2.1 transport or AWS's MCP Proxy
  for AWS, which owns credential resolution and SigV4 signing.
- MCP tools carry `readOnlyHint`; the proxy's read-only mode uses that hint too.
- The service has no additional charge. Called AWS services and data transfer
  retain their normal charges.

Sources:
https://docs.aws.amazon.com/agent-toolkit/latest/userguide/getting-started-aws-mcp-server.html
https://docs.aws.amazon.com/agent-toolkit/latest/userguide/understanding-mcp-server-tools.html
https://docs.aws.amazon.com/general/latest/gr/aws-mcp.html
https://aws.amazon.com/about-aws/whats-new/2026/05/aws-mcp-server/
https://github.com/aws/mcp-proxy-for-aws

Re-check the endpoint table, authentication decision guide, tool annotations,
and launch status before changing transport, access, or Region behavior.
"""

from __future__ import annotations

import re
from dataclasses import KW_ONLY, dataclass, field
from typing import Literal

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import UserError
from pydantic_ai.tools import AgentDepsT, RunContext, ToolDefinition
from pydantic_ai.toolsets import AbstractToolset, ToolsetTool

try:
    from fastmcp.client.transports import ClientTransport
    from pydantic_ai.mcp import MCPToolset, MCPToolsetClient
except ImportError as _import_error:  # pragma: no cover
    raise ImportError(
        'MCP support is required for the AWS capability. Install `pydantic-ai-harness[aws]`.'
    ) from _import_error

AWSAccess = Literal['read_only', 'approval_required', 'unrestricted']
"""Which managed AWS MCP tools the agent may execute."""

_ENDPOINTS = {
    'us-east-1': 'https://aws-mcp.us-east-1.api.aws/mcp',
    'eu-central-1': 'https://aws-mcp.eu-central-1.api.aws/mcp',
}
_ACCOUNT_ID_PATTERN = re.compile(r'[0-9]{12}')
_REGION_PATTERN = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)+')
_DEFAULT_DESCRIPTION = 'Use the managed AWS MCP Server for one AWS account and target Region.'
_INSTRUCTIONS = (
    'The declared AWS scope for this agent is account `{account_id}` and target Region `{region}`. Treat both values '
    'as required context for every AWS operation. The authenticated IAM identity is the authority: '
    'do not claim access that its policies deny, and do not switch accounts or target Regions. Prefer AWS documentation '
    'and read operations before proposing changes. After a failed change with an unknown outcome, inspect current state '
    'before retrying. This is real AWS, not the LocalStack emulator. Access mode is `{access}` and authentication mode '
    'is `{authentication}`.'
)


def _validate_configuration(
    account_id: str,
    region: str,
    endpoint_region: str,
    access: str,
    authentication: str,
    managed_transport: ClientTransport | None,
) -> tuple[AWSAccess, Literal['unauthenticated', 'oauth', 'sigv4']]:
    if _ACCOUNT_ID_PATTERN.fullmatch(account_id) is None:
        raise UserError('`account_id` must be a 12-digit AWS account ID.')
    if _REGION_PATTERN.fullmatch(region) is None:
        raise UserError('`region` must be an AWS Region identifier such as `us-west-2`.')
    if endpoint_region not in _ENDPOINTS:
        supported = ', '.join(f'`{value}`' for value in _ENDPOINTS)
        raise UserError(f'`endpoint_region` must be one of {supported}.')
    if access not in ('read_only', 'approval_required', 'unrestricted'):
        raise UserError('`access` must be `read_only`, `approval_required`, or `unrestricted`.')
    if authentication not in ('unauthenticated', 'oauth', 'sigv4'):
        raise UserError('`authentication` must be `unauthenticated`, `oauth`, or `sigv4`.')
    if authentication == 'unauthenticated' and managed_transport is not None:
        raise UserError('`managed_transport` requires `authentication="oauth"` or `authentication="sigv4"`.')
    if authentication != 'unauthenticated' and managed_transport is None:
        raise UserError(f'`authentication="{authentication}"` requires a caller-owned `managed_transport`.')
    if managed_transport is not None and not isinstance(managed_transport, ClientTransport):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise UserError('Authenticated AWS connections require a pre-built FastMCP `ClientTransport`.')
    return access, authentication


def _is_explicitly_read_only(tool_def: ToolDefinition) -> bool:
    metadata = tool_def.metadata
    if metadata is None:  # pragma: no cover - MCPToolset always supplies a metadata mapping
        return False
    annotations = metadata.get('annotations')
    match annotations:
        case {'readOnlyHint': True}:
            return True
        case _:
            return False


def _requires_approval(_ctx: RunContext[AgentDepsT], tool_def: ToolDefinition, _tool_args: dict[str, object]) -> bool:
    return not _is_explicitly_read_only(tool_def)


class _AWSToolset(MCPToolset[AgentDepsT]):
    """Managed AWS MCP connection with conservative annotation filtering."""

    def __init__(
        self,
        *,
        endpoint_region: Literal['us-east-1', 'eu-central-1'],
        read_only: bool,
        client: MCPToolsetClient | None,
        id: str,
    ) -> None:
        if client is None:
            super().__init__(_ENDPOINTS[endpoint_region], id=id, include_instructions=False)
        else:
            super().__init__(client, id=id, include_instructions=False)
        self._read_only = read_only

    async def get_tools(self, ctx: RunContext[AgentDepsT]) -> dict[str, ToolsetTool[AgentDepsT]]:
        tools = await super().get_tools(ctx)
        if not tools:
            raise UserError(
                'The managed AWS MCP Server returned no tools. Check authentication and retry the connection; '
                'an empty catalog can indicate throttled initialization.'
            )
        if not self._read_only:
            return tools
        read_tools = {name: tool for name, tool in tools.items() if _is_explicitly_read_only(tool.tool_def)}
        if not read_tools:
            raise UserError(
                'The managed AWS MCP Server returned no tools explicitly marked read-only. '
                'Its safety annotations may have changed.'
            )
        return read_tools


@dataclass
class AWS(AbstractCapability[AgentDepsT]):
    """Access one real AWS account and target Region through the managed AWS MCP Server.

    Direct connections use unauthenticated public knowledge tools. Pass a
    trusted caller-owned MCP transport for AWS Sign-In OAuth or AWS's SigV4 MCP
    proxy so the transport retains its identity lifecycle.
    The default exposes only tools whose MCP annotation explicitly marks them
    read-only. `approval_required` uses Pydantic AI's tool approval wrapper for
    every new non-read tool call, including a model-initiated retry, and `unrestricted`
    requires an explicit opt-in.
    """

    account_id: str
    """Declared 12-digit AWS account for this capability instance."""

    region: str
    """Declared target Region for AWS operations, for example `us-west-2`."""

    _: KW_ONLY

    endpoint_region: Literal['us-east-1', 'eu-central-1'] = 'us-east-1'
    """Managed endpoint for direct unauthenticated connections."""

    access: AWSAccess = 'read_only'
    """Expose read-only tools, approval-gate other tools, or expose all tools."""

    authentication: Literal['unauthenticated', 'oauth', 'sigv4'] = 'unauthenticated'
    """Use public knowledge tools, or identify the caller-owned OAuth or SigV4 transport."""

    id: str | None = None
    """Stable capability ID, derived from account, target Region, and endpoint Region when omitted."""

    description: str | None = _DEFAULT_DESCRIPTION
    """Routing description used when the capability is loaded on demand."""

    managed_transport: ClientTransport | None = field(default=None, repr=False)
    """Trusted caller-owned transport connected to the managed AWS MCP Server.

    For SigV4 one-account scope, configure exactly one proxy profile. Passing
    this value asserts that the transport reaches AWS's managed endpoint and that
    its identity matches `account_id`. Harness does not inspect credentials,
    sign requests, refresh tokens, or alter the transport.
    """

    def __post_init__(self) -> None:
        self.access, self.authentication = _validate_configuration(
            self.account_id,
            self.region,
            self.endpoint_region,
            self.access,
            self.authentication,
            self.managed_transport,
        )
        self.id = self._derived_id()

    def _derived_id(self) -> str:
        return self.id if self.id is not None else f'aws-{self.account_id}-{self.region}-{self.endpoint_region}'

    def get_toolset(self) -> AbstractToolset[AgentDepsT]:
        """Build the managed AWS MCP toolset and apply the selected access policy."""
        toolset = _AWSToolset[AgentDepsT](
            endpoint_region=self.endpoint_region,
            read_only=self.access == 'read_only',
            client=self.managed_transport,
            id=self._derived_id(),
        )
        if self.access == 'approval_required':
            return toolset.approval_required(_requires_approval)
        return toolset

    def get_instructions(self) -> str | None:
        """Return the declared account, Region, identity, and access scope."""
        return _INSTRUCTIONS.format(
            account_id=self.account_id,
            region=self.region,
            access=self.access,
            authentication=self.authentication,
        )

    @classmethod
    def from_spec(
        cls,
        account_id: str,
        region: str,
        *,
        endpoint_region: Literal['us-east-1', 'eu-central-1'] = 'us-east-1',
        access: AWSAccess = 'read_only',
        id: str | None = None,
        description: str | None = _DEFAULT_DESCRIPTION,
        defer_loading: bool = False,
    ) -> AWS[AgentDepsT]:
        """Construct from serializable options, excluding the runtime-only managed transport."""
        return cls(
            account_id=account_id,
            region=region,
            endpoint_region=endpoint_region,
            access=access,
            authentication='unauthenticated',
            id=id,
            description=description,
            defer_loading=defer_loading,
        )

    @classmethod
    def get_serialization_name(cls) -> str:
        """Return the agent-spec capability name."""
        return 'AWS'
