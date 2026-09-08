"""Tests for Google Workspace through its public capability surface."""

from __future__ import annotations

from pathlib import Path
from typing import Any, get_args

import pytest
from pydantic_ai import Agent
from pydantic_ai.capabilities.abstract import leaf_capabilities
from pydantic_ai.exceptions import UserError
from pydantic_ai.messages import ModelMessage, ToolReturnPart
from pydantic_ai.models.test import TestModel

from pydantic_ai_harness.google_workspace import GoogleWorkspace
from pydantic_ai_harness.google_workspace._capability import _MCP_URLS, GoogleWorkspaceService


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


def test_every_service_has_an_endpoint():
    assert set(get_args(GoogleWorkspaceService)) == set(_MCP_URLS)


@pytest.mark.parametrize(('services', 'message'), [((), 'at least one'), (('mail',), 'Unknown Google Workspace')])
def test_rejects_a_service_list_it_has_no_endpoints_for(services: tuple[str, ...], message: str):
    with pytest.raises(UserError, match=message):
        GoogleWorkspace(services=services)  # pyright: ignore[reportArgumentType]


def test_missing_token_fails_before_a_run(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv('GOOGLE_ACCESS_TOKEN', raising=False)
    with pytest.raises(UserError, match='needs a token'):
        Agent(TestModel(), capabilities=[GoogleWorkspace(services='gmail')])


def test_token_stays_out_of_the_repr():
    assert 'secret-token' not in repr(GoogleWorkspace(services='gmail', auth='secret-token'))


def test_token_cannot_be_written_into_a_spec(tmp_path: Path):
    spec = tmp_path / 'agent.yaml'
    spec.write_text(
        'model: openai:gpt-5.6-sol\ncapabilities:\n  - GoogleWorkspace:\n      services: [gmail]\n      auth: t\n',
        encoding='utf-8',
    )
    with pytest.raises(ValueError, match='auth'):
        Agent.from_file(spec, custom_capability_types=[GoogleWorkspace], model=TestModel())


async def test_each_selected_endpoint_receives_the_token(fake_google: None):
    capability = GoogleWorkspace(services=['gmail', 'calendar'], auth='explicit-token')
    result = await Agent(TestModel(), capabilities=[capability]).run('Read')
    returns = tool_returns(result.all_messages())
    assert {name.split('_')[0] for name in returns} == {'gmail', 'calendar'}
    assert {content['authorization'] for content in returns.values()} == {'Bearer explicit-token'}


async def test_environment_token_is_used_when_auth_is_omitted(fake_google: None, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv('GOOGLE_ACCESS_TOKEN', 'env-token')
    result = await Agent(TestModel(), capabilities=[GoogleWorkspace(services='gmail')]).run('Read')
    assert tool_returns(result.all_messages())['gmail_read_item']['authorization'] == 'Bearer env-token'


async def test_default_exposes_every_tool(fake_google: None):
    capability = GoogleWorkspace(services='gmail', auth='token')
    result = await Agent(TestModel(), capabilities=[capability]).run('Do everything')
    assert set(tool_returns(result.all_messages())) == {
        'gmail_read_item',
        'gmail_write_item',
        'gmail_unannotated_item',
    }


async def test_read_only_keeps_only_the_tools_google_marks_read_only(fake_google: None):
    capability = GoogleWorkspace(services='gmail', read_only=True, auth='token')
    result = await Agent(TestModel(), capabilities=[capability]).run('Do everything')
    assert set(tool_returns(result.all_messages())) == {'gmail_read_item'}


@pytest.mark.parametrize(
    ('capability', 'services', 'read_only'),
    [
        ('  - GoogleWorkspace: gmail\n', ('gmail',), False),
        (
            '  - GoogleWorkspace:\n      services: [gmail, calendar]\n      read_only: true\n',
            ('gmail', 'calendar'),
            True,
        ),
    ],
    ids=['short-form', 'long-form'],
)
def test_agent_spec_round_trips(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capability: str,
    services: tuple[GoogleWorkspaceService, ...],
    read_only: bool,
):
    monkeypatch.setenv('GOOGLE_ACCESS_TOKEN', 'token')
    spec = tmp_path / 'agent.yaml'
    spec.write_text(f'model: openai:gpt-5.6-sol\ncapabilities:\n{capability}', encoding='utf-8')
    agent = Agent.from_file(spec, custom_capability_types=[GoogleWorkspace], model=TestModel())
    loaded = [c for c in leaf_capabilities(agent.root_capability) if isinstance(c, GoogleWorkspace)]
    assert loaded == [GoogleWorkspace(services=services, read_only=read_only)]
