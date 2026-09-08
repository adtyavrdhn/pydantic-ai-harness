"""Tests for the connection `Linear` hands to `MCPToolset`."""

from __future__ import annotations

import httpx
import pytest
from fastmcp.client.auth import BearerAuth, OAuth
from fastmcp.client.transports import StreamableHttpTransport
from pydantic_ai.agent.spec import AgentSpec

from pydantic_ai_harness.linear import Linear


def _http_transport(linear: Linear[None]) -> StreamableHttpTransport:
    transport = linear.get_toolset().client.transport
    assert isinstance(transport, StreamableHttpTransport)
    return transport


class TestLinear:
    def test_default_uses_read_write_endpoint(self):
        assert _http_transport(Linear(auth='token')).url == 'https://mcp.linear.app/mcp'

    def test_read_only_uses_read_only_endpoint(self):
        assert _http_transport(Linear(auth='token', read_only=True)).url == 'https://mcp.linear.app/mcp/readonly'

    def test_bearer_token_reaches_transport_and_stays_out_of_repr(self):
        capability = Linear(auth='lin_api_secret')
        auth = _http_transport(capability).auth

        assert isinstance(auth, BearerAuth)
        assert auth.token.get_secret_value() == 'lin_api_secret'
        assert 'lin_api_secret' not in repr(capability)

    def test_custom_httpx_auth_reaches_transport(self):
        auth = httpx.BasicAuth('user', 'secret')

        assert _http_transport(Linear(auth=auth)).auth is auth

    def test_oauth_reaches_transport(self):
        with pytest.warns(UserWarning, match='in-memory token storage'):
            auth = _http_transport(Linear(auth='oauth')).auth

        assert isinstance(auth, OAuth)

    def test_toolset_id_defaults_to_linear_and_follows_capability_id(self):
        assert Linear(auth='token').get_toolset().id == 'linear'
        assert Linear(auth='token', id='tenant-linear').get_toolset().id == 'tenant-linear'

    def test_spec_schema_requires_auth(self):
        schema = AgentSpec.model_json_schema_with_capabilities([Linear])
        params = schema['$defs']['spec_params_Linear']

        assert set(params['properties']) == {'id', 'description', 'defer_loading', 'read_only', 'auth'}
        assert params['required'] == ['auth']

    def test_from_spec_forwards_options(self):
        capability = Linear.from_spec(
            id='tenant-linear', description='Tenant issues', defer_loading=True, read_only=True, auth='token'
        )

        assert capability.id == 'tenant-linear'
        assert capability.description == 'Tenant issues'
        assert capability.defer_loading is True
        assert capability.read_only is True
        assert capability.auth == 'token'
