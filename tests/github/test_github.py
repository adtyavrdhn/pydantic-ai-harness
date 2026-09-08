"""Tests for the connection `GitHub` hands to `MCPToolset`."""

from __future__ import annotations

import pytest
from fastmcp.client.auth import BearerAuth
from fastmcp.client.transports import StreamableHttpTransport
from pydantic_ai import Agent
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.exceptions import UserError
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.toolsets import AbstractToolset

from pydantic_ai_harness.github import GitHub

_TENANT_URL = 'https://copilot-api.tenant.ghe.com/mcp/'


@pytest.fixture(autouse=True)
def _no_ambient_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """A developer's own `GITHUB_TOKEN` must not decide what these tests assert."""
    monkeypatch.delenv('GITHUB_TOKEN', raising=False)


def _transport(toolset: AbstractToolset[None]) -> StreamableHttpTransport:
    assert isinstance(toolset, MCPToolset)
    transport = toolset.client.transport
    assert isinstance(transport, StreamableHttpTransport)
    return transport


def test_default_connects_to_the_hosted_endpoint():
    toolset = GitHub(auth='token').get_toolset()

    assert _transport(toolset).url == 'https://api.githubcopilot.com/mcp/'
    # The default hands back every tool GitHub serves: no read-only header, no filter.
    assert _transport(toolset).headers == {}
    assert type(toolset) is MCPToolset


def test_url_overrides_the_endpoint_for_a_data_residency_tenant():
    assert _transport(GitHub(auth='token', url=_TENANT_URL).get_toolset()).url == _TENANT_URL


def test_read_only_sends_githubs_read_only_header():
    toolset = GitHub(auth='token', read_only=True).get_toolset()

    # GitHub narrows the catalog server-side, so the header is the whole mechanism.
    assert _transport(toolset).headers == {'X-MCP-Readonly': 'true'}
    assert type(toolset) is MCPToolset


def test_token_reaches_the_transport():
    auth = _transport(GitHub(auth='github_pat_secret').get_toolset()).auth

    assert isinstance(auth, BearerAuth)
    assert auth.token.get_secret_value() == 'github_pat_secret'


def test_token_stays_out_of_repr():
    assert 'github_pat_secret' not in repr(GitHub(auth='github_pat_secret'))


def test_token_falls_back_to_the_environment(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv('GITHUB_TOKEN', 'github_pat_from_env')

    auth = _transport(GitHub().get_toolset()).auth

    assert isinstance(auth, BearerAuth)
    assert auth.token.get_secret_value() == 'github_pat_from_env'


def test_missing_token_raises_user_error():
    with pytest.raises(UserError, match='GITHUB_TOKEN'):
        GitHub().get_toolset()


def test_toolset_id_defaults_to_github_and_follows_capability_id():
    assert GitHub(auth='token').get_toolset().id == 'github'
    assert GitHub(auth='token', id='tenant-github').get_toolset().id == 'tenant-github'


def test_spec_schema_exposes_only_the_public_fields():
    params = AgentSpec.model_json_schema_with_capabilities([GitHub])['$defs']['spec_params_GitHub']

    # `auth` is absent, so a checked-in spec file cannot carry a GitHub token.
    assert set(params['properties']) == {'id', 'description', 'defer_loading', 'url', 'read_only'}
    assert 'required' not in params


def test_spec_round_trip_rebuilds_the_capability(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv('GITHUB_TOKEN', 'github_pat_from_env')

    agent = Agent.from_spec(
        {'model': 'test', 'capabilities': [{'GitHub': {'id': 'tenant-github', 'url': _TENANT_URL, 'read_only': True}}]},
        custom_capability_types=[GitHub],
    )
    (capability,) = [c for c in agent.root_capability.capabilities if isinstance(c, GitHub)]

    assert capability.id == 'tenant-github'
    assert capability.url == _TENANT_URL
    assert capability.read_only is True


def test_spec_naming_the_token_is_rejected():
    # The token belongs in `$GITHUB_TOKEN`, so a spec file cannot smuggle one in.
    with pytest.raises(ValueError, match='auth'):
        Agent.from_spec(
            {'model': 'test', 'capabilities': [{'GitHub': {'auth': 'github_pat_secret'}}]},
            custom_capability_types=[GitHub],
        )
