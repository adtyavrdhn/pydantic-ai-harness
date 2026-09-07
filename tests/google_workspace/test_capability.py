"""Tests for Google Workspace through its public capability surface."""

from __future__ import annotations

import json
from collections.abc import Generator
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic_ai import Agent, DeferredToolRequests, DeferredToolResults
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.exceptions import UserError
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from pydantic_ai_harness.google_workspace import GoogleWorkspace, GoogleWorkspaceService

from .conftest import FakeGoogle


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


def instructions(messages: list[ModelMessage]) -> str:
    return '\n'.join(m.instructions for m in messages if isinstance(m, ModelRequest) and m.instructions)


class TestGoogleWorkspace:
    # --- construction -------------------------------------------------------

    def test_services_accepts_one_name_or_several(self):
        assert GoogleWorkspace('gmail').services == ('gmail',)
        assert GoogleWorkspace(['gmail', 'calendar']).services == ('gmail', 'calendar')

    @pytest.mark.parametrize(
        ('services', 'message'),
        [
            ((), 'at least one'),
            (('gmail', 'gmail'), 'duplicates'),
            (('mail',), 'Unknown Google Workspace service'),
        ],
    )
    def test_rejects_invalid_services(self, services: tuple[str, ...], message: str):
        with pytest.raises(UserError, match=message):
            GoogleWorkspace(services)  # pyright: ignore[reportArgumentType]

    def test_rejects_unknown_access(self):
        with pytest.raises(UserError, match='`access` must be `read` or `write`'):
            GoogleWorkspace('gmail', access='admin')  # pyright: ignore[reportArgumentType]

    @pytest.mark.parametrize('allowed_tool', ['calendar_list_events', 'search_threads', 1, None])
    def test_rejects_allowed_tool_outside_selected_services(self, allowed_tool: object):
        with pytest.raises(UserError, match='does not belong to a selected service'):
            GoogleWorkspace('gmail', allowed_tools=(allowed_tool,))  # pyright: ignore[reportArgumentType]

    @pytest.mark.parametrize('token', ['', ' '])
    def test_rejects_empty_token(self, token: str, monkeypatch: pytest.MonkeyPatch):
        with pytest.raises(UserError, match='`auth` must not be empty'):
            GoogleWorkspace('gmail', auth=token)
        monkeypatch.setenv('GOOGLE_ACCESS_TOKEN', token)
        with pytest.raises(UserError, match='`GOOGLE_ACCESS_TOKEN` must not be empty'):
            Agent(TestModel(), capabilities=[GoogleWorkspace('gmail')])

    def test_missing_authentication_fails_before_a_run(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv('GOOGLE_ACCESS_TOKEN', raising=False)
        with pytest.raises(UserError, match='requires `auth` or `GOOGLE_ACCESS_TOKEN`'):
            Agent(TestModel(), capabilities=[GoogleWorkspace('gmail')])

    def test_credentials_stay_out_of_specs_and_reprs(self):
        schema = json.dumps(AgentSpec.model_json_schema_with_capabilities([GoogleWorkspace]))
        assert '"auth"' not in schema
        assert all(f'"{name}"' in schema for name in ('services', 'access', 'require_approval', 'allowed_tools'))
        assert 'secret-token' not in repr(GoogleWorkspace('gmail', auth='secret-token'))

    def test_agent_spec_loads(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv('GOOGLE_ACCESS_TOKEN', 'token')
        spec = tmp_path / 'agent.yaml'
        spec.write_text(
            'model: openai:gpt-5.6-sol\n'
            'capabilities:\n'
            '  - GoogleWorkspace:\n'
            '      services: [gmail, calendar]\n'
            '      access: write\n'
            '      require_approval: true\n',
            encoding='utf-8',
        )
        agent = Agent.from_file(spec, custom_capability_types=[GoogleWorkspace], model=TestModel())
        assert isinstance(agent, Agent)

    def test_from_spec_carries_every_option(self):
        from_spec = GoogleWorkspace.from_spec(
            ['gmail'],
            id='ws',
            defer_loading=True,
            access='write',
            require_approval=True,
            allowed_tools=['gmail_create_draft'],
            include_instructions=False,
        )
        direct = GoogleWorkspace(
            ['gmail'],
            id='ws',
            defer_loading=True,
            access='write',
            require_approval=True,
            allowed_tools=['gmail_create_draft'],
            include_instructions=False,
        )
        assert from_spec == direct

    # --- access policy ------------------------------------------------------

    async def test_read_access_exposes_only_tools_google_marks_read_only(self, gmail: str, calendar: str):
        agent = Agent(TestModel(), capabilities=[GoogleWorkspace(['gmail', 'calendar'], auth='token')])
        result = await agent.run('Read my mail and calendar')
        assert set(tool_returns(result.all_messages())) == {'gmail_search_threads', 'calendar_list_events'}

    async def test_write_access_exposes_every_tool(self, gmail: str, calendar: str):
        agent = Agent(TestModel(), capabilities=[GoogleWorkspace(['gmail', 'calendar'], access='write', auth='token')])
        result = await agent.run('Change my mail and calendar')
        assert set(tool_returns(result.all_messages())) == {
            'gmail_search_threads',
            'gmail_create_draft',
            'calendar_list_events',
            'calendar_create_event',
        }

    @pytest.mark.parametrize(('access', 'expected'), [('read', set[str]()), ('write', {'docs_update_doc'})])
    async def test_unannotated_tool_counts_as_a_write(self, access: str, expected: set[str], fake_google: FakeGoogle):
        fake_google.serve('docs', unannotated_tools=('update_doc',))
        agent = Agent(TestModel(), capabilities=[GoogleWorkspace('docs', access=access, auth='token')])  # pyright: ignore[reportArgumentType]
        result = await agent.run('Update the doc')
        assert set(tool_returns(result.all_messages())) == expected

    async def test_allowed_tools_narrows_but_never_widens_access(self, gmail: str, calendar: str):
        allowed = ('gmail_create_draft', 'calendar_list_events')
        writer = GoogleWorkspace(['gmail', 'calendar'], access='write', allowed_tools=allowed, auth='token')
        result = await Agent(TestModel(), capabilities=[writer]).run('Draft mail and read my calendar')
        assert set(tool_returns(result.all_messages())) == set(allowed)

        reader = GoogleWorkspace(['gmail', 'calendar'], allowed_tools=allowed, auth='token')
        result = await Agent(TestModel(), capabilities=[reader]).run('Draft mail and read my calendar')
        assert set(tool_returns(result.all_messages())) == {'calendar_list_events'}

    @pytest.mark.parametrize('service', ['gmail', 'drive', 'docs', 'sheets', 'slides', 'calendar', 'chat', 'people'])
    async def test_every_service_is_selectable_and_prefixed(
        self, service: GoogleWorkspaceService, fake_google: FakeGoogle
    ):
        fake_google.serve(service, read_tools=('get_item',))
        result = await Agent(TestModel(), capabilities=[GoogleWorkspace(service, auth='token')]).run('Read')
        assert set(tool_returns(result.all_messages())) == {f'{service}_get_item'}

    async def test_two_instances_for_one_product_collide(self, gmail: str):
        alice = GoogleWorkspace('gmail', auth='alice-token')
        bob = GoogleWorkspace('gmail', auth='bob-token')
        with pytest.raises(UserError, match='conflict'):
            await Agent(TestModel(), capabilities=[alice, bob]).run('Read both inboxes')

    # --- approval -----------------------------------------------------------

    async def test_require_approval_defers_writes_and_runs_reads(self, gmail: str):
        def call_read_and_write(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            if any(isinstance(part, ToolReturnPart) for message in messages for part in message.parts):
                return ModelResponse(parts=[TextPart('done')])
            return ModelResponse(
                parts=[
                    ToolCallPart('gmail_search_threads', {'query': 'launch'}, tool_call_id='read'),
                    ToolCallPart('gmail_create_draft', {'query': 'hello'}, tool_call_id='write'),
                ]
            )

        agent = Agent(
            FunctionModel(call_read_and_write),
            capabilities=[GoogleWorkspace('gmail', access='write', require_approval=True, auth='token')],
            output_type=[str, DeferredToolRequests],
        )
        paused = await agent.run('Find the launch thread and draft a reply')
        assert isinstance(paused.output, DeferredToolRequests)
        assert [approval.tool_name for approval in paused.output.approvals] == ['gmail_create_draft']
        assert set(tool_returns(paused.all_messages())) == {'gmail_search_threads'}

        resumed = await agent.run(
            message_history=paused.all_messages(),
            deferred_tool_results=DeferredToolResults(approvals={'write': True}),
        )
        assert set(tool_returns(resumed.all_messages())) == {'gmail_search_threads', 'gmail_create_draft'}

    async def test_require_approval_is_inert_in_read_mode(self, gmail: str):
        capability = GoogleWorkspace('gmail', require_approval=True, auth='token')
        result = await Agent(TestModel(), capabilities=[capability]).run('Read my mail')
        assert set(tool_returns(result.all_messages())) == {'gmail_search_threads'}

    # --- authentication -----------------------------------------------------

    async def test_bearer_token_reaches_every_product_server(self, gmail: str, calendar: str):
        capability = GoogleWorkspace(['gmail', 'calendar'], auth='explicit-token')
        result = await Agent(TestModel(), capabilities=[capability]).run('Read')
        returns = tool_returns(result.all_messages())
        assert {r['authorization'] for r in returns.values()} == {'Bearer explicit-token'}

    async def test_environment_token_is_used_when_auth_is_omitted(self, gmail: str, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv('GOOGLE_ACCESS_TOKEN', 'env-token')
        result = await Agent(TestModel(), capabilities=[GoogleWorkspace('gmail')]).run('Read')
        assert tool_returns(result.all_messages())['gmail_search_threads']['authorization'] == 'Bearer env-token'

    async def test_httpx_auth_supplies_the_token(self, gmail: str):
        class RefreshingAuth(httpx.Auth):
            def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
                request.headers['Authorization'] = 'Bearer refreshed-token'
                yield request

        capability = GoogleWorkspace('gmail', auth=RefreshingAuth())
        result = await Agent(TestModel(), capabilities=[capability]).run('Read')
        assert tool_returns(result.all_messages())['gmail_search_threads']['authorization'] == 'Bearer refreshed-token'

    # --- instructions -------------------------------------------------------

    async def test_instructions_follow_access_mode(self, gmail: str):
        result = await Agent(TestModel(), capabilities=[GoogleWorkspace('gmail', auth='token')]).run('Read')
        text = instructions(result.all_messages())
        assert 'untrusted data' in text
        assert 'read-only' in text

        writer = GoogleWorkspace('gmail', access='write', auth='token')
        assert 'Ask for confirmation before changing data' in (writer.get_instructions() or '')
        assert GoogleWorkspace('gmail', auth='token', include_instructions=False).get_instructions() is None
