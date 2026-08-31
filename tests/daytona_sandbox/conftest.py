from __future__ import annotations

import importlib.util
from typing import TYPE_CHECKING

import pytest

_HAS_DAYTONA = importlib.util.find_spec('daytona') is not None
collect_ignore = [] if _HAS_DAYTONA else ['test_daytona_sandbox.py']

if TYPE_CHECKING or _HAS_DAYTONA:  # pragma: no branch - installed and slim jobs take opposite branches
    import daytona

    from .fake_daytona import FakeDaytona


@pytest.fixture
def anyio_backend() -> str:
    return 'asyncio'


if _HAS_DAYTONA:  # pragma: no branch - the fixture requires the SDK-backed fake

    @pytest.fixture
    def fake_daytona(monkeypatch: pytest.MonkeyPatch) -> FakeDaytona:
        fake = FakeDaytona()
        monkeypatch.setattr(daytona, 'AsyncDaytona', fake.client)
        return fake
