"""Test Atlassian's connection settings and tools through an agent."""

from __future__ import annotations

from dataclasses import dataclass

import httpx
import pytest
from fastmcp.client.transports import StreamableHttpTransport
from mcp.server.fastmcp import FastMCP
from pydantic_ai import Agent
from pydantic_ai.capabilities import DynamicCapability
from pydantic_ai.exceptions import UserError
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.messages import ModelRequest
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import AbstractToolset
from pydantic_ai.usage import RunUsage

from pydantic_ai_harness.atlassian import Atlassian

# MCP's test server leaves its lifespan annotation unresolved with pydantic-settings 2.15.
pytestmark = pytest.mark.filterwarnings(
    "ignore:Field 'lifespan' has an incomplete definition:UserWarning:pydantic_settings.sources.utils"
)


@pytest.fixture
def anyio_backend() -> str:
    return 'asyncio'


@pytest.fixture
def server() -> FastMCP:
    server = FastMCP('provider', instructions='Provider instructions.')

    @server.tool()
    def read_resource() -> str:
        return 'read'

    return server


def transport(capability: Atlassian[None]) -> StreamableHttpTransport:
    toolset = capability.get_toolset()
    assert isinstance(toolset, MCPToolset)
    result = toolset.client.transport
    assert isinstance(result, StreamableHttpTransport)
    return result


async def connections_for(capability: Atlassian[str | None], deps: str | None) -> list[MCPToolset[str | None]]:
    """The MCP connections a run with `deps` would open."""
    ctx = RunContext[str | None](deps=deps, model=TestModel(), usage=RunUsage())
    toolset = await capability.get_toolset().for_run(ctx)
    connections: list[MCPToolset[str | None]] = []

    def collect(leaf: AbstractToolset[str | None]) -> None:
        if isinstance(leaf, MCPToolset):
            connections.append(leaf)

    toolset.apply(collect)
    return connections


@dataclass
class Tenant:
    token: str | None


def no_credential(ctx: RunContext[object]) -> None:
    return None


def bearer(connection: MCPToolset[str | None]) -> str:
    transport = connection.client.transport
    assert isinstance(transport, StreamableHttpTransport) and transport.auth is not None
    request = next(transport.auth.auth_flow(httpx.Request('POST', 'https://example.com/mcp')))
    return request.headers['Authorization']


class TestAtlassian:
    async def test_agent_executes_tools(self, server: FastMCP) -> None:
        agent = Agent(TestModel(), capabilities=[Atlassian(client=server)])
        result = await agent.run('Use the tools')
        assert result.output == '{"read_resource":"read"}'

    @pytest.mark.parametrize('include', [True, False])
    async def test_server_instructions(self, server: FastMCP, include: bool) -> None:
        agent = Agent(TestModel(call_tools=[]), capabilities=[Atlassian(client=server, include_instructions=include)])
        result = await agent.run('Hello')
        request = result.all_messages()[0]
        assert isinstance(request, ModelRequest)
        assert ('Provider instructions.' in (request.instructions or '')) is include

    def test_custom_client_owns_authentication(self) -> None:
        client = StreamableHttpTransport('https://example.com/mcp', auth=httpx.BasicAuth('user', 'secret'))
        assert transport(Atlassian(client=client, auth='ignored')).auth is client.auth

    def test_auth_reaches_default_connection(self) -> None:
        auth = httpx.BasicAuth('user', 'secret')
        assert transport(Atlassian(auth=auth)).auth is auth

    def test_credential_is_not_in_repr(self) -> None:
        assert 'secret-token' not in repr(Atlassian(auth='secret-token'))

    def test_environment_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv('ATLASSIAN_API_KEY', 'environment-token')
        auth = transport(Atlassian()).auth
        assert isinstance(auth, httpx.Auth)
        request = next(auth.auth_flow(httpx.Request('POST', 'https://example.com/mcp')))
        assert request.headers['Authorization'] == 'Bearer environment-token'

    def test_flat_v2_endpoint(self) -> None:
        assert transport(Atlassian(auth='token')).url == 'https://mcp.atlassian.com/v2/mcp?tools=all'

    def test_missing_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv('ATLASSIAN_API_KEY', raising=False)
        with pytest.raises(UserError, match='Set `ATLASSIAN_API_KEY`'):
            Atlassian().get_toolset()


class TestPerRunAuth:
    async def test_each_run_connects_with_its_own_credential(self) -> None:
        capability = Atlassian[str | None](auth=lambda ctx: ctx.deps)
        [alice] = await connections_for(capability, 'alice-token')
        [bob] = await connections_for(capability, 'bob-token')
        assert (bearer(alice), bearer(bob)) == ('Bearer alice-token', 'Bearer bob-token')

    async def test_provider_returning_none_does_not_fall_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv('ATLASSIAN_API_KEY', 'deployment-token')
        capability = Atlassian[str | None](auth=lambda ctx: ctx.deps)
        assert await connections_for(capability, None) == []
        agent = Agent(TestModel(), capabilities=[Atlassian[object](auth=no_credential)])
        result = await agent.run('Use the tools')
        assert result.output == 'success (no tool calls)'

    async def test_dynamic_capability_builds_per_run(self, server: FastMCP) -> None:
        def atlassian(ctx: RunContext[Tenant]) -> Atlassian[Tenant] | None:
            return None if ctx.deps.token is None else Atlassian(client=server)

        agent = Agent(TestModel(), deps_type=Tenant, capabilities=[DynamicCapability(atlassian, id='atlassian')])
        alice = await agent.run('Use the tools', deps=Tenant('alice-token'))
        nobody = await agent.run('Use the tools', deps=Tenant(None))
        assert (alice.output, nobody.output) == ('{"read_resource":"read"}', 'success (no tool calls)')
