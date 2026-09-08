"""Fixtures for the Atlassian capability tests.

Atlassian's Rovo MCP server is stood in for by a FastMCP server running in a child process and
served over real HTTP, so the credential travels the same path it does in production. Each tool
reports the `Authorization` header it received. One child process serves the whole session.
"""

from __future__ import annotations

import multiprocessing
import socket
import time
from collections.abc import Callable, Iterator

import pytest

# `fastmcp-slim` imports but raises ImportError for server support, so widen the skip.
pytest.importorskip('fastmcp.server', exc_type=ImportError)
pytest.importorskip('mcp')

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_request
from mcp.types import ToolAnnotations

from pydantic_ai_harness.atlassian import _capability


@pytest.fixture
def anyio_backend() -> str:
    return 'asyncio'


# `_tool` and `run_fake_server` execute only in the spawned child, which coverage does not measure.
def _tool(name: str) -> Callable[[str], dict[str, str | None]]:  # pragma: no cover
    def tool(query: str = '') -> dict[str, str | None]:
        """An Atlassian tool that reports the credentials it was called with."""
        return {'authorization': get_http_request().headers.get('authorization')}

    tool.__name__ = name
    return tool


def run_fake_server(*, host: str, port: int) -> None:  # pragma: no cover
    """Serve a stand-in Rovo server with Atlassian-style annotations; runs in the child process.

    The unannotated tool is the fail-closed case: the read-only filter must drop it.
    """
    server = FastMCP('atlassian-fake')
    server.tool(_tool('read_item'), annotations=ToolAnnotations(readOnlyHint=True))
    server.tool(_tool('write_item'), annotations=ToolAnnotations(readOnlyHint=False))
    server.tool(_tool('unannotated_item'))
    server.run(transport='http', host=host, port=port, path='/mcp', stateless_http=True, log_level='error')


@pytest.fixture(scope='session')
def fake_server() -> Iterator[str]:
    """A stand-in Rovo server in a spawned process, yielding its URL once it accepts connections.

    Spawn rather than fork: on Linux a forked child inherits the test's running event loop and
    cannot start the server's own.
    """
    host = '127.0.0.1'
    with socket.socket() as sock:
        sock.bind((host, 0))
        port: int = sock.getsockname()[1]
    process = multiprocessing.get_context('spawn').Process(
        target=run_fake_server, kwargs={'host': host, 'port': port}, daemon=True
    )
    process.start()
    deadline = time.monotonic() + 30
    while True:
        try:
            with socket.create_connection((host, port), timeout=0.2):
                break
        except OSError:
            if not process.is_alive() or time.monotonic() > deadline:  # pragma: no cover
                raise RuntimeError('the fake Atlassian server did not start') from None
            time.sleep(0.05)
    try:
        yield f'http://{host}:{port}/mcp'
    finally:
        process.terminate()
        process.join(timeout=5)


@pytest.fixture
def fake_atlassian(fake_server: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the capability's pinned endpoint at the stand-in server."""
    monkeypatch.setattr(_capability, '_ATLASSIAN_MCP_URL', fake_server)
