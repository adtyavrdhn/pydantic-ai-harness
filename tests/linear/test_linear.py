"""Tests for the connection `Linear` hands to `MCPToolset`."""

from __future__ import annotations

import pytest
from fastmcp.client.auth import BearerAuth
from fastmcp.client.transports import StreamableHttpTransport
from pydantic_ai import Agent
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.exceptions import UserError
from pydantic_ai.mcp import MCPToolset

from pydantic_ai_harness.linear import Linear


@pytest.fixture(autouse=True)
def _no_ambient_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """A developer's own `LINEAR_ACCESS_TOKEN` must not decide what these tests assert."""
    monkeypatch.delenv('LINEAR_ACCESS_TOKEN', raising=False)


def _transport(toolset: MCPToolset[None]) -> StreamableHttpTransport:
    transport = toolset.client.transport
    assert isinstance(transport, StreamableHttpTransport)
    return transport


def test_default_connects_to_the_read_write_endpoint():
    toolset = Linear(auth='token').get_toolset()

    assert _transport(toolset).url == 'https://mcp.linear.app/mcp'
    # The default hands back every tool Linear serves: no approval gate, no filter.
    assert type(toolset) is MCPToolset


def test_read_only_connects_to_the_read_only_endpoint():
    toolset = Linear(auth='token', read_only=True).get_toolset()

    # Linear narrows the catalog server-side, so the endpoint is the whole mechanism.
    assert _transport(toolset).url == 'https://mcp.linear.app/mcp/readonly'
    assert type(toolset) is MCPToolset


def test_token_reaches_the_transport():
    auth = _transport(Linear(auth='lin_api_secret').get_toolset()).auth

    assert isinstance(auth, BearerAuth)
    assert auth.token.get_secret_value() == 'lin_api_secret'


def test_token_stays_out_of_repr():
    assert 'lin_api_secret' not in repr(Linear(auth='lin_api_secret'))


def test_token_falls_back_to_the_environment(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv('LINEAR_ACCESS_TOKEN', 'lin_api_from_env')

    auth = _transport(Linear().get_toolset()).auth

    assert isinstance(auth, BearerAuth)
    assert auth.token.get_secret_value() == 'lin_api_from_env'


def test_missing_token_raises_user_error():
    with pytest.raises(UserError, match='LINEAR_ACCESS_TOKEN'):
        Linear().get_toolset()


def test_toolset_id_defaults_to_linear_and_follows_capability_id():
    assert Linear(auth='token').get_toolset().id == 'linear'
    assert Linear(auth='token', id='tenant-linear').get_toolset().id == 'tenant-linear'


def test_spec_schema_leaves_the_token_out():
    params = AgentSpec.model_json_schema_with_capabilities([Linear])['$defs']['spec_params_Linear']

    assert set(params['properties']) == {'id', 'description', 'defer_loading', 'read_only'}


def test_spec_carrying_a_token_is_rejected():
    # A checked-in spec file must not be able to hold a Linear credential.
    with pytest.raises(ValueError, match='auth'):
        Agent.from_spec(
            {'model': 'test', 'capabilities': [{'Linear': {'auth': 'lin_api_secret'}}]},
            custom_capability_types=[Linear],
        )


def test_spec_round_trip_rebuilds_the_capability(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv('LINEAR_ACCESS_TOKEN', 'lin_api_from_env')

    agent = Agent.from_spec(
        {'model': 'test', 'capabilities': [{'Linear': {'id': 'tenant-linear', 'read_only': True}}]},
        custom_capability_types=[Linear],
    )
    (capability,) = [c for c in agent.root_capability.capabilities if isinstance(c, Linear)]

    assert capability.id == 'tenant-linear'
    assert capability.read_only is True
