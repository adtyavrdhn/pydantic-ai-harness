"""Atlassian Rovo MCP tool selection and site boundary.

External contract, verified 2026-09-04:

- `https://mcp.atlassian.com/v2/mcp` is Atlassian's streamable HTTP endpoint.
  Adding `?tools=all` exposes the paginated flat tool catalogue instead of the
  discovery and execute meta-tools.
- Every product tool call uses a `cloudId`. Jira and Confluence support OAuth
  2.1 and API-token authentication. Jira Service Management requires API-token
  authentication. Bitbucket Cloud supports both and requires an
  organization-linked workspace.
- Atlassian groups tools by read, write, search, delete, and manage intent.
  Organization administrators can independently disable those groups.

Sources: https://support.atlassian.com/atlassian-ai-gateway/docs/supported-tools/,
https://support.atlassian.com/atlassian-ai-gateway/docs/configure-oauth-2-1/,
and https://support.atlassian.com/atlassian-ai-gateway/docs/authentication-and-authorization/.
Re-check the endpoint, authentication table, and exact tool names before
changing the allowlists below.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import AnyUrl
from pydantic_ai.exceptions import UserError
from pydantic_ai.tools import AgentDepsT, RunContext
from pydantic_ai.toolsets import ToolsetTool

try:
    from pydantic_ai.mcp import CallToolFunc, MCPToolset, MCPToolsetClient
except ImportError as _import_error:  # pragma: no cover
    raise ImportError(
        'MCP support is required for the Atlassian capability. Install it with: uv add "pydantic-ai-slim[mcp]"'
    ) from _import_error

ATLASSIAN_MCP_URL = 'https://mcp.atlassian.com/v2/mcp?tools=all'
"""Atlassian Rovo MCP v2 endpoint with the flat tool catalogue."""

AtlassianAccess = Literal['read_only', 'read_write', 'destructive']
"""Maximum class of Atlassian operation exposed to the agent."""

AtlassianProduct = Literal['jira', 'confluence', 'jira_service_management', 'bitbucket']
"""Atlassian products represented by the supported Rovo MCP tool catalogue."""

_COMMON_READ_TOOLS = frozenset({'atlassianUserInfo'})

_READ_TOOLS: dict[AtlassianProduct, frozenset[str]] = {
    'jira': frozenset(
        {
            'findJiraIssueAssignableUsers',
            'getJiraBoardConfig',
            'getJiraBoardIssueData',
            'getJiraBoardSprintData',
            'getJiraCurrentUser',
            'getJiraIssue',
            'getJiraProjectVersions',
            'listJiraBoardSprints',
            'listJiraBoards',
            'listJiraIssueComments',
            'listJiraIssueTransitions',
            'listJiraProjects',
            'lookupJiraAccountId',
            'searchJiraIssuesUsingJql',
        }
    ),
    'confluence': frozenset(
        {
            'getConfluenceAttachment',
            'getConfluenceComment',
            'getConfluenceContent',
            'getConfluenceContentPermissions',
            'getConfluenceContentRestrictionState',
            'getConfluenceSpace',
            'listConfluenceAttachments',
            'listConfluenceComments',
            'listConfluenceContent',
            'listConfluenceSpaces',
            'listConfluenceTasks',
            'searchConfluence',
        }
    ),
    'jira_service_management': frozenset(
        {
            'getJsmOpsAlerts',
            'getJsmOpsScheduleInfo',
            'getJsmOpsTeamInfo',
        }
    ),
    'bitbucket': frozenset(
        {
            'getBitbucketRepoBranch',
            'getBitbucketRepoCommit',
            'getBitbucketRepoFileContent',
            'getBitbucketRepoPipeline',
            'getBitbucketRepoPipelineStepLog',
            'getBitbucketRepoPullRequest',
            'getBitbucketRepoPullRequestDiff',
            'getBitbucketRepository',
            'listBitbucketRepoPipelines',
            'listBitbucketRepoPullRequestComments',
            'listBitbucketRepoPullRequests',
            'listBitbucketRepositories',
            'listBitbucketWorkspaces',
        }
    ),
}

_WRITE_TOOLS: dict[AtlassianProduct, frozenset[str]] = {
    'jira': frozenset(
        {
            'addOrEditJiraIssueComment',
            'addOrEditJiraIssueWorklog',
            'createJiraIssue',
            'createJiraIssueLink',
            'editJiraIssue',
            'transitionJiraIssue',
            'uploadAttachmentToJiraIssue',
            'watchJiraIssue',
        }
    ),
    'confluence': frozenset(
        {
            'addLabelsToConfluenceContent',
            'completeConfluenceTask',
            'createConfluenceAttachment',
            'createConfluenceComment',
            'createConfluenceContent',
            'updateConfluenceComment',
            'updateConfluenceContent',
        }
    ),
    'jira_service_management': frozenset({'updateJsmOpsAlert'}),
    'bitbucket': frozenset(
        {
            'addBitbucketRepoPullRequestComment',
            'approveBitbucketRepoPullRequest',
            'createBitbucketRepoBranch',
            'createBitbucketRepoPullRequest',
            'mergeBitbucketRepoPullRequest',
            'requestChangesOnBitbucketRepoPullRequest',
            'runBitbucketRepoPipeline',
            'updateBitbucketRepoPullRequest',
        }
    ),
}

_DESTRUCTIVE_TOOLS: dict[AtlassianProduct, frozenset[str]] = {
    'jira': frozenset({'deleteJiraComment', 'deleteJiraIssue', 'deleteJiraIssueAttachment'}),
    'confluence': frozenset(),
    'jira_service_management': frozenset(),
    'bitbucket': frozenset(),
}


def normalize_products(products: AtlassianProduct | Sequence[AtlassianProduct]) -> tuple[AtlassianProduct, ...]:
    values = (products,) if isinstance(products, str) else tuple(products)
    if not values:
        raise UserError('`products` must contain at least one Atlassian product.')
    unknown = set(values) - _READ_TOOLS.keys()
    if unknown:
        choices = ', '.join(sorted(_READ_TOOLS))
        raise UserError(f'Unknown Atlassian product {sorted(unknown)[0]!r}; expected one of: {choices}.')
    return tuple(dict.fromkeys(values))


def validate_access(access: AtlassianAccess) -> None:
    if access not in ('read_only', 'read_write', 'destructive'):
        raise UserError('`access` must be `read_only`, `read_write`, or `destructive`.')


def _is_url(client: MCPToolsetClient) -> bool:
    return isinstance(client, AnyUrl) or (
        isinstance(client, str) and urlsplit(client).scheme.lower() in ('http', 'https')
    )


class AtlassianToolset(MCPToolset[AgentDepsT]):
    """A site-scoped, allowlisted view of Atlassian's hosted Rovo MCP tools.

    Use `Atlassian` for capability instructions and safe write approvals. Use
    this lower-level type when composing Pydantic AI toolset wrappers directly.
    """

    def __init__(
        self,
        *,
        cloud_id: str,
        products: AtlassianProduct | Sequence[AtlassianProduct] = ('jira',),
        access: AtlassianAccess = 'read_only',
        authorization_token: str | None = None,
        client: MCPToolsetClient | None = None,
        id: str = 'atlassian',
    ) -> None:
        """Build an Atlassian Rovo MCP toolset.

        Args:
            cloud_id: Atlassian site ID accepted by every selected product tool.
            products: Product tool families to expose. Jira is the default.
            access: Read-only, read-write, or destructive tool exposure.
            authorization_token: Caller-owned bearer token. Omit for OAuth 2.1.
            client: Replacement MCP client for custom auth, transport, or tests.
            id: Stable toolset ID.
        """
        if not cloud_id.strip():
            raise UserError('`cloud_id` must not be empty.')
        normalized_products = normalize_products(products)
        validate_access(access)
        resolved_client: MCPToolsetClient = ATLASSIAN_MCP_URL if client is None else client
        auth: Literal['oauth'] | str | None = authorization_token or 'oauth' if _is_url(resolved_client) else None
        super().__init__(resolved_client, id=id, auth=auth, process_tool_call=self._enforce_site_scope)
        self.cloud_id = cloud_id
        self.products = normalized_products
        self.access = access

    @property
    def write_tool_names(self) -> frozenset[str]:
        """Mutation tool names enabled by the selected products and access."""
        if self.access == 'read_only':
            return frozenset()
        return frozenset(name for product in self.products for name in _WRITE_TOOLS[product])

    @property
    def destructive_tool_names(self) -> frozenset[str]:
        """Destructive tool names enabled by the selected products and access."""
        if self.access != 'destructive':
            return frozenset()
        return frozenset(name for product in self.products for name in _DESTRUCTIVE_TOOLS[product])

    async def get_tools(self, ctx: RunContext[AgentDepsT]) -> dict[str, ToolsetTool[AgentDepsT]]:
        tools = await super().get_tools(ctx)
        selected: dict[str, tuple[AtlassianProduct | Literal['common'], str]] = {
            name: ('common', 'read') for name in _COMMON_READ_TOOLS
        }
        for product in self.products:
            selected.update((name, (product, 'read')) for name in _READ_TOOLS[product])
            if self.access != 'read_only':
                selected.update((name, (product, 'write')) for name in _WRITE_TOOLS[product])
            if self.access == 'destructive':
                selected.update((name, (product, 'destructive')) for name in _DESTRUCTIVE_TOOLS[product])

        return {
            name: replace(
                tool,
                tool_def=replace(
                    tool.tool_def,
                    metadata={
                        **(tool.tool_def.metadata or {}),
                        'atlassian_product': selected[name][0],
                        'atlassian_access': selected[name][1],
                        'atlassian_cloud_id': self.cloud_id,
                    },
                ),
            )
            for name, tool in tools.items()
            if name in selected
        }

    async def _enforce_site_scope(
        self,
        ctx: RunContext[Any],
        call_tool: CallToolFunc,
        name: str,
        args: dict[str, Any],
    ) -> Any:
        del ctx
        if name not in _COMMON_READ_TOOLS:
            requested_cloud_id = args.get('cloudId')
            if requested_cloud_id != self.cloud_id:
                raise UserError(
                    f'Atlassian tool {name!r} is scoped to cloudId {self.cloud_id!r}; received {requested_cloud_id!r}.'
                )
        return await call_tool(name, args)
