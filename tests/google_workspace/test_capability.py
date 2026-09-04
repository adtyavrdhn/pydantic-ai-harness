"""Tests for Google Workspace through its public capability surface."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.exceptions import UserError
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.toolsets import FunctionToolset

from pydantic_ai_harness.google_workspace import GoogleWorkspace, GoogleWorkspaceService

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

pytestmark = pytest.mark.anyio


def tool_call_names(messages: list[ModelMessage]) -> set[str]:
    return {part.tool_name for message in messages for part in message.parts if isinstance(part, ToolCallPart)}


class TestGoogleWorkspace:
    def test_defaults_to_gmail_and_calendar(self, gmail_server: FastMCP, calendar_server: FastMCP):
        capability = GoogleWorkspace[object](clients={'gmail': gmail_server, 'calendar': calendar_server})
        assert capability.services == ('gmail', 'calendar')

    def test_spec_keeps_runtime_clients_and_secrets_out(self):
        schema = json.dumps(AgentSpec.model_json_schema_with_capabilities([GoogleWorkspace]), sort_keys=True)
        assert '"clients"' not in schema
        assert '"access_token"' not in schema
        assert '"services"' in schema

    def test_token_value_is_not_represented(self):
        capability = GoogleWorkspace(services=('gmail',), access_token='secret-token')
        assert 'secret-token' not in repr(capability)

    @pytest.mark.parametrize(
        ('services', 'message'),
        [
            ((), 'at least one'),
            (('gmail', 'gmail'), 'duplicates'),
            (('mail',), 'Unknown Google Workspace service'),
        ],
    )
    def test_rejects_invalid_service_sets(self, services: tuple[str, ...], message: str):
        with pytest.raises(UserError, match=message):
            GoogleWorkspace(services=services)  # pyright: ignore[reportArgumentType]

    def test_explicit_access_token_builds_toolset(self):
        Agent(TestModel(), capabilities=[GoogleWorkspace(services=('gmail',), access_token='token')])

    def test_environment_access_token_builds_toolset(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv('GOOGLE_ACCESS_TOKEN', 'token')
        Agent(TestModel(), capabilities=[GoogleWorkspace(services=('gmail',))])

    @pytest.mark.parametrize('access_token', ['', ' '])
    def test_rejects_empty_explicit_access_token(self, access_token: str):
        with pytest.raises(UserError, match='`access_token` must not be empty'):
            GoogleWorkspace(services=('gmail',), access_token=access_token)

    @pytest.mark.parametrize('access_token', ['', ' '])
    def test_rejects_empty_environment_access_token(self, access_token: str, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv('GOOGLE_ACCESS_TOKEN', access_token)
        with pytest.raises(UserError, match='`GOOGLE_ACCESS_TOKEN` must not be empty'):
            Agent(TestModel(), capabilities=[GoogleWorkspace(services=('gmail',))])

    @pytest.mark.parametrize(
        ('service', 'url'),
        [
            ('gmail', 'https://gmailmcp.googleapis.com/mcp/v1'),
            ('drive', 'https://drivemcp.googleapis.com/mcp/v1'),
            ('docs', 'https://docsmcp.googleapis.com/mcp/v1'),
            ('sheets', 'https://sheetsmcp.googleapis.com/mcp/v1'),
            ('slides', 'https://slidesmcp.googleapis.com/mcp/v1'),
            ('calendar', 'https://calendarmcp.googleapis.com/mcp/v1'),
            ('chat', 'https://chatmcp.googleapis.com/mcp/v1'),
            ('people', 'https://people.googleapis.com/mcp/v1'),
        ],
    )
    def test_access_token_boundary_uses_official_url(
        self,
        service: GoogleWorkspaceService,
        url: str,
        monkeypatch: pytest.MonkeyPatch,
    ):
        captured: dict[str, object] = {}

        def mcp_toolset(client: object, *, id: str, auth: str) -> FunctionToolset[object]:
            captured.update(client=client, id=id, auth=auth)
            return FunctionToolset()

        monkeypatch.setattr('pydantic_ai_harness.google_workspace._capability.MCPToolset', mcp_toolset)
        Agent(
            TestModel(),
            capabilities=[GoogleWorkspace(services=(service,), access_token='token')],
        )
        assert captured == {
            'client': url,
            'id': f'google-workspace-{service}',
            'auth': 'token',
        }

    def test_rejects_allowlist_for_unselected_service(self):
        with pytest.raises(UserError, match='does not belong to a selected service'):
            GoogleWorkspace(services=('gmail',), allowed_tools='calendar_list_events')

    @pytest.mark.parametrize('allowed_tool', [1, None])
    def test_rejects_non_string_allowlist_member(self, allowed_tool: object):
        with pytest.raises(UserError, match='does not belong to a selected service'):
            GoogleWorkspace(
                services=('gmail',),
                allowed_tools=(allowed_tool,),  # pyright: ignore[reportArgumentType]
            )

    def test_rejects_client_for_unselected_service(self, calendar_server: FastMCP):
        with pytest.raises(UserError, match='Client configured for unselected service'):
            GoogleWorkspace(services=('gmail',), clients={'calendar': calendar_server})

    def test_missing_authentication_fails_before_a_run(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv('GOOGLE_ACCESS_TOKEN', raising=False)
        with pytest.raises(UserError, match='Google Workspace authentication requires'):
            Agent(TestModel(), capabilities=[GoogleWorkspace(services=('gmail',))])

    async def test_default_is_read_only_and_namespaced(
        self,
        gmail_server: FastMCP,
        calendar_server: FastMCP,
    ):
        capability = GoogleWorkspace[object](clients={'gmail': gmail_server, 'calendar': calendar_server})
        result = await Agent(TestModel(), capabilities=[capability]).run('Read my mail and calendar')
        assert tool_call_names(result.all_messages()) == {'gmail_search_threads', 'calendar_list_events'}

    @pytest.mark.parametrize(
        ('service', 'read_tools', 'write_tools'),
        [
            (
                'gmail',
                ('get_message', 'get_thread', 'list_drafts', 'list_labels', 'search_threads'),
                ('create_draft', 'label_message', 'label_thread', 'unlabel_message', 'unlabel_thread'),
            ),
            (
                'drive',
                (
                    'download_file_content',
                    'get_file_metadata',
                    'get_file_permissions',
                    'list_recent_files',
                    'read_file_content',
                    'search_files',
                ),
                ('copy_file', 'create_file'),
            ),
            ('docs', ('read_doc',), ('update_doc',)),
            (
                'sheets',
                ('get_spreadsheet', 'get_values'),
                ('insert_dimension', 'update_formulas', 'update_spreadsheet', 'update_values'),
            ),
            ('slides', ('read_presentation',), ('update_presentation',)),
            (
                'calendar',
                ('get_event', 'list_calendars', 'list_events', 'search_events', 'suggest_time'),
                ('create_event', 'delete_event', 'respond_to_event', 'update_event'),
            ),
            (
                'chat',
                ('list_memberships', 'list_messages', 'search_conversations', 'search_messages'),
                ('mark_as_read', 'mark_as_unread', 'send_message'),
            ),
            ('people', ('get_user_profile', 'search_contacts', 'search_directory_people'), ()),
        ],
    )
    async def test_each_service_is_selectable_and_read_only_by_default(
        self,
        service: GoogleWorkspaceService,
        read_tools: Sequence[str],
        write_tools: Sequence[str],
        workspace_server_factory: Callable[[Sequence[str], Sequence[str]], FastMCP],
    ):
        server = workspace_server_factory(read_tools, write_tools)
        capability = GoogleWorkspace[object](services=(service,), clients={service: server})
        result = await Agent(TestModel(), capabilities=[capability]).run('Read from Workspace')
        assert tool_call_names(result.all_messages()) == {f'{service}_{name}' for name in read_tools}

    async def test_write_mode_and_exact_allowlist_intersect(
        self,
        gmail_server: FastMCP,
        calendar_server: FastMCP,
    ):
        capability = GoogleWorkspace[object](
            clients={'gmail': gmail_server, 'calendar': calendar_server},
            read_only=False,
            allowed_tools=('gmail_create_draft', 'calendar_list_events'),
        )
        result = await Agent(TestModel(), capabilities=[capability]).run('Draft mail and read my calendar')
        assert tool_call_names(result.all_messages()) == {'gmail_create_draft', 'calendar_list_events'}

    async def test_single_string_allowlist_is_exact(self, gmail_server: FastMCP):
        capability = GoogleWorkspace[object](
            services=('gmail',), clients={'gmail': gmail_server}, read_only=False, allowed_tools='gmail_create_draft'
        )
        result = await Agent(TestModel(), capabilities=[capability]).run('Draft mail')
        assert tool_call_names(result.all_messages()) == {'gmail_create_draft'}

    async def test_write_mode_exposes_server_catalog(self, gmail_server: FastMCP):
        capability = GoogleWorkspace[object](services=('gmail',), clients={'gmail': gmail_server}, read_only=False)
        result = await Agent(TestModel(), capabilities=[capability]).run('Use Gmail')
        assert tool_call_names(result.all_messages()) == {'gmail_search_threads', 'gmail_create_draft'}

    async def test_selected_write_tools_execute_at_the_network_boundary(
        self,
        calendar_server: FastMCP,
        workspace_server_factory: Callable[[Sequence[str], Sequence[str]], FastMCP],
    ):
        docs_server = workspace_server_factory(('read_doc',), ('update_doc',))
        capability = GoogleWorkspace[object](
            services=('calendar', 'docs'),
            clients={'calendar': calendar_server, 'docs': docs_server},
            read_only=False,
            allowed_tools=('calendar_create_event', 'docs_update_doc'),
        )
        result = await Agent(
            TestModel(call_tools=['calendar_create_event', 'docs_update_doc']), capabilities=[capability]
        ).run('Create an event and update a document')
        assert tool_call_names(result.all_messages()) == {'calendar_create_event', 'docs_update_doc'}
        returns = [
            part for message in result.all_messages() for part in message.parts if isinstance(part, ToolReturnPart)
        ]
        assert {part.tool_name for part in returns} == {'calendar_create_event', 'docs_update_doc'}

    async def test_prebuilt_mcp_toolset_executes(self, gmail_server: FastMCP):
        capability = GoogleWorkspace[object](
            services=('gmail',), clients={'gmail': MCPToolset(gmail_server, id='caller-owned')}
        )
        result = await Agent(TestModel(), capabilities=[capability]).run('Read Gmail')
        assert tool_call_names(result.all_messages()) == {'gmail_search_threads'}

    async def test_approval_wrapper_defers_without_executing(self, gmail_server: FastMCP):
        def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        'gmail_create_draft',
                        {'to': 'user@example.com', 'body': 'Draft'},
                        tool_call_id='approval',
                    )
                ]
            )

        workspace = GoogleWorkspace[object](services=('gmail',), clients={'gmail': gmail_server}, read_only=False)
        result = await Agent(
            FunctionModel(model),
            toolsets=[workspace.get_toolset().approval_required()],
            output_type=[str, DeferredToolRequests],
        ).run('Draft mail')
        assert isinstance(result.output, DeferredToolRequests)
        assert [approval.tool_name for approval in result.output.approvals] == ['gmail_create_draft']
        assert not any(isinstance(part, ToolReturnPart) for message in result.all_messages() for part in message.parts)

    async def test_agent_executes_selected_workspace_tool(self, gmail_server: FastMCP):
        agent = Agent(
            TestModel(call_tools=['gmail_search_threads']),
            capabilities=[GoogleWorkspace(services=('gmail',), clients={'gmail': gmail_server})],
        )
        result = await agent.run('Find the launch email')
        assert tool_call_names(result.all_messages()) == {'gmail_search_threads'}
        returns = [
            part
            for message in result.all_messages()
            for part in message.parts
            if isinstance(part, ToolReturnPart) and part.tool_name == 'gmail_search_threads'
        ]
        assert len(returns) == 1
        assert 'Launch plan' in str(returns[0].content)

    async def test_instructions_can_be_disabled(self, gmail_server: FastMCP):
        capability = GoogleWorkspace(services=('gmail',), clients={'gmail': gmail_server}, include_instructions=False)
        assert capability.get_instructions() is None

    def test_agent_spec_example_loads(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv('GOOGLE_ACCESS_TOKEN', 'token')
        spec = tmp_path / 'agent.yaml'
        spec.write_text(
            'model: openai:gpt-5.6-sol\n'
            'capabilities:\n'
            '  - GoogleWorkspace:\n'
            '      services: [gmail, calendar]\n'
            '      allowed_tools: [gmail_search_threads, calendar_list_events]\n',
            encoding='utf-8',
        )
        agent = Agent.from_file(spec, custom_capability_types=[GoogleWorkspace], model=TestModel())
        assert isinstance(agent, Agent)
