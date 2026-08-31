import pytest


@pytest.fixture
def anyio_backend() -> str:
    """Channels use asyncio because `ChannelHost` owns asyncio tasks and locks."""
    return 'asyncio'
