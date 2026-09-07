"""Fixtures for Google Workspace capability tests.

Google's servers are stood in for by FastMCP servers running in a child process and
served over real HTTP, so the bearer token and the wire annotations travel the same
path they do in production. Each fake tool returns the `Authorization` header it
received. One child process serves each distinct tool set for the whole session.
"""

from __future__ import annotations

import multiprocessing
import socket
import time
from collections.abc import Callable, Generator, Iterator
from contextlib import ExitStack, contextmanager

import pytest

# `fastmcp-slim` imports but raises ImportError for server support, so widen the skip.
pytest.importorskip('fastmcp.server', exc_type=ImportError)
pytest.importorskip('mcp')

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_request
from mcp.types import ToolAnnotations

from pydantic_ai_harness.google_workspace import _capability

ToolNames = tuple[str, ...]


@pytest.fixture
def anyio_backend() -> str:
    return 'asyncio'


# `_tool` and `run_fake_server` execute only in the spawned child, which coverage does not measure.
def _tool(name: str) -> Callable[[str], dict[str, str | None]]:  # pragma: no cover
    def tool(query: str = '') -> dict[str, str | None]:
        """A Workspace tool that reports the credentials it was called with."""
        return {'tool': name, 'authorization': get_http_request().headers.get('authorization')}

    tool.__name__ = name
    return tool


def run_fake_server(  # pragma: no cover
    *, host: str, port: int, read_tools: ToolNames, write_tools: ToolNames, unannotated_tools: ToolNames
) -> None:
    """Serve one stand-in product server with Google-style annotations; runs in the child process."""
    server = FastMCP('google-workspace-fake')
    for name in read_tools:
        server.tool(_tool(name), annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False))
    for name in write_tools:
        server.tool(_tool(name), annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
    for name in unannotated_tools:
        server.tool(_tool(name))
    # Google's servers are stateless; matching that keeps the fake honest.
    server.run(transport='http', host=host, port=port, path='/mcp', stateless_http=True, log_level='error')


def _free_port(host: str) -> int:
    with socket.socket() as sock:
        sock.bind((host, 0))
        return sock.getsockname()[1]


@contextmanager
def _server_process(
    read_tools: ToolNames, write_tools: ToolNames, unannotated_tools: ToolNames
) -> Generator[str, None, None]:
    """Run a stand-in server in a spawned process and yield its URL once it accepts connections.

    Spawn rather than fork: on Linux a forked child inherits the test's running event
    loop and cannot start the server's own.
    """
    host = '127.0.0.1'
    port = _free_port(host)
    process = multiprocessing.get_context('spawn').Process(
        target=run_fake_server,
        kwargs={
            'host': host,
            'port': port,
            'read_tools': read_tools,
            'write_tools': write_tools,
            'unannotated_tools': unannotated_tools,
        },
        daemon=True,
    )
    process.start()
    deadline = time.monotonic() + 30
    while True:
        try:
            with socket.create_connection((host, port), timeout=0.2):
                break
        except OSError:
            if not process.is_alive() or time.monotonic() > deadline:
                raise RuntimeError('the fake Google server did not start') from None  # pragma: no cover
            time.sleep(0.05)
    try:
        yield f'http://{host}:{port}/mcp'
    finally:
        process.terminate()
        process.join(timeout=5)


class FakeServers:
    """Session-wide cache of child-process servers, one per distinct tool set."""

    def __init__(self) -> None:
        self._stack = ExitStack()
        self._urls: dict[tuple[ToolNames, ToolNames, ToolNames], str] = {}

    def url_for(self, read_tools: ToolNames, write_tools: ToolNames, unannotated_tools: ToolNames) -> str:
        key = (read_tools, write_tools, unannotated_tools)
        if key not in self._urls:
            self._urls[key] = self._stack.enter_context(_server_process(*key))
        return self._urls[key]

    def close(self) -> None:
        self._stack.close()


class FakeGoogle:
    """Points the capability's product URLs at stand-in servers for one test."""

    def __init__(self, servers: FakeServers, monkeypatch: pytest.MonkeyPatch) -> None:
        self._servers = servers
        self._monkeypatch = monkeypatch

    def serve(
        self,
        service: str,
        *,
        read_tools: ToolNames = (),
        write_tools: ToolNames = (),
        unannotated_tools: ToolNames = (),
    ) -> str:
        """Route `service` to a stand-in with these tools and return its URL."""
        assert service in _capability._MCP_URLS, f'{service!r} has no endpoint'  # pyright: ignore[reportPrivateUsage]
        url = self._servers.url_for(read_tools, write_tools, unannotated_tools)
        self._monkeypatch.setitem(_capability._MCP_URLS, service, url)  # pyright: ignore[reportPrivateUsage]
        return url


@pytest.fixture(scope='session')
def fake_servers() -> Iterator[FakeServers]:
    servers = FakeServers()
    try:
        yield servers
    finally:
        servers.close()


@pytest.fixture
def fake_google(fake_servers: FakeServers, monkeypatch: pytest.MonkeyPatch) -> FakeGoogle:
    return FakeGoogle(fake_servers, monkeypatch)


@pytest.fixture
def gmail(fake_google: FakeGoogle) -> str:
    """A Gmail stand-in with one read and one write tool."""
    return fake_google.serve('gmail', read_tools=('search_threads',), write_tools=('create_draft',))


@pytest.fixture
def calendar(fake_google: FakeGoogle) -> str:
    """A Calendar stand-in with one read and one write tool."""
    return fake_google.serve('calendar', read_tools=('list_events',), write_tools=('create_event',))
