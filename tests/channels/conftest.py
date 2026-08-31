import pytest


@pytest.fixture
def anyio_backend() -> str:
    """Pydantic AI's agent run lifecycle currently creates asyncio tasks."""
    return 'asyncio'
