"""Behavioral tests for Linear through `Agent(capabilities=[...])`."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastmcp import Client
from fastmcp.client.transports import FastMCPTransport, StreamableHttpTransport
from pydantic_ai import Agent
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolCallPart
from pydantic_ai.models.test import TestModel
from pydantic_ai.toolsets import FilteredToolset

from pydantic_ai_harness.linear import LINEAR_MCP_URL, LINEAR_READ_ONLY_MCP_URL, Linear

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

pytestmark = pytest.mark.anyio


def _tool_call_names(messages: list[ModelMessage]) -> set[str]:
    return {part.tool_name for message in messages for part in message.parts if isinstance(part, ToolCallPart)}


def _request_instructions(messages: list[ModelMessage]) -> str:
    first = messages[0]
    assert isinstance(first, ModelRequest)
    return first.instructions or ''


def _http_transport(linear: Linear[None]) -> StreamableHttpTransport:
    toolset = linear.get_toolset()
    assert isinstance(toolset, MCPToolset)
    transport = toolset.client.transport
    assert isinstance(transport, StreamableHttpTransport)
    return transport


class TestLinear:
    def test_agent_spec_schema_excludes_runtime_client(self):
        schema = AgentSpec.model_json_schema_with_capabilities([Linear])
        linear_schema = schema['$defs']['spec_params_Linear']
        assert 'client' not in linear_schema['properties']

    def test_serialization_name(self):
        assert Linear.get_serialization_name() == 'Linear'

    def test_default_uses_documented_read_only_endpoint(self):
        transport = _http_transport(Linear())
        assert transport.url == LINEAR_READ_ONLY_MCP_URL
        assert transport.auth is None

    def test_oauth_is_forwarded(self):
        with pytest.warns(UserWarning, match='in-memory token storage'):
            transport = _http_transport(Linear(auth='oauth'))
        assert transport.auth is not None

    def test_read_write_is_explicit(self):
        transport = _http_transport(Linear(read_only=False))
        assert transport.url == LINEAR_MCP_URL

    def test_bearer_auth_is_forwarded_and_hidden_from_repr(self):
        capability = Linear(auth='lin_api_secret')
        transport = _http_transport(capability)
        assert transport.auth is not None
        assert 'lin_api_secret' not in repr(capability)

    def test_url_client_receives_auth(self):
        transport = _http_transport(Linear(client='https://proxy.example/mcp', auth='lin_api_secret'))
        assert transport.url == 'https://proxy.example/mcp'
        assert transport.auth is not None

    def test_no_auth_is_supported(self):
        transport = _http_transport(Linear(auth=None))
        assert transport.auth is None

    async def test_agent_calls_read_only_server_tool(self, linear_server: FastMCP):
        agent = Agent(TestModel(call_tools=['get_issue']), capabilities=[Linear(client=linear_server)])
        result = await agent.run('Read ENG-123')
        assert _tool_call_names(result.all_messages()) == {'get_issue'}
        assert 'Fix the build' in result.output

    async def test_allowed_tools_are_exact_names(self, linear_server: FastMCP):
        agent = Agent(
            TestModel(),
            capabilities=[Linear(client=linear_server, allowed_tools=['get_issue'])],
        )
        result = await agent.run('Read issues')
        assert _tool_call_names(result.all_messages()) == {'get_issue'}

    async def test_empty_allowed_tools_exposes_no_tools(self, linear_server: FastMCP):
        model = TestModel()
        agent = Agent(model, capabilities=[Linear(client=linear_server, allowed_tools=[])])
        await agent.run('Do not use tools')
        params = model.last_model_request_parameters
        assert params is not None
        assert params.function_tools == []

    def test_allowed_tools_uses_public_core_wrapper(self, linear_server: FastMCP):
        toolset = Linear(client=linear_server, allowed_tools=['get_issue']).get_toolset()
        assert isinstance(toolset, FilteredToolset)
        assert isinstance(toolset.wrapped, MCPToolset)

    def test_prebuilt_client_is_preserved(self, linear_server: FastMCP):
        client = Client(linear_server)
        toolset = Linear(client=client).get_toolset()
        assert isinstance(toolset, MCPToolset)
        assert toolset.client is client
        assert isinstance(client.transport, FastMCPTransport)

    def test_prebuilt_toolset_is_preserved(self, linear_server: FastMCP):
        prebuilt = MCPToolset(linear_server, id='tenant-linear')
        assert Linear(client=prebuilt).get_toolset() is prebuilt

    async def test_short_provider_instructions(self, linear_server: FastMCP):
        result = await Agent(TestModel(), capabilities=[Linear(client=linear_server)]).run('Read ENG-123')
        instructions = _request_instructions(result.all_messages())
        assert 'Linear' in instructions
        assert 'identifiers' in instructions

    async def test_read_write_instructions_cover_mutations(self, linear_server: FastMCP):
        result = await Agent(
            TestModel(), capabilities=[Linear(client=linear_server, read_only=False, allowed_tools=['create_issue'])]
        ).run('Create an issue')
        assert 'Before changing Linear data' in _request_instructions(result.all_messages())

    async def test_instructions_can_be_disabled(self, linear_server: FastMCP):
        result = await Agent(TestModel(), capabilities=[Linear(client=linear_server, include_instructions=False)]).run(
            'Read issues'
        )
        assert 'Linear tools' not in _request_instructions(result.all_messages())
