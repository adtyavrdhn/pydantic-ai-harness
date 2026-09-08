"""Tests for Atlassian through its public capability surface."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastmcp.client.transports import StreamableHttpTransport
from pydantic_ai import Agent
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities.abstract import leaf_capabilities
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.messages import ModelMessage, ToolReturnPart
from pydantic_ai.models.test import TestModel

from pydantic_ai_harness.atlassian import Atlassian


@pytest.fixture(autouse=True)
def _no_ambient_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """A developer's own `ATLASSIAN_API_KEY` must not decide what these tests assert."""
    monkeypatch.delenv('ATLASSIAN_API_KEY', raising=False)


def tool_returns(messages: list[ModelMessage]) -> dict[str, dict[str, Any]]:
    """Map each executed tool to the structured content it returned."""
    returns: dict[str, dict[str, Any]] = {}
    for message in messages:
        for part in message.parts:
            if isinstance(part, ToolReturnPart):
                content = part.content  # pyright: ignore[reportUnknownMemberType]
                assert isinstance(content, dict), content
                returns[part.tool_name] = content  # pyright: ignore[reportArgumentType, reportUnknownVariableType]
    return returns


def test_default_connects_to_atlassians_documented_endpoint():
    toolset = Atlassian[None](auth='api-key').get_toolset()
    assert isinstance(toolset, MCPToolset)
    transport = toolset.client.transport
    assert isinstance(transport, StreamableHttpTransport)
    assert transport.url == 'https://mcp.atlassian.com/v2/mcp?tools=all'


def test_toolset_id_defaults_to_atlassian_and_follows_the_capability_id():
    assert Atlassian(auth='api-key').get_toolset().id == 'atlassian'
    assert Atlassian(auth='api-key', id='ops-atlassian').get_toolset().id == 'ops-atlassian'


def test_credential_stays_out_of_the_repr():
    assert 'secret-key' not in repr(Atlassian(auth='secret-key'))


async def test_credential_reaches_the_server(fake_atlassian: None):
    result = await Agent(TestModel(), capabilities=[Atlassian(auth='secret-key')]).run('Read')
    assert {content['authorization'] for content in tool_returns(result.all_messages()).values()} == {
        'Bearer secret-key'
    }


async def test_the_credential_falls_back_to_the_environment(fake_atlassian: None, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv('ATLASSIAN_API_KEY', 'env-key')
    result = await Agent(TestModel(), capabilities=[Atlassian()]).run('Read')
    assert {content['authorization'] for content in tool_returns(result.all_messages()).values()} == {'Bearer env-key'}


async def test_default_exposes_every_tool(fake_atlassian: None):
    result = await Agent(TestModel(), capabilities=[Atlassian(auth='api-key')]).run('Do everything')
    assert set(tool_returns(result.all_messages())) == {'read_item', 'write_item', 'unannotated_item'}


async def test_read_only_keeps_only_the_tools_atlassian_marks_read_only(fake_atlassian: None):
    capability = Atlassian(auth='api-key', read_only=True)
    result = await Agent(TestModel(), capabilities=[capability]).run('Do everything')
    # `unannotated_item` carries no `readOnlyHint`, so the filter fails closed and drops it too.
    assert set(tool_returns(result.all_messages())) == {'read_item'}


def test_spec_schema_omits_the_credential():
    params = AgentSpec.model_json_schema_with_capabilities([Atlassian])['$defs']['spec_params_Atlassian']

    assert set(params['properties']) == {'id', 'description', 'defer_loading', 'read_only'}
    assert 'required' not in params


def test_agent_spec_round_trips(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv('ATLASSIAN_API_KEY', 'env-key')
    spec = tmp_path / 'agent.yaml'
    spec.write_text(
        'model: openai:gpt-5.6-sol\ncapabilities:\n  - Atlassian:\n      read_only: true\n', encoding='utf-8'
    )
    agent = Agent.from_file(spec, custom_capability_types=[Atlassian], model=TestModel())
    loaded = [c for c in leaf_capabilities(agent.root_capability) if isinstance(c, Atlassian)]
    assert loaded == [Atlassian(read_only=True)]


def test_agent_spec_cannot_carry_the_credential(tmp_path: Path):
    spec = tmp_path / 'agent.yaml'
    spec.write_text('model: openai:gpt-5.6-sol\ncapabilities:\n  - Atlassian:\n      auth: api-key\n', encoding='utf-8')

    with pytest.raises(ValueError, match='unexpected keyword argument .auth.'):
        Agent.from_file(spec, custom_capability_types=[Atlassian], model=TestModel())
