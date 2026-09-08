"""Tests for the connection `Stripe` hands to `MCPToolset`, and for what `read_only` hides."""

from __future__ import annotations

from typing import Any

import pytest
from fastmcp.client.auth import BearerAuth
from fastmcp.client.transports import StreamableHttpTransport
from pydantic_ai import Agent
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.exceptions import UserError
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.models.test import TestModel

from pydantic_ai_harness.stripe import Stripe


@pytest.fixture(autouse=True)
def _no_ambient_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """A developer's own `STRIPE_API_KEY` must not decide what these tests assert."""
    monkeypatch.delenv('STRIPE_API_KEY', raising=False)


def _transport(capability: Stripe[Any]) -> StreamableHttpTransport:
    toolset = capability.get_toolset()
    assert isinstance(toolset, MCPToolset)
    transport = toolset.client.transport
    assert isinstance(transport, StreamableHttpTransport)
    return transport


async def _tools_offered_to_the_model(capability: Stripe[Any]) -> set[str]:
    model = TestModel()
    await Agent(model, capabilities=[capability]).run('What can you do with Stripe?')
    parameters = model.last_model_request_parameters
    assert parameters is not None
    return {tool.name for tool in parameters.function_tools}


def test_endpoint_and_key_reach_the_transport():
    transport = _transport(Stripe(auth='sk_test_secret'))

    assert transport.url == 'https://mcp.stripe.com'
    assert isinstance(transport.auth, BearerAuth)
    assert transport.auth.token.get_secret_value() == 'sk_test_secret'


def test_connected_account_travels_as_the_stripe_account_header():
    assert _transport(Stripe(auth='sk_test_secret', connected_account='acct_123')).headers == {
        'Stripe-Account': 'acct_123'
    }
    assert _transport(Stripe(auth='sk_test_secret')).headers == {}


def test_key_stays_out_of_repr():
    assert 'sk_test_secret' not in repr(Stripe(auth='sk_test_secret'))


def test_key_falls_back_to_the_environment(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv('STRIPE_API_KEY', 'sk_test_from_env')

    auth = _transport(Stripe()).auth

    assert isinstance(auth, BearerAuth)
    assert auth.token.get_secret_value() == 'sk_test_from_env'


def test_missing_key_raises_user_error():
    with pytest.raises(UserError, match='STRIPE_API_KEY'):
        Stripe().get_toolset()


async def test_default_exposes_every_tool_stripe_serves(stripe_server: None):
    assert await _tools_offered_to_the_model(Stripe(auth='sk_test_secret')) == {
        'stripe_api_read',
        'stripe_api_write',
        'stripe_unlisted_tool',
    }


async def test_read_only_keeps_the_read_tools_and_hides_the_rest(stripe_server: None):
    # `stripe_unlisted_tool` stands for a tool Stripe adds later: the filter is a name set, so an
    # unrecognized tool is hidden rather than assumed to be a read.
    assert await _tools_offered_to_the_model(Stripe(auth='sk_test_secret', read_only=True)) == {'stripe_api_read'}


def test_spec_schema_exposes_only_the_public_fields():
    params = AgentSpec.model_json_schema_with_capabilities([Stripe])['$defs']['spec_params_Stripe']

    assert set(params['properties']) == {'id', 'description', 'defer_loading', 'connected_account', 'read_only'}
    assert 'required' not in params


def test_spec_rejects_a_key_written_into_the_file():
    with pytest.raises(ValueError, match="unexpected keyword argument 'auth'"):
        Agent.from_spec(
            {'model': 'test', 'capabilities': [{'Stripe': {'auth': 'sk_test_secret'}}]},
            custom_capability_types=[Stripe],
        )


def test_spec_round_trip_rebuilds_the_capability(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv('STRIPE_API_KEY', 'sk_test_from_env')

    agent = Agent.from_spec(
        {
            'model': 'test',
            'capabilities': [{'Stripe': {'id': 'platform-stripe', 'connected_account': 'acct_123', 'read_only': True}}],
        },
        custom_capability_types=[Stripe],
    )
    (capability,) = [c for c in agent.root_capability.capabilities if isinstance(c, Stripe)]

    assert capability.id == 'platform-stripe'
    assert capability.connected_account == 'acct_123'
    assert capability.read_only is True
