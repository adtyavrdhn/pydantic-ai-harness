"""Tests for Notion through its public capability surface."""

from __future__ import annotations

import pytest
from fastmcp.client.auth import BearerAuth
from fastmcp.client.transports import StreamableHttpTransport
from pydantic_ai import Agent
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.messages import ModelMessage, ToolReturnPart
from pydantic_ai.models.test import TestModel

from pydantic_ai_harness.notion import Notion


def _tools_called(messages: list[ModelMessage]) -> set[str]:
    """Every tool the model reached, which is every tool the capability exposed to it."""
    return {part.tool_name for message in messages for part in message.parts if isinstance(part, ToolReturnPart)}


def test_notion_endpoint_and_token_reach_the_transport() -> None:
    toolset = Notion(auth='notion-access-token').get_toolset()
    assert isinstance(toolset, MCPToolset)
    transport = toolset.client.transport
    assert isinstance(transport, StreamableHttpTransport)

    assert transport.url == 'https://mcp.notion.com/mcp'
    assert isinstance(transport.auth, BearerAuth)
    assert transport.auth.token.get_secret_value() == 'notion-access-token'


def test_token_stays_out_of_the_repr() -> None:
    assert 'notion-access-token' not in repr(Notion(auth='notion-access-token'))


def test_toolset_id_defaults_to_notion_and_follows_the_capability_id() -> None:
    assert Notion(auth='token').get_toolset().id == 'notion'
    assert Notion(auth='token', id='team-notion').get_toolset().id == 'team-notion'


async def test_default_exposes_every_tool(fake_notion: None) -> None:
    agent = Agent(TestModel(), capabilities=[Notion(auth='token')])

    result = await agent.run('Search the workspace and update the launch plan')

    assert _tools_called(result.all_messages()) == {'notion-search', 'notion-update-page', 'notion-summarize-page'}


async def test_read_only_keeps_only_the_read_tools(fake_notion: None) -> None:
    """A name Notion has not published is hidden too, so an unrecognized tool fails closed."""
    agent = Agent(TestModel(), capabilities=[Notion(auth='token', read_only=True)])

    result = await agent.run('Search the workspace and update the launch plan')

    assert _tools_called(result.all_messages()) == {'notion-search'}


def test_spec_schema_leaves_the_token_out() -> None:
    params = AgentSpec.model_json_schema_with_capabilities([Notion])['$defs']['spec_params_Notion']

    assert set(params['properties']) == {'id', 'description', 'defer_loading', 'read_only'}


def test_spec_carrying_a_token_is_rejected() -> None:
    # A checked-in spec file must not be able to hold a Notion access token.
    with pytest.raises(ValueError, match='auth'):
        Agent.from_spec(
            {'model': 'test', 'capabilities': [{'Notion': {'auth': 'ntn_access_token'}}]},
            custom_capability_types=[Notion],
        )


@pytest.mark.filterwarnings('ignore::UserWarning')
def test_spec_round_trip_rebuilds_the_capability() -> None:
    # A spec cannot name the token, so the rebuilt capability connects with `auth='oauth'`, and
    # fastmcp's OAuth client warns about its in-memory token store as it is built.
    agent = Agent.from_spec(
        {'model': 'test', 'capabilities': [{'Notion': {'id': 'team-notion', 'read_only': True}}]},
        custom_capability_types=[Notion],
    )
    (capability,) = [c for c in agent.root_capability.capabilities if isinstance(c, Notion)]

    assert capability.id == 'team-notion'
    assert capability.read_only is True
