"""A stand-in AWS MCP server in a child process, reached over HTTP.

`AWS` takes a URL, so the fake is reached the way the managed server is: no injected client and no
patched module constant. It publishes one tool annotated read-only, one annotated as changing
state, and one with no annotations at all -- everything the read-only filter has to decide between.

The server runs in a child because the in-process streamable-HTTP server leaks anyio memory
streams, and this suite turns warnings into errors.
"""

from __future__ import annotations

import importlib.util
import multiprocessing
import socket
import time
from collections.abc import Iterator

import pytest

collect_ignore = (
    ['test_aws.py'] if importlib.util.find_spec('mcp') is None or importlib.util.find_spec('fastmcp') is None else []
)


@pytest.fixture
def anyio_backend() -> str:
    return 'asyncio'


def run_fake_server(host: str, port: int) -> None:  # pragma: no cover - runs in the spawned child
    """Serve the stand-in catalog. The child process entry point."""
    from fastmcp import FastMCP  # noqa: PLC0415
    from mcp.types import ToolAnnotations  # noqa: PLC0415

    def describe_thing() -> str:
        """A tool the server marks read-only."""
        return 'described'

    def change_thing() -> str:
        """A tool the server marks as changing state."""
        return 'changed'

    def unannotated_thing() -> str:
        """A tool the server ships with no annotations at all."""
        return 'unknown'

    server = FastMCP('aws-stand-in')
    server.tool(describe_thing, annotations=ToolAnnotations(readOnlyHint=True))
    server.tool(change_thing, annotations=ToolAnnotations(readOnlyHint=False))
    server.tool(unannotated_thing)
    server.run(transport='http', host=host, port=port, path='/mcp', stateless_http=True, log_level='error')


@pytest.fixture(scope='module')
def aws_mcp_url() -> Iterator[str]:
    """Serve the stand-in for the module and yield its MCP endpoint."""
    host = '127.0.0.1'
    with socket.socket() as probe:
        probe.bind((host, 0))
        port = probe.getsockname()[1]
    # Spawn rather than fork: a forked child inherits this process's event loop and cannot start its own.
    process = multiprocessing.get_context('spawn').Process(target=run_fake_server, args=(host, port), daemon=True)
    process.start()
    deadline = time.monotonic() + 30
    while True:
        try:
            with socket.create_connection((host, port), timeout=0.2):
                break
        except OSError:
            if not process.is_alive() or time.monotonic() > deadline:  # pragma: no cover - the child never started
                raise RuntimeError('the stand-in AWS MCP server did not start') from None
            time.sleep(0.05)
    try:
        yield f'http://{host}:{port}/mcp'
    finally:
        process.terminate()
        process.join(timeout=5)
