"""Behavioral tests for the public `Stripe` capability."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastmcp.client.transports import StreamableHttpTransport
from pydantic_ai import Agent, DeferredToolRequests, DeferredToolResults
from pydantic_ai.exceptions import UserError
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.models.test import TestModel
from pydantic_ai.toolsets import AbstractToolset

from pydantic_ai_harness.stripe import Stripe

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

pytestmark = pytest.mark.anyio


def mcp_leaf(toolset: AbstractToolset[None]) -> MCPToolset[None]:
    found: list[MCPToolset[None]] = []

    def collect(inner: AbstractToolset[None]) -> AbstractToolset[None]:
        if isinstance(inner, MCPToolset):
            found.append(inner)
        return inner

    toolset.visit_and_replace(collect)
    assert len(found) == 1
    return found[0]


def transport(capability: Stripe[None]) -> StreamableHttpTransport:
    resolved = mcp_leaf(capability.get_toolset()).client.transport
    assert isinstance(resolved, StreamableHttpTransport)
    return resolved


class TestStripe:
    async def test_read_only_by_default(self, stripe_server: FastMCP) -> None:
        model = TestModel()
        capability = Stripe(api_key='rk_test_read_only', client=stripe_server)
        await Agent(model, capabilities=[capability]).run('What Stripe tools are available?')
        request_parameters = model.last_model_request_parameters
        assert request_parameters is not None
        assert {tool.name for tool in request_parameters.function_tools} == {
            'search_stripe_documentation',
            'stripe_api_details',
            'stripe_api_read',
            'stripe_api_search',
        }

    async def test_agent_uses_stripe_read_tool(self, stripe_server: FastMCP) -> None:
        agent = Agent(
            TestModel(call_tools=['stripe_api_read']),
            capabilities=[Stripe(api_key='rk_test_agent', client=stripe_server)],
        )
        result = await agent.run('List customers')
        assert 'stripe_api_read' in result.output
        assert '"mode":"read"' in result.output

    async def test_mutations_require_approval(self, stripe_server: FastMCP) -> None:
        agent = Agent(
            TestModel(call_tools=['stripe_api_write']),
            capabilities=[Stripe(api_key='rk_test_write', enable_writes=True, client=stripe_server)],
            output_type=[str, DeferredToolRequests],
        )
        result = await agent.run('Create a refund')
        assert isinstance(result.output, DeferredToolRequests)
        assert [call.tool_name for call in result.output.approvals] == ['stripe_api_write']

        call = result.output.approvals[0]
        resumed = await agent.run(
            message_history=result.all_messages(),
            deferred_tool_results=DeferredToolResults(approvals={call.tool_call_id: True}),
        )
        assert isinstance(resumed.output, str)
        assert '"mode":"write"' in resumed.output

    def test_write_tool_is_absent_without_opt_in(self, stripe_server: FastMCP) -> None:
        with pytest.raises(UserError, match='stripe_api_write'):
            Agent(
                TestModel(call_tools=['stripe_api_write']),
                capabilities=[Stripe(api_key='rk_test_read_only', client=stripe_server)],
            ).run_sync('Create a refund')

    def test_account_boundaries_do_not_cross(self) -> None:
        platform = Stripe(api_key='rk_test_platform')
        connected = Stripe(api_key='rk_live_connected', mode='live', connected_account='acct_connected')

        platform_transport = transport(platform)
        connected_transport = transport(connected)

        assert platform_transport.headers == {'Authorization': 'Bearer rk_test_platform'}
        assert connected_transport.headers == {
            'Authorization': 'Bearer rk_live_connected',
            'Stripe-Account': 'acct_connected',
        }
        assert 'Stripe-Account' not in platform_transport.headers

    def test_credentials_and_account_are_hidden(self) -> None:
        capability = Stripe(
            api_key='rk_live_top_secret',
            mode='live',
            connected_account='acct_privateidentity',
            client='https://user:url-secret@example.com/mcp?token=query-secret',
        )
        representations = (repr(capability), repr(capability.get_toolset()))
        for representation in representations:
            assert 'top_secret' not in representation
            assert 'private_identity' not in representation
            assert 'url-secret' not in representation
            assert 'query-secret' not in representation
        instructions = capability.get_instructions()
        assert instructions is not None
        assert 'top_secret' not in instructions
        assert 'privateidentity' not in instructions

    @pytest.mark.parametrize(
        ('api_key', 'mode'),
        [
            ('rk_live_secret', 'sandbox'),
            ('rk_test_secret', 'live'),
        ],
    )
    def test_mode_must_match_key(self, api_key: str, mode: str) -> None:
        with pytest.raises(UserError, match='does not match'):
            Stripe(api_key=api_key, mode=mode)  # pyright: ignore[reportArgumentType]

    def test_mode_must_be_known(self) -> None:
        with pytest.raises(UserError, match='mode'):
            Stripe(api_key='rk_live_secret', mode='production')  # pyright: ignore[reportArgumentType]

    @pytest.mark.parametrize('api_key', ['sk_test_secret', 'pk_test_public', 'rk_other_secret', ''])
    def test_restricted_key_is_required(self, api_key: str) -> None:
        with pytest.raises(UserError, match='restricted API key'):
            Stripe(api_key=api_key)

    @pytest.mark.parametrize('account', ['acct_', 'customer_123', 'acct_bad value', 'acct_bad\nheader'])
    def test_connected_account_is_validated(self, account: str) -> None:
        with pytest.raises(UserError, match='connected_account'):
            Stripe(api_key='rk_test_secret', connected_account=account)

    def test_two_accounts_are_not_merged(self) -> None:
        first = Stripe(api_key='rk_test_first')
        second = Stripe(api_key='rk_test_second', connected_account='acct_second')
        agent = Agent(TestModel(), capabilities=[first, second])
        capabilities = [
            capability
            for capability in agent._root_capability.capabilities  # pyright: ignore[reportPrivateUsage]
            if isinstance(capability, Stripe)
        ]
        assert capabilities == [first, second]
