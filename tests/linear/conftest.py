"""In-process MCP server fixtures for the Linear capability tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from mcp.server.fastmcp import FastMCP

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def anyio_backend() -> str:
    return 'asyncio'


@pytest.fixture
def linear_server() -> Iterator[FastMCP]:
    server = FastMCP('linear-fake')

    @server.tool()
    def get_issue(issue_id: str) -> dict[str, str]:
        """Get one Linear issue."""
        return {'id': issue_id, 'title': 'Fix the build'}

    @server.tool()
    def list_issues() -> list[dict[str, str]]:
        """List Linear issues."""
        return [{'id': 'ENG-123', 'title': 'Fix the build'}]

    @server.tool()
    def create_issue(title: str) -> dict[str, str]:
        """Create a Linear issue."""
        return {'id': 'ENG-124', 'title': title}

    yield server
