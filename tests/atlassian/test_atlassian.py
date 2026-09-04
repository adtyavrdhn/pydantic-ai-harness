"""Behavioral tests for Atlassian's public capability and toolset boundaries."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import TYPE_CHECKING

import pytest

pytest.importorskip('fastmcp')

from fastmcp.client.transports import StreamableHttpTransport
from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.exceptions import UserError
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import RunContext

from pydantic_ai_harness.atlassian import ATLASSIAN_MCP_URL, Atlassian, AtlassianToolset

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

pytestmark = pytest.mark.anyio


def _tool_call_names(messages: list[ModelMessage]) -> set[str]:
    return {part.tool_name for message in messages for part in message.parts if isinstance(part, ToolCallPart)}


class TestAtlassian:
    def test_default_connection_uses_v2_flat_catalogue_and_oauth(self):
        with pytest.warns(UserWarning, match='in-memory token storage'):
            toolset = AtlassianToolset[None](cloud_id='site-1')
        transport = toolset.client.transport
        assert isinstance(transport, StreamableHttpTransport)
        assert transport.url == ATLASSIAN_MCP_URL
        assert transport.auth is not None

    def test_secret_and_injected_client_are_hidden_from_repr(self):
        capability = Atlassian(
            cloud_id='site-1',
            authorization_token='bearer-secret',
            client='https://user:url-secret@example.com/mcp?token=query-secret',
        )
        representation = repr(capability)
        assert 'bearer-secret' not in representation
        assert 'url-secret' not in representation
        assert 'query-secret' not in representation

    @pytest.mark.parametrize(
        ('build', 'match'),
        [
            (lambda: AtlassianToolset(cloud_id=''), '`cloud_id` must not be empty'),
            (lambda: AtlassianToolset(cloud_id='site-1', products=()), '`products` must contain'),
            (
                lambda: AtlassianToolset(cloud_id='site-1', products=('compass',)),  # pyright: ignore[reportArgumentType]
                'Unknown Atlassian product',
            ),
            (
                lambda: AtlassianToolset(cloud_id='site-1', access='admin'),  # pyright: ignore[reportArgumentType]
                '`access` must be',
            ),
        ],
    )
    def test_invalid_configuration_fails_at_construction(self, build: Callable[[], object], match: str):
        with pytest.raises(UserError, match=match):
            build()

    def test_agent_spec_excludes_runtime_client(self):
        schema = AgentSpec.model_json_schema_with_capabilities([Atlassian])
        assert '"client"' not in json.dumps(schema, sort_keys=True)
        with pytest.warns(UserWarning, match='in-memory token storage'):
            agent = Agent.from_spec(
                {'capabilities': [{'Atlassian': {'cloud_id': 'site-1', 'products': 'confluence'}}]},
                custom_capability_types=[Atlassian],
                model=TestModel(),
            )
        assert isinstance(agent, Agent)

    def test_ids_preserve_site_identity(self, atlassian_server: FastMCP):
        first = Atlassian(cloud_id='site-1', client=atlassian_server)
        second = Atlassian(cloud_id='site-2', client=atlassian_server)
        assert (first.id, second.id) == ('atlassian-site-1', 'atlassian-site-2')
        Agent(TestModel(), capabilities=[first, second])

    async def test_default_agent_path_exposes_only_reviewed_jira_reads(self, atlassian_server: FastMCP):
        capability = Atlassian(cloud_id='site-1', client=atlassian_server)

        def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            names = {tool.name for tool in info.function_tools}
            assert names == {'atlassianUserInfo', 'getJiraIssue', 'searchJiraIssuesUsingJql'}
            if not any(isinstance(part, ToolReturnPart) for message in messages for part in message.parts):
                return ModelResponse(
                    parts=[ToolCallPart(tool_name='getJiraIssue', args={'cloudId': 'site-1', 'issueIdOrKey': 'ENG-42'})]
                )
            return ModelResponse(parts=[TextPart('done')])

        result = await Agent(FunctionModel(model), capabilities=[capability]).run('Read ENG-42')
        assert result.output == 'done'
        assert _tool_call_names(result.all_messages()) == {'getJiraIssue'}
        first_request = result.all_messages()[0]
        assert isinstance(first_request, ModelRequest)
        assert 'cloudId `site-1`' in (first_request.instructions or '')

    async def test_product_selection_is_exact(self, atlassian_server: FastMCP, run_context: RunContext[None]):
        toolset = AtlassianToolset(
            cloud_id='site-1',
            products=('confluence', 'bitbucket'),
            client=atlassian_server,
        )
        async with toolset:
            tools = await toolset.get_tools(run_context)
        assert set(tools) == {'atlassianUserInfo', 'getConfluenceContent', 'getBitbucketRepository'}
        assert tools['getConfluenceContent'].tool_def.metadata == {
            'meta': None,
            'annotations': None,
            'task': False,
            'atlassian_product': 'confluence',
            'atlassian_access': 'read',
            'atlassian_cloud_id': 'site-1',
        }

    async def test_write_access_requires_approval_before_server_call(self, atlassian_server: FastMCP):
        capability = Atlassian(cloud_id='site-1', access='read_write', client=atlassian_server)

        def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name='createJiraIssue',
                        args={'cloudId': 'site-1', 'projectKey': 'ENG', 'summary': 'Add SSO'},
                    )
                ]
            )

        result = await Agent(
            FunctionModel(model), capabilities=[capability], output_type=[str, DeferredToolRequests]
        ).run('Create an issue')
        assert isinstance(result.output, DeferredToolRequests)
        assert [call.tool_name for call in result.output.approvals] == ['createJiraIssue']

    async def test_site_scope_rejects_cross_tenant_call(self, atlassian_server: FastMCP, run_context: RunContext[None]):
        toolset = AtlassianToolset(cloud_id='site-1', client=atlassian_server)
        async with toolset:
            tools = await toolset.get_tools(run_context)
            with pytest.raises(UserError, match="scoped to cloudId 'site-1'.*'site-2'"):
                await toolset.call_tool(
                    'getJiraIssue',
                    {'cloudId': 'site-2', 'issueIdOrKey': 'ENG-42'},
                    run_context,
                    tools['getJiraIssue'],
                )

    async def test_destructive_tools_require_explicit_mode(
        self, atlassian_server: FastMCP, run_context: RunContext[None]
    ):
        write_tools = AtlassianToolset(cloud_id='site-1', access='read_write', client=atlassian_server)
        destructive_tools = AtlassianToolset(cloud_id='site-1', access='destructive', client=atlassian_server)
        async with write_tools:
            assert 'deleteJiraIssue' not in await write_tools.get_tools(run_context)
        async with destructive_tools:
            tools = await destructive_tools.get_tools(run_context)
        assert 'deleteJiraIssue' in tools
        metadata = tools['deleteJiraIssue'].tool_def.metadata
        assert metadata is not None
        assert metadata['atlassian_access'] == 'destructive'
