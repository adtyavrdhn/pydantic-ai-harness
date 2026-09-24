"""Test Stripe's connection settings and tools through an agent."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastmcp.client.auth import OAuth
from fastmcp.client.transports import StreamableHttpTransport
from mcp.server.fastmcp import FastMCP
from pydantic_ai import Agent
from pydantic_ai.exceptions import UserError
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.messages import ModelRequest
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import AbstractToolset
from pydantic_ai.usage import RunUsage

from pydantic_ai_harness.stripe import Stripe

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


def transport(capability: Stripe[None]) -> StreamableHttpTransport:
    toolset = capability.get_toolset()
    assert isinstance(toolset, MCPToolset)
    result = toolset.client.transport
    assert isinstance(result, StreamableHttpTransport)
    return result


async def connections_for(capability: Stripe[str | None], deps: str | None) -> list[MCPToolset[str | None]]:
    """The MCP connections a run with `deps` would open."""
    ctx = RunContext[str | None](deps=deps, model=TestModel(), usage=RunUsage())
    toolset = await capability.get_toolset().for_run(ctx)
    connections: list[MCPToolset[str | None]] = []

    def collect(leaf: AbstractToolset[str | None]) -> None:
        if isinstance(leaf, MCPToolset):
            connections.append(leaf)

    toolset.apply(collect)
    return connections


def no_credential(ctx: RunContext[object]) -> None:
    return None


def bearer(connection: MCPToolset[str | None]) -> str:
    transport = connection.client.transport
    assert isinstance(transport, StreamableHttpTransport) and transport.auth is not None
    request = next(transport.auth.auth_flow(httpx.Request('POST', 'https://example.com/mcp')))
    return request.headers['Authorization']


class TestStripe:
    async def test_agent_executes_tools(self, server: FastMCP) -> None:
        agent = Agent(TestModel(), capabilities=[Stripe(client=server)])
        result = await agent.run('Use the tools')
        assert result.output == '{"read_resource":"read"}'

    @pytest.mark.parametrize('include', [True, False])
    async def test_server_instructions(self, server: FastMCP, include: bool) -> None:
        agent = Agent(TestModel(call_tools=[]), capabilities=[Stripe(client=server, include_instructions=include)])
        result = await agent.run('Hello')
        request = result.all_messages()[0]
        assert isinstance(request, ModelRequest)
        assert ('Provider instructions.' in (request.instructions or '')) is include

    def test_custom_client_owns_authentication(self) -> None:
        client = StreamableHttpTransport('https://example.com/mcp', auth=httpx.BasicAuth('user', 'secret'))
        assert transport(Stripe(client=client)).auth is client.auth

    @pytest.mark.parametrize(
        'settings', [{'auth': 'key'}, {'auth': no_credential}, {'connected_account': 'acct_example'}]
    )
    def test_client_cannot_be_combined_with_connection_settings(self, settings: dict[str, Any]) -> None:
        with pytest.raises(UserError, match='`client` owns the connection'):
            Stripe(client='https://example.com/mcp', **settings)

    def test_defer_loading_needs_no_id(self, server: FastMCP) -> None:
        Agent(TestModel(), capabilities=[Stripe(client=server, defer_loading=True)])

    def test_two_that_differ_raise_when_the_agent_is_built(self) -> None:
        with pytest.raises(UserError, match="Two `Stripe` capabilities share the id 'stripe'"):
            Agent(TestModel(), capabilities=[Stripe(auth='a'), Stripe(auth='b', connected_account='acct_other')])

    def test_credential_is_not_in_repr(self) -> None:
        assert 'secret-token' not in repr(Stripe(auth='secret-token'))

    def test_environment_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv('STRIPE_API_KEY', 'environment-token')
        auth = transport(Stripe()).auth
        assert isinstance(auth, httpx.Auth)
        request = next(auth.auth_flow(httpx.Request('POST', 'https://example.com/mcp')))
        assert request.headers['Authorization'] == 'Bearer environment-token'

    def test_native_connected_account(self) -> None:
        assert transport(Stripe(auth='token', connected_account='acct_example')).headers == {
            'Stripe-Account': 'acct_example'
        }

    def test_hosted_endpoint(self) -> None:
        assert transport(Stripe(auth='token')).url == 'https://mcp.stripe.com'

    def test_missing_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv('STRIPE_API_KEY', raising=False)
        with pytest.raises(UserError, match='Set `STRIPE_API_KEY`'):
            Stripe().get_toolset()


class TestPerRunAuth:
    async def test_each_run_connects_with_its_own_credential(self) -> None:
        capability = Stripe[str | None](auth=lambda ctx: ctx.deps)
        [alice] = await connections_for(capability, 'alice-token')
        [bob] = await connections_for(capability, 'bob-token')
        assert (bearer(alice), bearer(bob)) == ('Bearer alice-token', 'Bearer bob-token')

    @pytest.mark.parametrize('missing', [None, ''])
    async def test_provider_returning_none_does_not_fall_back(
        self, missing: str | None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv('STRIPE_API_KEY', 'deployment-token')
        capability = Stripe[str | None](auth=lambda ctx: ctx.deps)
        assert await connections_for(capability, missing) == []
        agent = Agent(TestModel(), capabilities=[Stripe[object](auth=no_credential)])
        result = await agent.run('Use the tools')
        assert result.output == 'success (no tool calls)'

    async def test_provider_returning_oauth_raises(self) -> None:
        capability = Stripe[str | None](auth=lambda ctx: ctx.deps)
        with pytest.raises(UserError, match="must return an API key or token, not 'oauth'"):
            await connections_for(capability, 'oauth')

    @pytest.mark.filterwarnings('ignore:Using in-memory token storage')
    def test_fixed_oauth_uses_browser_login(self) -> None:
        assert isinstance(transport(Stripe(auth='oauth')).auth, OAuth)

    async def test_connected_account_applies_per_run(self) -> None:
        capability = Stripe[str | None](auth=lambda ctx: ctx.deps, connected_account='acct_example')
        [connection] = await connections_for(capability, 'alice-token')
        transport = connection.client.transport
        assert isinstance(transport, StreamableHttpTransport)
        assert transport.headers == {'Stripe-Account': 'acct_example'}

    async def test_dynamic_capability_selects_connected_account_per_run(self) -> None:
        def stripe(ctx: RunContext[str | None]) -> Stripe[str | None] | None:
            if ctx.deps is None:
                return None
            return Stripe(auth='platform-key', connected_account=ctx.deps)

        alice = stripe(RunContext(deps='acct_alice', model=TestModel(), usage=RunUsage()))
        bob = stripe(RunContext(deps='acct_bob', model=TestModel(), usage=RunUsage()))
        assert alice is not None and bob is not None
        assert transport(alice).headers == {'Stripe-Account': 'acct_alice'}
        assert transport(bob).headers == {'Stripe-Account': 'acct_bob'}
        assert stripe(RunContext(deps=None, model=TestModel(), usage=RunUsage())) is None
