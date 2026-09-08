"""A stand-in Cloudflare managed MCP server, served over real HTTP.

The fake runs in a child process behind a URL, so the capability reaches it the same way it
reaches Cloudflare: `url` picks the server, the credential travels as a request header, and the
`readOnlyHint` annotations arrive over the wire rather than being handed to a filter directly.
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
from fastmcp.server.dependencies import get_http_request
from mcp.types import ToolAnnotations


def run_fake_server(*, host: str, port: int) -> None:  # pragma: no cover - runs in the child process
    """Serve one read, one write, and one unannotated tool, each reporting its credential."""
    server = FastMCP('cloudflare-fake')

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
    def read_item() -> str | None:
        """Read a resource."""
        return get_http_request().headers.get('authorization')

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False))
    def write_item() -> str | None:
        """Change a resource."""
        return get_http_request().headers.get('authorization')

    @server.tool
    def unannotated_item() -> str | None:
        """Do something Cloudflare left unannotated."""
        return get_http_request().headers.get('authorization')

    server.run(transport='http', host=host, port=port, path='/mcp', stateless_http=True, log_level='error')


@pytest.fixture
def anyio_backend() -> str:
    return 'asyncio'


@pytest.fixture(scope='session')
def cloudflare_server() -> Iterator[str]:
    """The stand-in server's URL, once it accepts connections."""
    host = '127.0.0.1'
    with socket.socket() as probe:
        probe.bind((host, 0))
        port: int = probe.getsockname()[1]
    # Spawn rather than fork: a forked child inherits the test's running event loop
    # and cannot start the server's own.
    process = multiprocessing.get_context('spawn').Process(
        target=run_fake_server, kwargs={'host': host, 'port': port}, daemon=True
    )
    process.start()
    deadline = time.monotonic() + 30
    while True:
        try:
            with socket.create_connection((host, port), timeout=0.2):
                break
        except OSError:  # pragma: no cover - timing-dependent retry
            if not process.is_alive() or time.monotonic() > deadline:
                raise RuntimeError('the stand-in Cloudflare server did not start') from None
            time.sleep(0.05)
    try:
        yield f'http://{host}:{port}/mcp'
    finally:
        process.terminate()
        process.join(timeout=5)
