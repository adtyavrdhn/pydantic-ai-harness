"""Cloudflare managed MCP transport and policy.

External contract, verified 2026-09-04:

- Cloudflare recommends `https://mcp.cloudflare.com/mcp` for broad API access. It exposes
  `docs`, `search`, and `execute`; `execute` can read or mutate and is marked destructive.
- Sixteen focused `*.mcp.cloudflare.com/mcp` servers expose typed product tools. Their
  `readOnlyHint` annotations distinguish calls safe to expose without mutation opt-in.
- Managed servers support browser OAuth and bearer API tokens. Focused authenticated
  servers accept `cf-account-id` to pin multi-account credentials. Code Mode accepts
  `account_id` on `execute` instead.
- Cloudflare exposes no managed-server zone header. Focused tool schemas use `zone_id`,
  `zoneId`, or `zone`; this toolset restricts and fills those arguments when `zone_id` is set.

Sources: https://github.com/cloudflare/mcp and
https://github.com/cloudflare/mcp-server-cloudflare. Re-check both READMEs and the
Code Mode `src/tools` implementations when the catalog or policy changes.
"""

from __future__ import annotations

from dataclasses import replace
from enum import Enum
from pathlib import Path
from typing import Any, TypeAlias

from pydantic import AnyUrl, TypeAdapter
from pydantic_ai.exceptions import ApprovalRequired, ModelRetry, UserError
from pydantic_ai.tools import AgentDepsT, ObjectJsonSchema, RunContext
from pydantic_ai.toolsets import ToolsetTool
from pydantic_core import to_json

try:
    from fastmcp.client.transports import StreamableHttpTransport
    from pydantic_ai.mcp import MCPToolset, MCPToolsetClient
except ImportError as _import_error:  # pragma: no cover
    raise ImportError(
        'MCP support is required for Cloudflare. Install it with: uv add "pydantic-ai-harness[cloudflare]"'
    ) from _import_error

__all__ = ['CloudflareServer', 'CloudflareToolset', 'MCPToolsetClient']


class CloudflareServer(str, Enum):
    """Official Cloudflare managed MCP server selection."""

    API = 'api'
    DOCS = 'docs'
    WORKERS_BINDINGS = 'workers_bindings'
    WORKERS_BUILDS = 'workers_builds'
    OBSERVABILITY = 'observability'
    CONTAINERS = 'containers'
    BROWSER = 'browser'
    LOGPUSH = 'logpush'
    AI_GATEWAY = 'ai_gateway'
    AUTORAG = 'autorag'
    AUDIT_LOGS = 'audit_logs'
    DNS_ANALYTICS = 'dns_analytics'
    DEX = 'dex'
    CASB = 'casb'
    RADAR = 'radar'
    BLOG = 'blog'
    DEMO_DAY = 'demo_day'


_SERVER_URLS: dict[CloudflareServer, str] = {
    CloudflareServer.API: 'https://mcp.cloudflare.com/mcp',
    CloudflareServer.DOCS: 'https://docs.mcp.cloudflare.com/mcp',
    CloudflareServer.WORKERS_BINDINGS: 'https://bindings.mcp.cloudflare.com/mcp',
    CloudflareServer.WORKERS_BUILDS: 'https://builds.mcp.cloudflare.com/mcp',
    CloudflareServer.OBSERVABILITY: 'https://observability.mcp.cloudflare.com/mcp',
    CloudflareServer.CONTAINERS: 'https://containers.mcp.cloudflare.com/mcp',
    CloudflareServer.BROWSER: 'https://browser.mcp.cloudflare.com/mcp',
    CloudflareServer.LOGPUSH: 'https://logs.mcp.cloudflare.com/mcp',
    CloudflareServer.AI_GATEWAY: 'https://ai-gateway.mcp.cloudflare.com/mcp',
    CloudflareServer.AUTORAG: 'https://autorag.mcp.cloudflare.com/mcp',
    CloudflareServer.AUDIT_LOGS: 'https://auditlogs.mcp.cloudflare.com/mcp',
    CloudflareServer.DNS_ANALYTICS: 'https://dns-analytics.mcp.cloudflare.com/mcp',
    CloudflareServer.DEX: 'https://dex.mcp.cloudflare.com/mcp',
    CloudflareServer.CASB: 'https://casb.mcp.cloudflare.com/mcp',
    CloudflareServer.RADAR: 'https://radar.mcp.cloudflare.com/mcp',
    CloudflareServer.BLOG: 'https://blog.mcp.cloudflare.com/mcp',
    CloudflareServer.DEMO_DAY: 'https://demo-day.mcp.cloudflare.com/mcp',
}

_ACCOUNT_KEYS = ('account_id', 'accountId')
_ZONE_KEYS = ('zone_id', 'zoneId', 'zone')
_PAGE_KEYS = ('limit', 'per_page', 'perPage', 'page_size', 'pageSize', 'first')
_API_SAFE_TOOLS = frozenset({'docs', 'search'})
_PUBLIC_SERVERS = frozenset({CloudflareServer.DOCS, CloudflareServer.BLOG, CloudflareServer.DEMO_DAY})
_TRUNCATION_MARKER = '[... Cloudflare result truncated ...]'
_OBJECT_DICT = TypeAdapter(dict[str, object])
_STRING_LIST = TypeAdapter(list[str])
_OBJECT_LIST = TypeAdapter(list[object])

_NumericBounds: TypeAlias = tuple[int | float | None, int | float | None]


def _object_dict(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return _OBJECT_DICT.validate_python(value)


def _annotations(tool: ToolsetTool[AgentDepsT]) -> dict[str, object]:
    metadata = tool.tool_def.metadata or {}
    value: object = metadata.get('annotations')
    return _object_dict(value)


def _is_read_only(server: CloudflareServer, tool: ToolsetTool[AgentDepsT], *, official_client: bool) -> bool:
    if official_client and server is CloudflareServer.API and tool.tool_def.name in _API_SAFE_TOOLS:
        return True
    return _annotations(tool).get('readOnlyHint') is True


def _properties(tool: ToolsetTool[AgentDepsT]) -> dict[str, object]:
    value: object = tool.tool_def.parameters_json_schema.get('properties')
    return _object_dict(value)


def _zone_key(tool: ToolsetTool[AgentDepsT]) -> str | None:
    properties = _properties(tool)
    return next((key for key in _ZONE_KEYS if key in properties), None)


def _account_key(tool: ToolsetTool[AgentDepsT]) -> str | None:
    properties = _properties(tool)
    return next((key for key in _ACCOUNT_KEYS if key in properties), None)


def _is_api_safe_tool(server: CloudflareServer, tool: ToolsetTool[AgentDepsT], *, official_client: bool) -> bool:
    return official_client and server is CloudflareServer.API and tool.tool_def.name in _API_SAFE_TOOLS


def _merge_bounds(left: _NumericBounds, right: _NumericBounds) -> _NumericBounds:
    left_minimum, left_maximum = left
    right_minimum, right_maximum = right
    minimum = (
        max(left_minimum, right_minimum)
        if left_minimum is not None and right_minimum is not None
        else left_minimum
        if left_minimum is not None
        else right_minimum
    )
    maximum = (
        min(left_maximum, right_maximum)
        if left_maximum is not None and right_maximum is not None
        else left_maximum
        if left_maximum is not None
        else right_maximum
    )
    return minimum, maximum


def _union_bounds(variants: list[object]) -> _NumericBounds | None:
    numeric_variants: list[_NumericBounds] = []
    for variant in variants:
        variant_schema = _object_dict(variant)
        if variant_schema.get('type') == 'null':
            continue
        variant_bounds = _numeric_bounds(variant)
        if variant_bounds is None:
            return None
        numeric_variants.append(variant_bounds)
    return numeric_variants[0] if len(numeric_variants) == 1 else None


def _numeric_bounds(schema: object) -> _NumericBounds | None:
    field = _object_dict(schema)
    minimum: int | float | None = None
    maximum: int | float | None = None
    minimum_value = field.get('minimum')
    maximum_value = field.get('maximum')
    if isinstance(minimum_value, (int, float)) and not isinstance(minimum_value, bool):
        minimum = minimum_value
    if isinstance(maximum_value, (int, float)) and not isinstance(maximum_value, bool):
        maximum = maximum_value
    for keyword in ('anyOf', 'oneOf'):
        variants = field.get(keyword)
        if not isinstance(variants, list):
            continue
        variant_bounds = _union_bounds(_OBJECT_LIST.validate_python(variants))
        if variant_bounds is None:
            return None
        minimum, maximum = _merge_bounds((minimum, maximum), variant_bounds)
    variants = field.get('allOf')
    if isinstance(variants, list):
        for variant in _OBJECT_LIST.validate_python(variants):
            variant_bounds = _numeric_bounds(variant)
            if variant_bounds is None:
                return None
            minimum, maximum = _merge_bounds((minimum, maximum), variant_bounds)
    return minimum, maximum


def _page_limit(tool: ToolsetTool[AgentDepsT], key: str, configured: int) -> int:
    bounds = _numeric_bounds(_properties(tool).get(key))
    # `get_tools` excludes unsupported schemas before either caller reaches this helper.
    if bounds is None:  # pragma: no cover
        return configured
    _, maximum = bounds
    return min(configured, int(maximum)) if maximum is not None else configured


def _supports_result_limit(tool: ToolsetTool[AgentDepsT], configured: int) -> bool:
    properties = _properties(tool)
    for key in _PAGE_KEYS:
        bounds = _numeric_bounds(properties.get(key))
        if bounds is None:
            return False
        minimum, _ = bounds
        if minimum is not None and minimum > _page_limit(tool, key, configured):
            return False
    return True


def _take_utf8_prefix(text: str, byte_limit: int) -> str:
    return text.encode('utf-8')[:byte_limit].decode('utf-8', errors='ignore')


def _bounded_text(text: str, *, max_bytes: int, max_lines: int) -> str:
    lines = text.splitlines()
    lines_exceeded = len(lines) > max_lines
    bytes_exceeded = len(text.encode('utf-8')) > max_bytes
    if not lines_exceeded and not bytes_exceeded:
        return text

    marker = _TRUNCATION_MARKER
    if max_lines == 1:
        return _take_utf8_prefix(marker, max_bytes)
    marker_bytes = len(marker.encode('utf-8')) + 1
    if marker_bytes >= max_bytes:
        return _take_utf8_prefix(marker, max_bytes)
    body_lines = lines[: max_lines - 1]
    body = _take_utf8_prefix('\n'.join(body_lines), max_bytes - marker_bytes).rstrip('\n')
    return f'{body}\n{marker}' if body else marker


class CloudflareToolset(MCPToolset[AgentDepsT]):
    """One official Cloudflare managed MCP server with client-side policy.

    The toolset selects one server, filters its tools to the read-safe set by
    default, injects configured resource boundaries, bounds result sizes, and
    sends mutation-capable tools through Pydantic AI's approval flow. It keeps
    the rest of the public `MCPToolset` surface for toolset composition.

    Use `Cloudflare` for capability instructions and agent-spec support. Use
    this class directly with toolset combinators.
    """

    def __init__(
        self,
        *,
        server: CloudflareServer | str = CloudflareServer.DOCS,
        account_id: str | None = None,
        zone_id: str | None = None,
        api_token: str | None = None,
        allow_mutations: bool = False,
        max_results: int = 20,
        max_output_bytes: int = 50 * 1024,
        max_output_lines: int = 500,
        client: MCPToolsetClient | None = None,
        id: str = 'cloudflare',
        include_instructions: bool = True,
    ) -> None:
        """Connect to one managed server with conservative execution policy.

        Args:
            server: Managed endpoint selected from Cloudflare's catalog.
            account_id: Account enforced through explicit focused-server tool
                arguments. Not supported by public servers.
            zone_id: Zone enforced through explicit tool arguments.
            api_token: Bearer token. Authenticated servers use OAuth when omitted.
            allow_mutations: Expose tools outside the read-safe set. Their calls
                still require Pydantic AI approval.
            max_results: Maximum value for recognized pagination arguments.
            max_output_bytes: Maximum serialized UTF-8 bytes returned per call.
            max_output_lines: Maximum serialized lines returned per call.
            client: Prebuilt MCP client or transport. It owns authentication and
                account selection; zone and execution policies still apply.
            id: Toolset identifier.
            include_instructions: Include remote MCP server instructions.
                `Cloudflare` uses the same flag for its capability guidance.
        """
        try:
            resolved_server = CloudflareServer(server)
        except ValueError as e:
            values = ', '.join(repr(item.value) for item in CloudflareServer)
            raise UserError(f'`server` must be one of: {values}.') from e
        for name, value in (
            ('max_results', max_results),
            ('max_output_bytes', max_output_bytes),
            ('max_output_lines', max_output_lines),
        ):
            if type(value) is not int or value <= 0:
                raise ValueError(f'{name} must be a positive integer, got {value!r}.')
        if (
            resolved_server is CloudflareServer.API
            and (account_id is not None or zone_id is not None)
            and allow_mutations
        ):
            raise UserError(
                "Cloudflare's Code Mode `execute` tool accepts arbitrary JavaScript, so this client cannot enforce "
                'an account or zone boundary on it. Select a focused server with explicit resource arguments.'
            )
        if resolved_server in _PUBLIC_SERVERS:
            if account_id is not None or zone_id is not None:
                raise UserError(f'The public Cloudflare `{resolved_server.value}` server has no account or zone scope.')
            if api_token is not None:
                raise UserError(f'The public Cloudflare `{resolved_server.value}` server does not accept `api_token`.')
        if isinstance(client, (str, Path, AnyUrl)):
            raise UserError(
                '`client` must be a prebuilt MCP client or transport, not an address. Omit it to use the selected '
                'managed server with configured OAuth or token authentication.'
            )

        resolved_client: MCPToolsetClient = client if client is not None else _SERVER_URLS[resolved_server]
        if client is not None and (api_token is not None or account_id is not None):
            raise UserError(
                '`client` owns its authentication and account selection; do not also pass `api_token` or `account_id`.'
            )
        if client is None:
            auth = None if resolved_server in _PUBLIC_SERVERS else (api_token if api_token is not None else 'oauth')
            super().__init__(resolved_client, id=id, include_instructions=include_instructions, auth=auth)
        else:
            super().__init__(resolved_client, id=id, include_instructions=include_instructions)
        transport = self.client.transport
        self._official_client = client is None or (
            resolved_server is CloudflareServer.API
            and isinstance(transport, StreamableHttpTransport)
            and str(transport.url).rstrip('/') == _SERVER_URLS[CloudflareServer.API]
        )
        self.server = resolved_server
        self.account_id = account_id
        self.zone_id = zone_id
        self.allow_mutations = allow_mutations
        self.max_results = max_results
        self.max_output_bytes = max_output_bytes
        self.max_output_lines = max_output_lines

    async def get_tools(self, ctx: RunContext[AgentDepsT]) -> dict[str, ToolsetTool[AgentDepsT]]:
        tools = await super().get_tools(ctx)
        selected: dict[str, ToolsetTool[AgentDepsT]] = {}
        for name, tool in tools.items():
            read_only = _is_read_only(self.server, tool, official_client=self._official_client)
            if not read_only and not self.allow_mutations:
                continue
            api_safe = _is_api_safe_tool(self.server, tool, official_client=self._official_client)
            if self.account_id is not None and not api_safe and _account_key(tool) is None:
                continue
            if self.zone_id is not None and not api_safe and _zone_key(tool) is None:
                continue
            if not _supports_result_limit(tool, self.max_results):
                continue
            selected[name] = replace(
                tool, tool_def=replace(tool.tool_def, parameters_json_schema=self._bounded_schema(tool))
            )
        return selected

    def _bounded_schema(self, tool: ToolsetTool[AgentDepsT]) -> ObjectJsonSchema:
        schema = dict(tool.tool_def.parameters_json_schema)
        properties = _properties(tool)
        bounded_properties: ObjectJsonSchema = dict(properties)
        for key in _PAGE_KEYS:
            value: object = properties.get(key)
            field = _object_dict(value)
            if not field:
                continue
            existing_maximum = field.get('maximum')
            effective_limit = _page_limit(tool, key, self.max_results)
            if not isinstance(existing_maximum, (int, float)) or existing_maximum > effective_limit:
                field['maximum'] = effective_limit
            existing_default = field.get('default')
            if isinstance(existing_default, (int, float)) and existing_default > effective_limit:
                field['default'] = effective_limit
            bounded_properties[key] = field
        schema['properties'] = bounded_properties
        required = schema.get('required')
        if isinstance(required, list):
            required_keys = _STRING_LIST.validate_python(required)
            injected = {
                key
                for key in (
                    _account_key(tool) if self.account_id is not None else None,
                    _zone_key(tool) if self.zone_id is not None else None,
                )
                if key is not None
            }
            schema['required'] = [key for key in required_keys if key not in injected]
        return schema

    def _scoped_args(self, tool_args: dict[str, Any], tool: ToolsetTool[AgentDepsT]) -> dict[str, Any]:
        args = dict(tool_args)
        properties = _properties(tool)
        account_key = _account_key(tool)
        if self.account_id is not None and account_key is not None:
            if args.get(account_key, self.account_id) != self.account_id:
                raise ModelRetry('The requested operation is outside the configured Cloudflare account boundary.')
            args[account_key] = self.account_id
        if self.zone_id is not None and not _is_api_safe_tool(self.server, tool, official_client=self._official_client):
            key = _zone_key(tool)
            if key is None:  # pragma: no cover
                raise ModelRetry('The requested tool does not expose a Cloudflare zone boundary.')
            if args.get(key, self.zone_id) != self.zone_id:
                raise ModelRetry('The requested operation is outside the configured Cloudflare zone boundary.')
            args[key] = self.zone_id
        for key in _PAGE_KEYS:
            if key not in properties:
                continue
            value = args.get(key)
            effective_limit = _page_limit(tool, key, self.max_results)
            if isinstance(value, (int, float)) and value > effective_limit:
                raise ModelRetry(f'`{key}` cannot exceed the configured Cloudflare result limit of {effective_limit}.')
            if value is None:
                args[key] = effective_limit
        return args

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext[AgentDepsT],
        tool: ToolsetTool[AgentDepsT],
    ) -> Any:
        if not _is_read_only(self.server, tool, official_client=self._official_client) and not ctx.tool_call_approved:
            raise ApprovalRequired
        result = await super().call_tool(name, self._scoped_args(tool_args, tool), ctx, tool)
        if isinstance(result, str):
            return _bounded_text(result, max_bytes=self.max_output_bytes, max_lines=self.max_output_lines)

        serialized = to_json(result).decode('utf-8', errors='replace')
        if (
            len(serialized.encode('utf-8')) <= self.max_output_bytes
            and len(serialized.splitlines()) <= self.max_output_lines
        ):
            return result
        return _bounded_text(_TRUNCATION_MARKER, max_bytes=self.max_output_bytes, max_lines=self.max_output_lines)
