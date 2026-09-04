"""In-process Supabase MCP stand-in."""

from __future__ import annotations

import importlib.util
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

collect_ignore = (
    ['test_supabase.py']
    if importlib.util.find_spec('mcp') is None or importlib.util.find_spec('fastmcp') is None
    else []
)


@pytest.fixture
def anyio_backend() -> str:
    return 'asyncio'


@pytest.fixture
def calls() -> list[str]:
    return []


@pytest.fixture
def supabase_server(calls: list[str]) -> FastMCP:
    from mcp.server.fastmcp.server import FastMCP, Settings  # noqa: PLC0415

    Settings.model_rebuild()
    server = FastMCP('supabase-fake')

    @server.tool()
    def list_tables() -> list[str]:
        """List database tables."""
        calls.append('list_tables')
        return ['public.todos']

    @server.tool()
    def execute_sql(query: str) -> str:
        """Execute SQL against the database."""
        calls.append(f'execute_sql:{query}')
        return 'ok'

    @server.tool()
    def apply_migration(name: str, query: str) -> str:
        """Apply a database migration."""
        calls.append(f'apply_migration:{name}:{query}')
        return 'applied'

    @server.tool()
    def query_logs(query: str = 'select 1') -> list[str]:
        """Query project logs."""
        calls.append(f'query_logs:{query}')
        return []

    @server.tool()
    def get_project_url() -> str:
        """Get the project URL."""
        calls.append('get_project_url')
        return 'https://example.supabase.co'

    @server.tool()
    def search_docs(query: str = 'database') -> list[str]:
        """Search Supabase documentation."""
        calls.append(f'search_docs:{query}')
        return []

    @server.tool()
    def list_edge_functions() -> list[str]:
        """List Edge Functions."""
        calls.append('list_edge_functions')
        return []

    @server.tool()
    def deploy_edge_function(name: str) -> str:
        """Deploy an Edge Function."""
        calls.append(f'deploy_edge_function:{name}')
        return 'deployed'

    @server.tool()
    def list_storage_buckets() -> list[str]:
        """List Storage buckets."""
        calls.append('list_storage_buckets')
        return []

    @server.tool()
    def create_branch(name: str) -> str:
        """Create a database branch."""
        calls.append(f'create_branch:{name}')
        return name

    return server
