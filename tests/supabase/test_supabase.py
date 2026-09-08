"""Tests for the connection `Supabase` hands to `MCPToolset`."""

from __future__ import annotations

import pytest
from fastmcp.client.auth import BearerAuth
from fastmcp.client.transports import StreamableHttpTransport
from pydantic_ai import Agent
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.exceptions import UserError
from pydantic_ai.mcp import MCPToolset

from pydantic_ai_harness.supabase import Supabase


@pytest.fixture(autouse=True)
def _no_ambient_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """A developer's own `SUPABASE_ACCESS_TOKEN` must not decide what these tests assert."""
    monkeypatch.delenv('SUPABASE_ACCESS_TOKEN', raising=False)


def _transport(toolset: MCPToolset[None]) -> StreamableHttpTransport:
    transport = toolset.client.transport
    assert isinstance(transport, StreamableHttpTransport)
    return transport


def test_project_ref_scopes_the_connection_at_the_server():
    toolset = Supabase(project_ref='dev-project', auth='token').get_toolset()

    assert _transport(toolset).url == 'https://mcp.supabase.com/mcp?project_ref=dev-project'
    # The default hands back every tool the project-scoped server serves: no approval gate, no filter.
    assert type(toolset) is MCPToolset


def test_read_only_narrows_at_the_server():
    toolset = Supabase(project_ref='dev-project', auth='token', read_only=True).get_toolset()

    # Supabase runs SQL as a read-only Postgres user and withholds its write tools, so the query
    # parameter is the whole mechanism; nothing narrows the catalog client-side.
    assert _transport(toolset).url == 'https://mcp.supabase.com/mcp?project_ref=dev-project&read_only=true'
    assert type(toolset) is MCPToolset


def test_token_reaches_the_transport():
    auth = _transport(Supabase(project_ref='dev-project', auth='sbp_secret').get_toolset()).auth

    assert isinstance(auth, BearerAuth)
    assert auth.token.get_secret_value() == 'sbp_secret'


def test_token_stays_out_of_repr():
    assert 'sbp_secret' not in repr(Supabase(project_ref='dev-project', auth='sbp_secret'))


def test_token_falls_back_to_the_environment(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv('SUPABASE_ACCESS_TOKEN', 'sbp_from_env')

    auth = _transport(Supabase(project_ref='dev-project').get_toolset()).auth

    assert isinstance(auth, BearerAuth)
    assert auth.token.get_secret_value() == 'sbp_from_env'


def test_missing_token_is_reported():
    with pytest.raises(UserError, match='Supabase needs a token'):
        Supabase(project_ref='dev-project').get_toolset()


def test_blank_project_ref_is_reported():
    # An unscoped connection reaches every project the credential can, so a blank ref is not a
    # connection this capability will build.
    with pytest.raises(UserError, match='Supabase needs a project ref'):
        Supabase(project_ref='', auth='token').get_toolset()


def test_toolset_id_defaults_to_supabase_and_follows_capability_id():
    assert Supabase(project_ref='dev-project', auth='token').get_toolset().id == 'supabase'
    assert Supabase(project_ref='dev-project', auth='token', id='primary').get_toolset().id == 'primary'


def test_spec_schema_leaves_the_token_out():
    params = AgentSpec.model_json_schema_with_capabilities([Supabase])['$defs']['spec_params_Supabase']

    assert set(params['properties']) == {'id', 'description', 'defer_loading', 'project_ref', 'read_only'}
    assert params['required'] == ['project_ref']


def test_spec_carrying_a_token_is_rejected():
    # A checked-in spec file must not be able to hold a Supabase credential.
    with pytest.raises(ValueError, match='auth'):
        Agent.from_spec(
            {'model': 'test', 'capabilities': [{'Supabase': {'project_ref': 'dev-project', 'auth': 'sbp_secret'}}]},
            custom_capability_types=[Supabase],
        )


def test_spec_round_trip_rebuilds_the_capability(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv('SUPABASE_ACCESS_TOKEN', 'sbp_from_env')

    agent = Agent.from_spec(
        {'model': 'test', 'capabilities': [{'Supabase': {'project_ref': 'dev-project', 'read_only': True}}]},
        custom_capability_types=[Supabase],
    )
    (capability,) = [c for c in agent.root_capability.capabilities if isinstance(c, Supabase)]

    assert capability.project_ref == 'dev-project'
    assert capability.read_only is True
