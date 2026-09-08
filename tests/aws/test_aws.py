"""Tests for the connection `AWS` hands to `MCPToolset`, and what `read_only` hides."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastmcp.client.auth import BearerAuth
from fastmcp.client.transports import StreamableHttpTransport
from pydantic_ai import Agent
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from pydantic_ai_harness.aws import AWS

pytestmark = pytest.mark.anyio

_EU_URL = 'https://aws-mcp.eu-central-1.api.aws/mcp'


@pytest.fixture(autouse=True)
def _no_ambient_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep a developer's own `AWS_MCP_TOKEN` out of the tests that assert on the credential."""
    monkeypatch.delenv('AWS_MCP_TOKEN', raising=False)


def _http_transport(aws: AWS[Any]) -> StreamableHttpTransport:
    toolset = aws.get_toolset()
    assert isinstance(toolset, MCPToolset)
    transport = toolset.client.transport
    assert isinstance(transport, StreamableHttpTransport)
    return transport


def _record_tool_names(seen: list[str]) -> FunctionModel:
    """A model that reports the tools it was offered instead of calling any of them."""

    def record(_messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.extend(sorted(tool.name for tool in info.function_tools))
        return ModelResponse(parts=[TextPart('done')])

    return FunctionModel(record)


async def _visible_tool_names(aws: AWS[Any]) -> list[str]:
    seen: list[str] = []
    await Agent(_record_tool_names(seen), capabilities=[aws]).run('inspect AWS')
    return seen


class TestAWS:
    def test_endpoint_reaches_transport(self):
        assert _http_transport(AWS()).url == 'https://aws-mcp.us-east-1.api.aws/mcp'
        assert _http_transport(AWS(url=_EU_URL)).url == _EU_URL

    def test_token_reaches_transport_as_a_bearer_credential(self):
        auth = _http_transport(AWS(auth='aws-signin-token')).auth

        assert isinstance(auth, BearerAuth)
        assert auth.token.get_secret_value() == 'aws-signin-token'

    def test_token_stays_out_of_repr(self):
        assert 'aws-signin-token' not in repr(AWS(auth='aws-signin-token'))

    def test_token_falls_back_to_the_environment(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv('AWS_MCP_TOKEN', 'token-from-the-environment')
        auth = _http_transport(AWS()).auth

        assert isinstance(auth, BearerAuth)
        assert auth.token.get_secret_value() == 'token-from-the-environment'

    def test_no_credential_is_a_supported_connection(self):
        # AWS answers its documentation and discovery tools unauthenticated, so an absent
        # credential is a configuration rather than an error.
        assert _http_transport(AWS()).auth is None

    def test_toolset_id_defaults_to_aws_and_follows_capability_id(self):
        assert AWS().get_toolset().id == 'aws'
        assert AWS(id='eu-aws').get_toolset().id == 'eu-aws'

    async def test_default_exposes_every_tool(self, aws_mcp_url: str):
        assert await _visible_tool_names(AWS(url=aws_mcp_url)) == [
            'change_thing',
            'describe_thing',
            'unannotated_thing',
        ]

    async def test_read_only_exposes_only_the_tools_the_server_marks_read_only(self, aws_mcp_url: str):
        # `unannotated_thing` carries no annotations, so the filter fails closed and drops it.
        assert await _visible_tool_names(AWS(url=aws_mcp_url, read_only=True)) == ['describe_thing']

    async def test_spec_round_trips_into_a_live_connection(self, tmp_path: Path, aws_mcp_url: str):
        spec = tmp_path / 'agent.yaml'
        spec.write_text(f'capabilities:\n  - AWS:\n      url: {aws_mcp_url}\n      read_only: true\n', encoding='utf-8')
        seen: list[str] = []

        agent = Agent.from_file(spec, custom_capability_types=[AWS], model=_record_tool_names(seen))
        await agent.run('inspect AWS')

        assert seen == ['describe_thing']

    def test_a_spec_cannot_carry_a_token(self, tmp_path: Path):
        spec = tmp_path / 'agent.yaml'
        spec.write_text('capabilities:\n  - AWS:\n      auth: aws-signin-token\n', encoding='utf-8')

        with pytest.raises(ValueError, match="unexpected keyword argument 'auth'"):
            Agent.from_file(spec, custom_capability_types=[AWS], model=_record_tool_names([]))
