"""Test Linear's connection settings and tool selection through an agent."""

from __future__ import annotations

from dataclasses import dataclass

import httpx
import pytest
from fastmcp.client.transports import StreamableHttpTransport
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic_ai import Agent
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.messages import ModelRequest
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import AbstractToolset
from pydantic_ai.usage import RunUsage

from pydantic_ai_harness.linear import Linear

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

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
    def read_resource() -> str:
        return 'read'

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False))
    def write_resource() -> str:
        return 'written'

    @server.tool()
    def unmarked_resource() -> str:
        return 'unmarked'

    return server


def transport(capability: Linear[None]) -> StreamableHttpTransport:
    toolset = capability.get_toolset()
    assert isinstance(toolset, MCPToolset)
    result = toolset.client.transport
    assert isinstance(result, StreamableHttpTransport)
    return result


async def connections_for(capability: Linear[str | None], deps: str | None) -> list[MCPToolset[str | None]]:
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


class TestLinear:
    @pytest.mark.parametrize(
        ('read_only', 'expected'),
        [
            (False, '{"read_resource":"read","write_resource":"written","unmarked_resource":"unmarked"}'),
            (True, '{"read_resource":"read"}'),
        ],
    )
    async def test_agent_executes_selected_tools(self, server: FastMCP, read_only: bool, expected: str) -> None:
        agent = Agent(TestModel(), capabilities=[Linear(client=server, read_only=read_only)])
        result = await agent.run('Use the tools')
        assert result.output == expected

    @pytest.mark.parametrize('include', [True, False])
    async def test_server_instructions(self, server: FastMCP, include: bool) -> None:
        agent = Agent(TestModel(call_tools=[]), capabilities=[Linear(client=server, include_instructions=include)])
        result = await agent.run('Hello')
        request = result.all_messages()[0]
        assert isinstance(request, ModelRequest)
        assert ('Provider instructions.' in (request.instructions or '')) is include

    def test_custom_client_owns_authentication(self) -> None:
        client = StreamableHttpTransport('https://example.com/mcp', auth=httpx.BasicAuth('user', 'secret'))
        assert transport(Linear(client=client, auth='ignored')).auth is client.auth

    def test_auth_reaches_default_connection(self) -> None:
        auth = httpx.BasicAuth('user', 'secret')
        assert transport(Linear(auth=auth)).auth is auth

    def test_credential_is_not_in_repr(self) -> None:
        assert 'secret-token' not in repr(Linear(auth='secret-token'))

    def test_environment_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv('LINEAR_ACCESS_TOKEN', 'environment-token')
        auth = transport(Linear()).auth
        assert isinstance(auth, httpx.Auth)
        request = next(auth.auth_flow(httpx.Request('POST', 'https://example.com/mcp')))
        assert request.headers['Authorization'] == 'Bearer environment-token'

    @pytest.mark.parametrize('read_only', [True, False])
    def test_native_read_only_endpoint(self, read_only: bool) -> None:
        suffix = '/readonly' if read_only else ''
        assert transport(Linear(auth='token', read_only=read_only)).url == 'https://mcp.linear.app/mcp' + suffix

    def test_oauth_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv('LINEAR_ACCESS_TOKEN', raising=False)
        with pytest.warns(UserWarning, match='in-memory token storage'):
            assert transport(Linear()).auth is not None


class TestPerRunAuth:
    async def test_each_run_connects_with_its_own_credential(self) -> None:
        capability = Linear[str | None](auth=lambda ctx: ctx.deps)
        [alice] = await connections_for(capability, 'alice-token')
        [bob] = await connections_for(capability, 'bob-token')
        assert (bearer(alice), bearer(bob)) == ('Bearer alice-token', 'Bearer bob-token')

    async def test_async_provider(self) -> None:
        async def token(ctx: RunContext[str | None]) -> str | None:
            return ctx.deps

        [connection] = await connections_for(Linear[str | None](auth=token), 'alice-token')
        assert bearer(connection) == 'Bearer alice-token'

    async def test_provider_returning_none_does_not_fall_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv('LINEAR_ACCESS_TOKEN', 'deployment-token')
        capability = Linear[str | None](auth=lambda ctx: ctx.deps)
        assert await connections_for(capability, None) == []
        agent = Agent(TestModel(), capabilities=[Linear[object](auth=no_credential)])
        result = await agent.run('Use the tools')
        assert result.output == 'success (no tool calls)'

    async def test_read_only_endpoint_applies_per_run(self) -> None:
        capability = Linear[str | None](auth=lambda ctx: ctx.deps, read_only=True)
        [connection] = await connections_for(capability, 'alice-token')
        transport = connection.client.transport
        assert isinstance(transport, StreamableHttpTransport)
        assert transport.url == 'https://mcp.linear.app/mcp/readonly'

    async def test_client_function_is_filtered_per_run(self, server: FastMCP) -> None:
        def client(ctx: RunContext[Tenant]) -> FastMCP | None:
            return None if ctx.deps.token is None else server

        agent = Agent(TestModel(), deps_type=Tenant, capabilities=[Linear(client=client, read_only=True)])
        alice = await agent.run('Use the tools', deps=Tenant('alice-token'))
        nobody = await agent.run('Use the tools', deps=Tenant(None))
        assert (alice.output, nobody.output) == ('{"read_resource":"read"}', 'success (no tool calls)')
