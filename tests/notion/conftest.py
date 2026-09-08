"""Fixtures for the Notion capability tests.

Notion's hosted server is stood in for by a FastMCP server in a spawned child process, served over
real HTTP at the capability's endpoint, so its tools reach the agent over the transport they use in
production. The stand-in carries three tools: one the capability classifies as a read, one it
classifies as a write, and one under a name Notion has never published.
"""

from __future__ import annotations

import multiprocessing
import socket
import time
from collections.abc import Iterator

import pytest

# `fastmcp-slim` imports but raises ImportError for server support, so widen the skip.
pytest.importorskip('fastmcp.server', exc_type=ImportError)

from fastmcp import FastMCP

from pydantic_ai_harness.notion import _capability


@pytest.fixture
def anyio_backend() -> str:
    return 'asyncio'


def run_stand_in_server(host: str, port: int) -> None:  # pragma: no cover - runs in the child process
    """Serve the stand-in Notion tools; the spawned child runs this and coverage cannot see it."""
    server = FastMCP('notion-fake')

    @server.tool(name='notion-search')
    def search() -> str:
        """A tool the capability classifies as a read."""
        return 'searched'

    @server.tool(name='notion-update-page')
    def update_page() -> str:
        """A tool the capability classifies as a write."""
        return 'updated'

    @server.tool(name='notion-summarize-page')
    def summarize_page() -> str:
        """A tool under a name Notion has never published."""
        return 'summarized'

    server.run(transport='http', host=host, port=port, path='/mcp', stateless_http=True, log_level='error')


@pytest.fixture(scope='session')
def notion_url() -> Iterator[str]:
    """Run the stand-in on a loopback port and yield its MCP endpoint once it accepts connections.

    Spawn rather than fork: a forked child inherits the test's running event loop and cannot start
    the server's own.
    """
    host = '127.0.0.1'
    with socket.socket() as probe:
        probe.bind((host, 0))
        port = probe.getsockname()[1]
    process = multiprocessing.get_context('spawn').Process(target=run_stand_in_server, args=(host, port), daemon=True)
    process.start()
    deadline = time.monotonic() + 30
    while True:
        try:
            with socket.create_connection((host, port), timeout=0.2):
                break
        except OSError:
            # A server that never starts must fail the suite rather than hang it.
            assert process.is_alive() and time.monotonic() < deadline, 'the stand-in Notion server did not start'
            time.sleep(0.05)
    try:
        yield f'http://{host}:{port}/mcp'
    finally:
        process.terminate()
        process.join(timeout=5)


@pytest.fixture
def fake_notion(notion_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the capability's endpoint at the stand-in server."""
    monkeypatch.setattr(_capability, '_NOTION_MCP_URL', notion_url)
