"""Behavioral tests for the public `Stripe` capability."""

from __future__ import annotations

from typing import Literal, Protocol

import httpx
import pytest
from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai.exceptions import UserError
from pydantic_ai.models.test import TestModel

from pydantic_ai_harness.stripe import Stripe


class StripeServer(Protocol):
    """Observable boundary exposed by the fake Stripe MCP server."""

    headers: list[dict[str, str]]
    urls: list[str]
    follow_redirects: list[bool]
    status_code: int | None
    redirect_to: str | None


pytestmark = pytest.mark.anyio


class TestStripe:
    async def test_read_only_by_default(self, stripe_server: StripeServer) -> None:
        model = TestModel()
        capability = Stripe(api_key='rk_test_read_only')
        await Agent(model, capabilities=[capability]).run('What Stripe tools are available?')
        request_parameters = model.last_model_request_parameters
        assert request_parameters is not None
        assert {tool.name for tool in request_parameters.function_tools} == {
            'get_stripe_account_info',
            'search_stripe_documentation',
            'stripe_api_details',
            'stripe_api_read',
            'stripe_api_search',
        }

    async def test_agent_uses_stripe_read_tool(self, stripe_server: StripeServer) -> None:
        agent = Agent(
            TestModel(call_tools=['stripe_api_read']),
            capabilities=[Stripe(api_key='rk_test_agent')],
        )
        result = await agent.run('List customers')
        assert 'stripe_api_read' in result.output
        assert '"mode":"read"' in result.output

    async def test_mutations_require_approval(self, stripe_server: StripeServer) -> None:
        agent = Agent(
            TestModel(call_tools=['stripe_api_write']),
            capabilities=[Stripe(api_key='rk_test_write', enable_writes=True)],
            output_type=[str, DeferredToolRequests],
        )
        result = await agent.run('Create a refund')
        assert isinstance(result.output, DeferredToolRequests)
        assert [call.tool_name for call in result.output.approvals] == ['stripe_api_write']

        resumed = await agent.run(
            message_history=result.all_messages(),
            deferred_tool_results=result.output.build_results(approve_all=True, metadata=result.output.metadata),
        )
        assert isinstance(resumed.output, str)
        assert '"mode":"write"' in resumed.output

    async def test_reads_do_not_require_approval_when_writes_are_enabled(self, stripe_server: StripeServer) -> None:
        agent = Agent(
            TestModel(call_tools=['stripe_api_read']),
            capabilities=[Stripe(api_key='rk_test_read_with_writes', enable_writes=True)],
            output_type=[str, DeferredToolRequests],
        )
        result = await agent.run('List customers')
        assert isinstance(result.output, str)
        assert '"mode":"read"' in result.output

    @pytest.mark.parametrize(
        ('api_key', 'mode', 'connected_account'),
        [
            ('rk_test_shared', 'sandbox', 'acct_second'),
            ('rk_test_other', 'sandbox', 'acct_first'),
            ('rk_live_other', 'live', 'acct_first'),
        ],
    )
    async def test_approval_cannot_cross_scope(
        self,
        stripe_server: StripeServer,
        api_key: str,
        mode: Literal['sandbox', 'live'],
        connected_account: str,
    ) -> None:
        first_agent = Agent(
            TestModel(call_tools=['stripe_api_write']),
            capabilities=[
                Stripe(
                    api_key='rk_test_shared',
                    connected_account='acct_first',
                    enable_writes=True,
                )
            ],
            output_type=[str, DeferredToolRequests],
        )
        result = await first_agent.run('Create a refund')
        assert isinstance(result.output, DeferredToolRequests)

        second_agent = Agent(
            TestModel(call_tools=['stripe_api_write']),
            capabilities=[
                Stripe(
                    api_key=api_key,
                    mode=mode,
                    connected_account=connected_account,
                    enable_writes=True,
                )
            ],
            output_type=[str, DeferredToolRequests],
        )
        with pytest.raises(UserError, match='does not match the current account scope'):
            await second_agent.run(
                message_history=result.all_messages(),
                deferred_tool_results=result.output.build_results(approve_all=True, metadata=result.output.metadata),
            )

    async def test_approval_requires_scope_metadata(self, stripe_server: StripeServer) -> None:
        agent = Agent(
            TestModel(call_tools=['stripe_api_write']),
            capabilities=[Stripe(api_key='rk_test_write', enable_writes=True)],
            output_type=[str, DeferredToolRequests],
        )
        result = await agent.run('Create a refund')
        assert isinstance(result.output, DeferredToolRequests)
        with pytest.raises(UserError, match='does not match the current account scope'):
            await agent.run(
                message_history=result.all_messages(),
                deferred_tool_results=result.output.build_results(approve_all=True),
            )

    async def test_write_tool_is_absent_without_opt_in(self, stripe_server: StripeServer) -> None:
        with pytest.raises(UserError, match='stripe_api_write'):
            await Agent(
                TestModel(call_tools=['stripe_api_write']),
                capabilities=[Stripe(api_key='rk_test_read_only')],
            ).run('Create a refund')

    async def test_account_boundaries_do_not_cross(self, stripe_server: StripeServer) -> None:
        platform = Stripe(api_key='rk_test_platform')
        connected = Stripe(api_key='rk_live_connected', mode='live', connected_account='acct_connected')

        await Agent(TestModel(), capabilities=[platform]).run('Inspect the platform')
        platform_headers = list(stripe_server.headers)
        stripe_server.headers.clear()
        await Agent(TestModel(), capabilities=[connected]).run('Inspect the connected account')
        connected_headers = list(stripe_server.headers)

        assert platform_headers and connected_headers
        assert stripe_server.urls
        assert set(stripe_server.urls) == {'https://mcp.stripe.com'}
        assert stripe_server.follow_redirects and not any(stripe_server.follow_redirects)
        assert all(headers.get('authorization') == 'Bearer rk_test_platform' for headers in platform_headers)
        assert all('stripe-account' not in headers for headers in platform_headers)
        assert all(headers.get('authorization') == 'Bearer rk_live_connected' for headers in connected_headers)
        assert all(headers.get('stripe-account') == 'acct_connected' for headers in connected_headers)

    def test_credentials_and_account_are_hidden(self) -> None:
        capability = Stripe(
            api_key='rk_live_top_secret',
            mode='live',
            connected_account='acct_privateidentity',
        )
        representations = (repr(capability), repr(capability.get_toolset()))
        for representation in representations:
            assert 'top_secret' not in representation
            assert 'private_identity' not in representation
        instructions = capability.get_instructions()
        assert instructions is not None
        assert 'top_secret' not in instructions
        assert 'privateidentity' not in instructions

    def test_instructions_can_be_disabled(self) -> None:
        assert Stripe(api_key='rk_test_secret', include_instructions=False).get_instructions() is None

    def test_credentials_are_not_serializable(self) -> None:
        assert Stripe.get_serialization_name() is None

    async def test_http_failure_does_not_expose_scope(self, stripe_server: StripeServer) -> None:
        stripe_server.status_code = 401
        capability = Stripe(
            api_key='rk_live_failure_secret',
            mode='live',
            connected_account='acct_failureidentity',
        )
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await Agent(TestModel(), capabilities=[capability]).run('List customers')
        message = str(exc_info.value)
        assert 'failure_secret' not in message
        assert 'failureidentity' not in message

    async def test_redirect_does_not_leave_stripe_origin(self, stripe_server: StripeServer) -> None:
        stripe_server.redirect_to = 'https://attacker.example/collect'
        capability = Stripe(
            api_key='rk_test_redirect_secret',
            connected_account='acct_redirectidentity',
        )
        with pytest.raises(httpx.HTTPStatusError):
            await Agent(TestModel(), capabilities=[capability]).run('List customers')
        assert set(stripe_server.urls) == {'https://mcp.stripe.com'}
        assert not any(stripe_server.follow_redirects)

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

    @pytest.mark.parametrize(
        'api_key',
        [
            'sk_test_secret',
            'pk_test_public',
            'rk_other_secret',
            '',
            'rk_test_',
            'rk_test_secret\nleak',
            'rk_test_sécret',
        ],
    )
    def test_restricted_key_is_required(self, api_key: str) -> None:
        with pytest.raises(UserError, match='restricted API key'):
            Stripe(api_key=api_key)

    @pytest.mark.parametrize('account', ['acct_', 'customer_123', 'acct_bad value', 'acct_bad\nheader', 'acct_sécret'])
    def test_connected_account_is_validated(self, account: str) -> None:
        with pytest.raises(UserError, match='connected_account'):
            Stripe(api_key='rk_test_secret', connected_account=account)

    async def test_two_accounts_are_not_merged(self, stripe_server: StripeServer) -> None:
        first = Stripe(api_key='rk_test_first')
        second = Stripe(api_key='rk_test_second', connected_account='acct_second')
        agent = Agent(TestModel(), capabilities=[first, second])
        with pytest.raises(UserError, match='conflicts with existing tool'):
            await agent.run('List customers')
