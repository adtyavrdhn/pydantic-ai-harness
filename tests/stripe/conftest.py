"""Fixtures for the Stripe capability tests."""

from __future__ import annotations

import pytest

pytest.importorskip('fastmcp')

from mcp.server.fastmcp.server import FastMCP, Settings


@pytest.fixture
def anyio_backend() -> str:
    return 'asyncio'


@pytest.fixture
def stripe_server() -> FastMCP:
    """In-process stand-in for Stripe's hosted MCP endpoint."""
    Settings.model_rebuild()
    server = FastMCP('stripe-fake')

    @server.tool()
    def stripe_api_search(query: str) -> list[str]:
        """Search the Stripe API catalog."""
        return [f'GET /v1/{query}']

    @server.tool()
    def stripe_api_details(method: str) -> dict[str, str]:
        """Describe a Stripe API method."""
        return {'method': method}

    @server.tool()
    def stripe_api_read(method: str) -> dict[str, str]:
        """Read from the Stripe API."""
        return {'method': method, 'mode': 'read'}

    @server.tool()
    def stripe_api_write(method: str) -> dict[str, str]:
        """Write to the Stripe API."""
        return {'method': method, 'mode': 'write'}

    @server.tool()
    def search_stripe_documentation(question: str) -> str:
        """Search Stripe documentation."""
        return question

    @server.tool()
    def send_stripe_mcp_feedback(message: str) -> str:
        """Send feedback outside the configured Stripe account."""
        return message

    return server
