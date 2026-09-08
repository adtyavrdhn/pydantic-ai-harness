"""Tests for the connection `Cloudflare` hands to `MCPToolset`, and for what it exposes."""

from __future__ import annotations

import pytest
from fastmcp.client.auth import BearerAuth
from fastmcp.client.transports import StreamableHttpTransport
from pydantic_ai import Agent
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.messages import ModelMessage, ToolReturnPart
from pydantic_ai.models.test import TestModel

from pydantic_ai_harness.cloudflare import Cloudflare


def _transport(cloudflare: Cloudflare[None]) -> StreamableHttpTransport:
    toolset = cloudflare.get_toolset()
    assert isinstance(toolset, MCPToolset)
    transport = toolset.client.transport
    assert isinstance(transport, StreamableHttpTransport)
    return transport


def _tool_returns(messages: list[ModelMessage]) -> dict[str, object]:
    """Map each executed tool to what it returned."""
    return {
        part.tool_name: part.content
        for message in messages
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    }


class TestCloudflare:
    def test_default_connects_to_the_public_documentation_server(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv('CLOUDFLARE_API_TOKEN', raising=False)
        transport = _transport(Cloudflare())

        assert transport.url == 'https://docs.mcp.cloudflare.com/mcp'
        assert transport.auth is None

    def test_api_token_reaches_the_transport_and_stays_out_of_repr(self):
        capability = Cloudflare(auth='cf-api-token')
        auth = _transport(capability).auth

        assert isinstance(auth, BearerAuth)
        assert auth.token.get_secret_value() == 'cf-api-token'
        assert 'cf-api-token' not in repr(capability)

    def test_environment_token_is_used_when_auth_is_omitted(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv('CLOUDFLARE_API_TOKEN', 'env-token')
        auth = _transport(Cloudflare()).auth

        assert isinstance(auth, BearerAuth)
        assert auth.token.get_secret_value() == 'env-token'

    async def test_default_exposes_every_tool_and_sends_the_credential(self, cloudflare_server: str):
        capability = Cloudflare(url=cloudflare_server, auth='wire-token')
        result = await Agent(TestModel(), capabilities=[capability]).run('Use every tool')
        returns = _tool_returns(result.all_messages())

        assert set(returns) == {'read_item', 'write_item', 'unannotated_item'}
        assert set(returns.values()) == {'Bearer wire-token'}

    async def test_read_only_keeps_only_the_tools_the_server_marks_read_only(self, cloudflare_server: str):
        capability = Cloudflare(url=cloudflare_server, auth='wire-token', read_only=True)
        result = await Agent(TestModel(), capabilities=[capability]).run('Use every tool')

        assert set(_tool_returns(result.all_messages())) == {'read_item'}

    def test_spec_schema_omits_the_credential(self):
        params = AgentSpec.model_json_schema_with_capabilities([Cloudflare])['$defs']['spec_params_Cloudflare']

        assert set(params['properties']) == {'id', 'description', 'defer_loading', 'url', 'read_only'}

    def test_spec_round_trip_rebuilds_the_capability(self):
        agent = Agent.from_spec(
            {'capabilities': [{'Cloudflare': {'url': 'https://blog.mcp.cloudflare.com/mcp', 'read_only': True}}]},
            custom_capability_types=[Cloudflare],
            model=TestModel(),
        )
        (capability,) = [c for c in agent.root_capability.capabilities if isinstance(c, Cloudflare)]

        assert capability.url == 'https://blog.mcp.cloudflare.com/mcp'
        assert capability.read_only is True

    def test_spec_carrying_a_token_is_rejected(self):
        # A checked-in spec file must not be able to hold a Cloudflare API token.
        with pytest.raises(ValueError, match='auth'):
            Agent.from_spec(
                {'capabilities': [{'Cloudflare': {'auth': 'cf-api-token'}}]},
                custom_capability_types=[Cloudflare],
                model=TestModel(),
            )
