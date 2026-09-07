"""Behavioral tests for Linear through `Agent(capabilities=[...])`."""

from __future__ import annotations

import warnings

import pytest
from fastmcp.client.transports import StreamableHttpTransport
from mcp.server.fastmcp.server import FastMCP, Settings
from pydantic_ai import Agent
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.models.test import TestModel

from pydantic_ai_harness.linear import Linear

# The MCP SDK leaves a settings annotation unresolved in some supported dependency
# combinations. Rebuild it before warnings are escalated by the test suite.
Settings.model_rebuild()


@pytest.fixture
def anyio_backend() -> str:
    return 'asyncio'


def _http_transport(linear: Linear[None]) -> StreamableHttpTransport:
    toolset = linear.get_toolset()
    transport = toolset.client.transport
    assert isinstance(transport, StreamableHttpTransport)
    return transport


class TestLinear:
    def test_agent_accepts_capability(self):
        capability = Linear(auth='token')

        agent = Agent(TestModel(), capabilities=[capability])

        assert capability in agent.root_capability.capabilities

    @pytest.mark.anyio
    async def test_agent_runs_with_linear_tools(self):
        server = FastMCP('linear-fake')

        @server.tool()
        def get_issue(issue_id: str) -> dict[str, str]:
            """Get one Linear issue."""
            return {'id': issue_id, 'title': 'Fix the build'}

        class FakeLinear(Linear[None]):
            def get_toolset(self) -> MCPToolset[None]:
                return MCPToolset(server)

        agent = Agent[None, str](
            TestModel(call_tools=['get_issue']), deps_type=type(None), capabilities=[FakeLinear(auth='token')]
        )

        result = await agent.run('Read ENG-123')

        assert 'Fix the build' in result.output

    def test_serialization_name(self):
        assert Linear.get_serialization_name() == 'Linear'

    def test_from_spec_forwards_options(self):
        capability = Linear.from_spec(
            id='tenant-linear',
            description='Tenant issues',
            defer_loading=True,
            read_only=False,
            auth='token',
        )
        assert capability.id == 'tenant-linear'
        assert capability.description == 'Tenant issues'
        assert capability.defer_loading is True
        assert capability.read_only is False
        assert capability.auth == 'token'

    def test_default_uses_read_only_endpoint(self):
        transport = _http_transport(Linear(auth='token'))

        assert transport.url == 'https://mcp.linear.app/mcp/readonly'

    def test_read_write_is_explicit(self):
        transport = _http_transport(Linear(auth='token', read_only=False))

        assert transport.url == 'https://mcp.linear.app/mcp'

    def test_oauth_is_forwarded(self):
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            transport = _http_transport(Linear(auth='oauth'))

        assert transport.auth is not None

    def test_bearer_auth_is_forwarded_and_hidden_from_repr(self):
        capability = Linear(auth='lin_api_secret')
        transport = _http_transport(capability)

        assert transport.auth is not None
        assert 'lin_api_secret' not in repr(capability)

    def test_custom_id_is_forwarded(self):
        toolset = Linear(auth='token', id='tenant-linear').get_toolset()

        assert isinstance(toolset, MCPToolset)
        assert toolset.id == 'tenant-linear'
