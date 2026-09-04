"""Behavioral tests for Cloudflare's managed MCP servers."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import pytest
from fastmcp import Client
from fastmcp.client.auth import BearerAuth, OAuth
from fastmcp.client.transports import StreamableHttpTransport
from pydantic_ai import Agent
from pydantic_ai.exceptions import ApprovalRequired, ModelRetry, UserError
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.messages import (
    DeferredToolRequests,
    DeferredToolResults,
    ModelMessage,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from pydantic_ai_harness.cloudflare import Cloudflare, CloudflareServer, CloudflareToolset

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP
    from pydantic_ai.tools import RunContext
    from pydantic_ai.toolsets import ToolsetTool

pytestmark = pytest.mark.anyio


def _transport(toolset: CloudflareToolset[None]) -> StreamableHttpTransport:
    transport = toolset.client.transport
    assert isinstance(transport, StreamableHttpTransport)
    return transport


_SERVER_URLS = [
    (CloudflareServer.API, 'https://mcp.cloudflare.com/mcp'),
    (CloudflareServer.DOCS, 'https://docs.mcp.cloudflare.com/mcp'),
    (CloudflareServer.WORKERS_BINDINGS, 'https://bindings.mcp.cloudflare.com/mcp'),
    (CloudflareServer.WORKERS_BUILDS, 'https://builds.mcp.cloudflare.com/mcp'),
    (CloudflareServer.OBSERVABILITY, 'https://observability.mcp.cloudflare.com/mcp'),
    (CloudflareServer.CONTAINERS, 'https://containers.mcp.cloudflare.com/mcp'),
    (CloudflareServer.BROWSER, 'https://browser.mcp.cloudflare.com/mcp'),
    (CloudflareServer.LOGPUSH, 'https://logs.mcp.cloudflare.com/mcp'),
    (CloudflareServer.AI_GATEWAY, 'https://ai-gateway.mcp.cloudflare.com/mcp'),
    (CloudflareServer.AUTORAG, 'https://autorag.mcp.cloudflare.com/mcp'),
    (CloudflareServer.AUDIT_LOGS, 'https://auditlogs.mcp.cloudflare.com/mcp'),
    (CloudflareServer.DNS_ANALYTICS, 'https://dns-analytics.mcp.cloudflare.com/mcp'),
    (CloudflareServer.DEX, 'https://dex.mcp.cloudflare.com/mcp'),
    (CloudflareServer.CASB, 'https://casb.mcp.cloudflare.com/mcp'),
    (CloudflareServer.RADAR, 'https://radar.mcp.cloudflare.com/mcp'),
    (CloudflareServer.BLOG, 'https://blog.mcp.cloudflare.com/mcp'),
    (CloudflareServer.DEMO_DAY, 'https://demo-day.mcp.cloudflare.com/mcp'),
]


class TestCloudflareToolset:
    def test_defaults_to_public_docs_without_auth(self) -> None:
        transport = _transport(CloudflareToolset[None]())
        assert transport.url == 'https://docs.mcp.cloudflare.com/mcp'
        assert transport.auth is None

    @pytest.mark.parametrize(('server', 'url'), _SERVER_URLS)
    def test_official_server_catalog(self, server: CloudflareServer, url: str) -> None:
        public = {CloudflareServer.DOCS, CloudflareServer.BLOG, CloudflareServer.DEMO_DAY}
        toolset = CloudflareToolset[None](server=server, api_token=None if server in public else 'secret')
        assert _transport(toolset).url == url

    def test_authenticated_server_defaults_to_oauth(self) -> None:
        with pytest.warns(UserWarning, match='in-memory token storage'):
            toolset = CloudflareToolset[None](server=CloudflareServer.DNS_ANALYTICS)
        assert isinstance(_transport(toolset).auth, OAuth)

    def test_token_auth_does_not_send_an_account_override_header(self) -> None:
        toolset = CloudflareToolset[None](server=CloudflareServer.DNS_ANALYTICS, api_token='secret', account_id='a1')
        transport = _transport(toolset)
        assert isinstance(transport.auth, BearerAuth)
        assert 'cf-account-id' not in transport.headers
        assert 'secret' not in repr(toolset)

    async def test_safe_default_exposes_only_annotated_reads(
        self, focused_server: FastMCP, run_context: RunContext[None]
    ) -> None:
        toolset = CloudflareToolset(client=focused_server, server=CloudflareServer.DNS_ANALYTICS)
        async with toolset:
            tools = await toolset.get_tools(run_context)
        assert set(tools) == {'list_records'}

    async def test_custom_clients_cannot_claim_api_safety_by_tool_name(
        self, untrusted_api_server: FastMCP, run_context: RunContext[None]
    ) -> None:
        toolset = CloudflareToolset(client=untrusted_api_server, server=CloudflareServer.API)
        async with toolset:
            assert await toolset.get_tools(run_context) == {}

    async def test_prebuilt_client_targeting_official_api_uses_verified_safe_names(
        self,
        untrusted_api_server: FastMCP,
        run_context: RunContext[None],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        source = CloudflareToolset[None](
            client=untrusted_api_server,
            server=CloudflareServer.API,
            allow_mutations=True,
        )
        async with source:
            source_tools = await source.get_tools(run_context)

        async def fake_get_tools(_toolset: MCPToolset[None], _ctx: RunContext[None]) -> dict[str, ToolsetTool[None]]:
            return source_tools

        monkeypatch.setattr(MCPToolset, 'get_tools', fake_get_tools)
        toolset = CloudflareToolset[None](
            client=Client('https://mcp.cloudflare.com/mcp', auth='secret'),
            server=CloudflareServer.API,
        )
        assert set(await toolset.get_tools(run_context)) == {'search'}

    async def test_zone_boundary_is_injected_and_mismatch_is_rejected(
        self, focused_server: FastMCP, run_context: RunContext[None]
    ) -> None:
        toolset = CloudflareToolset(client=focused_server, server=CloudflareServer.DNS_ANALYTICS, zone_id='z1')
        async with toolset:
            tools = await toolset.get_tools(run_context)
            assert 'zoneId' not in tools['list_records'].tool_def.parameters_json_schema['required']
            result = await toolset.call_tool('list_records', {'account_id': 'a1'}, run_context, tools['list_records'])
            with pytest.raises(ModelRetry, match='outside the configured Cloudflare zone'):
                await toolset.call_tool(
                    'list_records', {'account_id': 'a1', 'zoneId': 'other'}, run_context, tools['list_records']
                )
        assert str(result).startswith('a1:z1:0')

    async def test_alternate_account_and_zone_keys_are_pinned(
        self,
        alternate_schema_server: FastMCP,
        run_context: RunContext[None],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        source = CloudflareToolset[None](client=alternate_schema_server, server=CloudflareServer.DNS_ANALYTICS)
        async with source:
            source_tools = await source.get_tools(run_context)

        captured_args: dict[str, object] = {}

        async def fake_get_tools(_toolset: MCPToolset[None], _ctx: RunContext[None]) -> dict[str, ToolsetTool[None]]:
            return source_tools

        async def fake_call_tool(
            _toolset: MCPToolset[None],
            _name: str,
            tool_args: dict[str, object],
            _ctx: RunContext[None],
            _tool: ToolsetTool[None],
        ) -> object:
            captured_args.update(tool_args)
            return 'scoped'

        monkeypatch.setattr(MCPToolset, 'get_tools', fake_get_tools)
        monkeypatch.setattr(MCPToolset, 'call_tool', fake_call_tool)
        toolset = CloudflareToolset[None](
            server=CloudflareServer.DNS_ANALYTICS,
            api_token='secret',
            account_id='a1',
            zone_id='z1',
        )
        tools = await toolset.get_tools(run_context)
        assert tools['camel_scope'].tool_def.parameters_json_schema['required'] == []
        assert await toolset.call_tool('camel_scope', {}, run_context, tools['camel_scope']) == 'scoped'
        assert captured_args == {'accountId': 'a1', 'zone': 'z1'}
        with pytest.raises(ModelRetry, match='outside the configured Cloudflare account'):
            await toolset.call_tool('camel_scope', {'accountId': 'other'}, run_context, tools['camel_scope'])

    async def test_zone_boundary_hides_tools_without_a_zone_argument(
        self, focused_server: FastMCP, run_context: RunContext[None]
    ) -> None:
        toolset = CloudflareToolset(
            client=focused_server,
            server=CloudflareServer.DNS_ANALYTICS,
            zone_id='z1',
            allow_mutations=True,
        )
        async with toolset:
            tools = await toolset.get_tools(run_context)
        assert set(tools) == {'list_records', 'delete_record'}

    async def test_mutations_are_explicit_and_use_core_approval(
        self, focused_server: FastMCP, run_context: RunContext[None]
    ) -> None:
        toolset = CloudflareToolset(
            client=focused_server,
            server=CloudflareServer.DNS_ANALYTICS,
            zone_id='z1',
            allow_mutations=True,
        )
        async with toolset:
            tools = await toolset.get_tools(run_context)
            with pytest.raises(ApprovalRequired):
                await toolset.call_tool(
                    'delete_record', {'account_id': 'a1', 'record_id': 'r1'}, run_context, tools['delete_record']
                )

    async def test_pagination_schema_and_calls_are_bounded(
        self, focused_server: FastMCP, run_context: RunContext[None]
    ) -> None:
        toolset = CloudflareToolset(client=focused_server, max_results=7)
        async with toolset:
            tools = await toolset.get_tools(run_context)
            schema = tools['list_records'].tool_def.parameters_json_schema
            assert schema['properties']['limit']['maximum'] == 7
            assert schema['properties']['limit']['default'] == 7
            result = await toolset.call_tool(
                'list_records', {'account_id': 'a1', 'zoneId': 'z1'}, run_context, tools['list_records']
            )
            with pytest.raises(ModelRetry, match='cannot exceed.*7'):
                await toolset.call_tool(
                    'list_records', {'account_id': 'a1', 'zoneId': 'z1', 'limit': 8}, run_context, tools['list_records']
                )
        assert len(str(result).splitlines()) == 7

    async def test_server_maximum_wins_and_incompatible_minimum_hides_tool(
        self, alternate_schema_server: FastMCP, run_context: RunContext[None]
    ) -> None:
        toolset = CloudflareToolset(client=alternate_schema_server, max_results=4)
        async with toolset:
            tools = await toolset.get_tools(run_context)
            assert 'minimum_too_large' not in tools
            assert 'ambiguous_limit' not in tools
            schema = tools['limited_records'].tool_def.parameters_json_schema
            assert schema['properties']['limit']['maximum'] == 3
            result = await toolset.call_tool('limited_records', {}, run_context, tools['limited_records'])
            assert await toolset.call_tool('simple_limited', {'limit': 2}, run_context, tools['simple_limited']) == '2'
        assert result == '0,1,2'

    async def test_all_of_bounds_and_nested_ambiguous_unions(
        self,
        alternate_schema_server: FastMCP,
        run_context: RunContext[None],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        source = CloudflareToolset[None](client=alternate_schema_server)
        async with source:
            source_tools = await source.get_tools(run_context)
        base = source_tools['simple_limited']
        base_schema = dict(base.tool_def.parameters_json_schema)
        all_of_schema = dict(base_schema)
        all_of_schema['properties'] = {
            'limit': {
                'type': 'integer',
                'allOf': [{'minimum': 1}, {'maximum': 3}],
                'default': 10,
            }
        }
        nested_union_schema = dict(base_schema)
        nested_union_schema['properties'] = {
            'limit': {
                'anyOf': [
                    {'anyOf': [{'type': 'integer', 'maximum': 3}, {'type': 'integer', 'minimum': 10}]},
                    {'type': 'null'},
                ]
            }
        }
        ambiguous_all_of_schema = dict(base_schema)
        ambiguous_all_of_schema['properties'] = {
            'limit': {'allOf': [{'anyOf': [{'type': 'integer', 'maximum': 3}, {'type': 'integer', 'minimum': 10}]}]}
        }
        all_of_tool = replace(base, tool_def=replace(base.tool_def, parameters_json_schema=all_of_schema))
        nested_union_tool = replace(
            base,
            tool_def=replace(base.tool_def, name='nested_union', parameters_json_schema=nested_union_schema),
        )
        ambiguous_all_of_tool = replace(
            base,
            tool_def=replace(
                base.tool_def,
                name='ambiguous_all_of',
                parameters_json_schema=ambiguous_all_of_schema,
            ),
        )

        async def fake_get_tools(_toolset: MCPToolset[None], _ctx: RunContext[None]) -> dict[str, ToolsetTool[None]]:
            return {
                'simple_limited': all_of_tool,
                'nested_union': nested_union_tool,
                'ambiguous_all_of': ambiguous_all_of_tool,
            }

        monkeypatch.setattr(MCPToolset, 'get_tools', fake_get_tools)
        toolset = CloudflareToolset[None](server=CloudflareServer.DNS_ANALYTICS, api_token='secret', max_results=4)
        tools = await toolset.get_tools(run_context)
        assert set(tools) == {'simple_limited'}
        limit_schema = tools['simple_limited'].tool_def.parameters_json_schema['properties']['limit']
        assert limit_schema['maximum'] == 3
        assert limit_schema['default'] == 3

    async def test_text_within_limits_is_unchanged(
        self, alternate_schema_server: FastMCP, run_context: RunContext[None]
    ) -> None:
        toolset = CloudflareToolset(client=alternate_schema_server)
        async with toolset:
            tools = await toolset.get_tools(run_context)
            result = await toolset.call_tool('exact_text', {}, run_context, tools['exact_text'])
        assert result == 'a\r\nb\n'

        bounded = CloudflareToolset(client=alternate_schema_server, max_output_bytes=4)
        async with bounded:
            tools = await bounded.get_tools(run_context)
            result = await bounded.call_tool('exact_text', {}, run_context, tools['exact_text'])
        assert isinstance(result, str)
        assert len(result.encode()) <= 4

    async def test_text_result_caps_include_the_marker(
        self, focused_server: FastMCP, run_context: RunContext[None]
    ) -> None:
        toolset = CloudflareToolset(client=focused_server, max_output_bytes=90, max_output_lines=3)
        async with toolset:
            tools = await toolset.get_tools(run_context)
            result = await toolset.call_tool(
                'list_records', {'account_id': 'a1', 'zoneId': 'z1'}, run_context, tools['list_records']
            )
        assert isinstance(result, str)
        assert len(result.encode()) <= 90
        assert len(result.splitlines()) <= 3
        assert 'truncated' in result

    @pytest.mark.parametrize(('max_bytes', 'max_lines'), [(40, 1), (5, 2), (40, 2)])
    async def test_marker_only_truncation_paths(
        self,
        alternate_schema_server: FastMCP,
        run_context: RunContext[None],
        max_bytes: int,
        max_lines: int,
    ) -> None:
        toolset = CloudflareToolset(
            client=alternate_schema_server,
            max_output_bytes=max_bytes,
            max_output_lines=max_lines,
        )
        async with toolset:
            tools = await toolset.get_tools(run_context)
            result = await toolset.call_tool('emoji_read', {}, run_context, tools['emoji_read'])
        assert isinstance(result, str)
        assert len(result.encode()) <= max_bytes

    async def test_structured_results_are_preserved_or_replaced_whole(
        self, alternate_schema_server: FastMCP, run_context: RunContext[None]
    ) -> None:
        toolset = CloudflareToolset(client=alternate_schema_server)
        async with toolset:
            tools = await toolset.get_tools(run_context)
            result = await toolset.call_tool('structured_read', {}, run_context, tools['structured_read'])
        assert result == {'count': 2}

        bounded = CloudflareToolset(client=alternate_schema_server, max_output_bytes=2)
        async with bounded:
            tools = await bounded.get_tools(run_context)
            result = await bounded.call_tool('structured_read', {}, run_context, tools['structured_read'])
        assert isinstance(result, str)
        assert len(result.encode()) <= 2

    async def test_custom_api_client_must_expose_resource_arguments(
        self, api_server: FastMCP, run_context: RunContext[None]
    ) -> None:
        toolset = CloudflareToolset(client=api_server, server=CloudflareServer.API, zone_id='z1')
        async with toolset:
            assert await toolset.get_tools(run_context) == {}

    @pytest.mark.parametrize('scope', [{'account_id': 'a1'}, {'zone_id': 'z1'}])
    def test_resource_boundary_rejects_api_execution(self, scope: dict[str, str]) -> None:
        with pytest.raises(UserError, match='cannot enforce an account or zone boundary'):
            CloudflareToolset(server=CloudflareServer.API, allow_mutations=True, **scope)  # pyright: ignore[reportArgumentType]

    def test_public_server_rejects_resource_boundaries_and_tokens(self) -> None:
        with pytest.raises(UserError, match='has no account or zone scope'):
            CloudflareToolset(server=CloudflareServer.DOCS, account_id='a1')
        with pytest.raises(UserError, match='does not accept `api_token`'):
            CloudflareToolset(server=CloudflareServer.DOCS, api_token='secret')

    def test_prebuilt_client_owns_authentication(self, focused_server: FastMCP) -> None:
        with pytest.raises(UserError, match='client.*owns its authentication'):
            CloudflareToolset(client=focused_server, server=CloudflareServer.DNS_ANALYTICS, api_token='secret')
        with pytest.raises(UserError, match='client.*account selection'):
            CloudflareToolset(client=focused_server, server=CloudflareServer.DNS_ANALYTICS, account_id='a1')

    def test_client_address_is_rejected(self) -> None:
        with pytest.raises(UserError, match='prebuilt MCP client or transport'):
            CloudflareToolset(client='https://mcp.cloudflare.com/mcp', server=CloudflareServer.API)

    @pytest.mark.parametrize('name', ['max_results', 'max_output_bytes', 'max_output_lines'])
    @pytest.mark.parametrize('value', [0, True])
    def test_invalid_limits(self, name: str, value: int) -> None:
        with pytest.raises(ValueError, match=name):
            CloudflareToolset(**{name: value})  # pyright: ignore[reportArgumentType]

    def test_invalid_server(self) -> None:
        with pytest.raises(UserError, match='server.*must be one of'):
            CloudflareToolset(server='missing')

    async def test_remote_instructions_can_be_disabled(
        self, focused_server: FastMCP, run_context: RunContext[None]
    ) -> None:
        enabled = CloudflareToolset(client=focused_server)
        async with enabled:
            assert await enabled.get_instructions(run_context) is not None
        disabled = CloudflareToolset(client=focused_server, include_instructions=False)
        async with disabled:
            assert await disabled.get_instructions(run_context) is None


class TestCloudflareCapability:
    def test_secret_is_hidden(self) -> None:
        capability = Cloudflare(api_token='secret', server=CloudflareServer.DNS_ANALYTICS)
        assert 'secret' not in repr(capability)
        assert capability.server is CloudflareServer.DNS_ANALYTICS

    async def test_agent_uses_public_capability(self, api_server: FastMCP) -> None:
        def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            if any(isinstance(part, ToolReturnPart) for message in messages for part in message.parts):
                return ModelResponse(parts=[TextPart('done')])
            assert {tool.name for tool in info.function_tools} == {'docs', 'search'}
            return ModelResponse(parts=[ToolCallPart('docs', {'query': 'cache'})])

        agent = Agent(FunctionModel(model), capabilities=[Cloudflare(client=api_server, server=CloudflareServer.API)])
        result = await agent.run('look it up')
        assert result.output == 'done'

    async def test_agent_mutation_runs_after_deferred_approval(self, focused_server: FastMCP) -> None:
        def model(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
            if any(isinstance(part, ToolReturnPart) for message in messages for part in message.parts):
                return ModelResponse(parts=[TextPart('deleted')])
            return ModelResponse(parts=[ToolCallPart('delete_record', {'account_id': 'a1', 'record_id': 'r1'})])

        agent = Agent(
            FunctionModel(model),
            capabilities=[
                Cloudflare(
                    client=focused_server,
                    server=CloudflareServer.DNS_ANALYTICS,
                    zone_id='z1',
                    allow_mutations=True,
                )
            ],
            output_type=[str, DeferredToolRequests],
        )
        pending = await agent.run('delete')
        assert isinstance(pending.output, DeferredToolRequests)
        approval = pending.output.approvals[0]
        resumed = await agent.run(
            message_history=pending.all_messages(),
            deferred_tool_results=DeferredToolResults(approvals={approval.tool_call_id: True}),
        )
        assert resumed.output == 'deleted'
        returns = [
            part for message in resumed.all_messages() for part in message.parts if isinstance(part, ToolReturnPart)
        ]
        assert any(part.content == 'deleted:a1:z1:r1' for part in returns)

    def test_instructions_can_be_disabled(self) -> None:
        assert Cloudflare(include_instructions=False).get_instructions() is None

    def test_scoped_id_and_instructions_do_not_expose_identifiers(self) -> None:
        capability = Cloudflare(
            server=CloudflareServer.DNS_ANALYTICS,
            account_id='account-secret',
            zone_id='zone-secret',
        )
        assert capability.id is not None
        assert capability.id.startswith('cloudflare-dns_analytics-')
        instructions = capability.get_instructions()
        assert instructions is not None
        assert 'configured account and zone boundary' in instructions
        assert 'account-secret' not in instructions
        assert 'zone-secret' not in instructions

    def test_custom_id_is_preserved(self) -> None:
        assert Cloudflare(id='my-cloudflare').id == 'my-cloudflare'

    def test_agent_spec(self) -> None:
        agent = Agent.from_spec(
            {'capabilities': [{'Cloudflare': {'server': 'docs'}}]},
            custom_capability_types=[Cloudflare],
            model=TestModel(),
        )
        assert isinstance(agent, Agent)
