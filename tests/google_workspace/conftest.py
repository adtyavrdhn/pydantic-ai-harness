"""Fixtures for Google Workspace capability tests.

Google's servers are stood in for by FastMCP servers on localhost, served over real
HTTP so the bearer token and the wire annotations travel the same path they do in
production. Each fake tool returns the `Authorization` header it received.
"""

from __future__ import annotations

import asyncio
import gc
import socket
from collections.abc import AsyncIterator, Callable

import pytest

pytest.importorskip('fastmcp')
pytest.importorskip('mcp')

import uvicorn
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_request
from mcp.types import ToolAnnotations

from pydantic_ai_harness.google_workspace import _capability


@pytest.fixture
def anyio_backend() -> str:
    return 'asyncio'


def _tool(name: str) -> Callable[[str], dict[str, str | None]]:
    def tool(query: str = '') -> dict[str, str | None]:
        """A Workspace tool that reports the credentials it was called with."""
        return {'tool': name, 'authorization': get_http_request().headers.get('authorization')}

    tool.__name__ = name
    return tool


class FakeGoogle:
    """Serves stand-ins for Google's product servers and points the capability at them."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._monkeypatch = monkeypatch
        self._servers: list[tuple[uvicorn.Server, asyncio.Task[None]]] = []

    async def serve(
        self,
        service: str,
        *,
        read_tools: tuple[str, ...] = (),
        write_tools: tuple[str, ...] = (),
        unannotated_tools: tuple[str, ...] = (),
    ) -> str:
        """Serve one product with Google-style annotations and return its URL."""
        server = FastMCP(f'{service}-fake')
        for name in read_tools:
            server.tool(_tool(name), annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False))
        for name in write_tools:
            server.tool(_tool(name), annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
        for name in unannotated_tools:
            server.tool(_tool(name))

        # Bind the port here and hand the socket to uvicorn, so no other process can grab it in between.
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
        # Google's servers are stateless; matching that also avoids leaking per-session streams.
        app = server.http_app(path='/mcp', stateless_http=True)
        uvicorn_server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
        task = asyncio.create_task(uvicorn_server.serve(sockets=[sock]))
        while not uvicorn_server.started:
            if task.done():
                task.result()
            await asyncio.sleep(0.01)
        self._servers.append((uvicorn_server, task))

        url = f'http://127.0.0.1:{port}/mcp'
        self._monkeypatch.setitem(_capability._MCP_URLS, service, url)  # pyright: ignore[reportPrivateUsage]
        return url

    async def close(self) -> None:
        for uvicorn_server, task in self._servers:
            uvicorn_server.should_exit = True
            await task
        # Collect the server SDK's leaked per-request streams here, inside the test's warning filter.
        gc.collect()


@pytest.fixture
async def fake_google(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[FakeGoogle]:
    fake = FakeGoogle(monkeypatch)
    try:
        yield fake
    finally:
        await fake.close()


@pytest.fixture
async def gmail(fake_google: FakeGoogle) -> str:
    """A Gmail stand-in with one read and one write tool."""
    return await fake_google.serve('gmail', read_tools=('search_threads',), write_tools=('create_draft',))


@pytest.fixture
async def calendar(fake_google: FakeGoogle) -> str:
    """A Calendar stand-in with one read and one write tool."""
    return await fake_google.serve('calendar', read_tools=('list_events',), write_tools=('create_event',))
