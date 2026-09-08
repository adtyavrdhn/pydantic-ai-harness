"""Fixtures for the Stripe capability tests.

Stripe's endpoint is stood in for by a FastMCP server running in a child process and served over
real HTTP, so what `read_only` hides is observed the way an agent sees it: over `tools/list` on the
wire. The stand-in serves one tool Stripe documents as a read, its write tool, and one tool this
package has never heard of.
"""

from __future__ import annotations

import multiprocessing
import socket
import time
from collections.abc import Callable, Iterator

import pytest

# `fastmcp-slim` imports but raises ImportError for server support, so widen the skip.
pytest.importorskip('fastmcp.server', exc_type=ImportError)

from fastmcp import FastMCP

from pydantic_ai_harness.stripe import _capability

_HOST = '127.0.0.1'
_TOOL_NAMES = ('stripe_api_read', 'stripe_api_write', 'stripe_unlisted_tool')


@pytest.fixture
def anyio_backend() -> str:
    return 'asyncio'


# `_tool` and `run_fake_server` execute only in the spawned child, which coverage does not measure.
def _tool(name: str) -> Callable[[], str]:  # pragma: no cover
    def tool() -> str:
        """A Stripe tool that reports its own name."""
        return name

    tool.__name__ = name
    return tool


def run_fake_server(*, port: int) -> None:  # pragma: no cover
    """Serve the stand-in Stripe MCP server; runs in the child process."""
    server = FastMCP('stripe-fake')
    for name in _TOOL_NAMES:
        server.tool(_tool(name))
    # Stripe's server is stateless; matching that keeps the fake honest.
    server.run(transport='http', host=_HOST, port=port, path='/mcp', stateless_http=True, log_level='error')


@pytest.fixture(scope='session')
def stripe_server_url() -> Iterator[str]:
    """Run the stand-in in a spawned process and yield its URL once it accepts connections.

    Spawn rather than fork: on Linux a forked child inherits the test's running event loop and
    cannot start the server's own.
    """
    with socket.socket() as probe:
        probe.bind((_HOST, 0))
        port = probe.getsockname()[1]
    process = multiprocessing.get_context('spawn').Process(target=run_fake_server, kwargs={'port': port}, daemon=True)
    process.start()
    deadline = time.monotonic() + 30
    while True:
        try:
            with socket.create_connection((_HOST, port), timeout=0.2):
                break
        except OSError:
            if not process.is_alive() or time.monotonic() > deadline:  # pragma: no cover
                raise RuntimeError('the fake Stripe server did not start') from None
            time.sleep(0.05)
    try:
        yield f'http://{_HOST}:{port}/mcp'
    finally:
        process.terminate()
        process.join(timeout=5)


@pytest.fixture
def stripe_server(stripe_server_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the capability's endpoint at the stand-in server."""
    monkeypatch.setattr(_capability, '_STRIPE_MCP_URL', stripe_server_url)
