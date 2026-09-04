"""Behavioral tests for the public Supabase capability."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastmcp.client.auth import OAuth
from fastmcp.client.auth.bearer import BearerAuth
from fastmcp.client.transports import StreamableHttpTransport
from pydantic_ai import Agent, DeferredToolRequests, DeferredToolResults
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.exceptions import UserError
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from pydantic_ai_harness.supabase import Supabase, SupabaseFeature

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP
    from pydantic_ai.toolsets import AbstractToolset

pytestmark = pytest.mark.anyio


def _mcp_toolset(toolset: AbstractToolset[None]) -> MCPToolset[None]:
    leaves: list[MCPToolset[None]] = []

    def capture(candidate: AbstractToolset[None]) -> None:
        if isinstance(candidate, MCPToolset):
            leaves.append(candidate)

    toolset.apply(capture)
    assert len(leaves) == 1
    return leaves[0]


def _tool_names(model: TestModel) -> set[str]:
    params = model.last_model_request_parameters
    assert params is not None
    return {tool.name for tool in params.function_tools}


class TestSupabase:
    def test_default_remote_configuration(self):
        with pytest.warns(UserWarning, match='in-memory token storage'):
            capability = Supabase(project_ref='abcdefghijklmnopqrst')
            leaf = _mcp_toolset(capability.get_toolset())

        transport = leaf.client.transport
        assert isinstance(transport, StreamableHttpTransport)
        assert transport.url == (
            'https://mcp.supabase.com/mcp?project_ref=abcdefghijklmnopqrst'
            '&features=database%2Cdebugging%2Cdevelopment%2Cdocs&read_only=true'
        )
        assert isinstance(transport.auth, OAuth)
        assert capability.id == 'supabase-abcdefghijklmnopqrst'

    def test_personal_access_token_authentication_is_hidden(self):
        capability = Supabase(project_ref='abcdefghijklmnopqrst', access_token='sbp_secret')
        leaf = _mcp_toolset(capability.get_toolset())
        transport = leaf.client.transport
        assert isinstance(transport, StreamableHttpTransport)
        assert isinstance(transport.auth, BearerAuth)
        assert 'sbp_secret' not in repr(capability)
        assert 'sbp_secret' not in repr(leaf)

    def test_writable_url_uses_the_server_default(self):
        capability = Supabase(project_ref='abcdefghijklmnopqrst', access_token='token', read_only=False)
        transport = _mcp_toolset(capability.get_toolset()).client.transport
        assert isinstance(transport, StreamableHttpTransport)
        assert 'read_only' not in transport.url

    def test_agent_spec_schema_excludes_runtime_client(self):
        schema = AgentSpec.model_json_schema_with_capabilities([Supabase])
        properties = schema['$defs']['spec_params_Supabase']['properties']
        assert 'client' not in properties
        assert 'access_token' not in properties
        assert Supabase.get_serialization_name() == 'Supabase'

    @pytest.mark.parametrize('project_ref', ['', 'has spaces', 'a/b', 'a?b'])
    def test_project_ref_must_be_url_safe(self, project_ref: str):
        with pytest.raises(UserError, match='project_ref'):
            Supabase(project_ref=project_ref, access_token='token')

    @pytest.mark.parametrize('features', [(), ('database', 'database')])
    def test_feature_groups_are_deliberate(self, features: tuple[SupabaseFeature, ...]):
        with pytest.raises(UserError, match='features'):
            Supabase(project_ref='abcdefghijklmnopqrst', access_token='token', features=features)

    def test_account_feature_is_rejected(self):
        with pytest.raises(UserError, match='features'):
            Supabase(
                project_ref='abcdefghijklmnopqrst',
                access_token='token',
                features=('account',),  # pyright: ignore[reportArgumentType]
            )

    async def test_read_only_agent_tools(self, supabase_server: FastMCP):
        model = TestModel()
        agent = Agent(model, capabilities=[Supabase(project_ref='dev-project')])

        await agent.run('Inspect the project')

        assert _tool_names(model) == {'list_tables', 'execute_sql', 'query_logs', 'get_project_url', 'search_docs'}

    async def test_feature_groups_filter_the_public_agent_surface(self, supabase_server: FastMCP):
        model = TestModel()
        agent = Agent(
            model,
            capabilities=[Supabase(project_ref='dev-project', features=('docs',))],
        )

        await agent.run('Search the docs')

        assert _tool_names(model) == {'search_docs'}

    async def test_optional_feature_groups_remain_read_only(self, supabase_server: FastMCP):
        model = TestModel()
        agent = Agent(
            model,
            capabilities=[
                Supabase(
                    project_ref='dev-project',
                    features=('functions', 'storage', 'branching'),
                )
            ],
        )

        await agent.run('Inspect optional features')

        assert _tool_names(model) == {'list_edge_functions', 'list_storage_buckets'}

    async def test_writes_require_approval(self, supabase_server: FastMCP, calls: list[str]):
        def call_sql(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            if not any(isinstance(part, ToolCallPart) for message in messages for part in message.parts):
                return ModelResponse(parts=[ToolCallPart('execute_sql', {'query': 'delete from todos'})])
            return ModelResponse(parts=[TextPart('done')])

        agent = Agent(
            FunctionModel(call_sql),
            capabilities=[Supabase(project_ref='dev-project', read_only=False)],
            output_type=[str, DeferredToolRequests],
        )
        result = await agent.run('Delete the todos')

        assert isinstance(result.output, DeferredToolRequests)
        assert [call.tool_name for call in result.output.approvals] == ['execute_sql']
        assert calls == []

        call_id = result.output.approvals[0].tool_call_id
        resumed = await agent.run(
            message_history=result.all_messages(),
            deferred_tool_results=DeferredToolResults(approvals={call_id: True}),
        )
        assert resumed.output == 'done'
        assert calls == ['execute_sql:delete from todos']

    async def test_schema_mutation_requires_approval(self, supabase_server: FastMCP, calls: list[str]):
        model = TestModel(call_tools=['apply_migration'])
        agent = Agent(
            model,
            capabilities=[Supabase(project_ref='dev-project', read_only=False)],
            output_type=[str, DeferredToolRequests],
        )

        result = await agent.run('Add a column')

        assert isinstance(result.output, DeferredToolRequests)
        assert [call.tool_name for call in result.output.approvals] == ['apply_migration']
        assert calls == []

    async def test_write_approval_composes_with_stricter_caller_policy(
        self, supabase_server: FastMCP, calls: list[str]
    ):
        capability = Supabase(project_ref='dev-project', read_only=False)
        toolset = capability.get_toolset().approval_required(lambda _ctx, tool, _args: tool.name == 'list_tables')
        model = TestModel(call_tools=['list_tables'])
        agent = Agent(model, toolsets=[toolset], output_type=[str, DeferredToolRequests])

        result = await agent.run('List tables')

        assert isinstance(result.output, DeferredToolRequests)
        assert [call.tool_name for call in result.output.approvals] == ['list_tables']
        assert calls == []
