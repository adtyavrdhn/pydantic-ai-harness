"""Tests for the Daytona sandbox capability."""

from __future__ import annotations

import inspect

import pytest
from pydantic_ai.models.test import TestModel
from pydantic_ai.sandboxes import SandboxRef
from pydantic_ai.tools import RunContext
from pydantic_ai.usage import RunUsage

import pydantic_ai_harness
import pydantic_ai_harness.daytona_sandbox as daytona_sandbox
from pydantic_ai_harness.daytona_sandbox import DaytonaSandbox, DaytonaSandboxBackend

from .fake_daytona import FakeDaytona

pytestmark = pytest.mark.anyio(backends=['asyncio'])


def _ctx(run_id: str = 'run-1') -> RunContext[None]:
    return RunContext(deps=None, model=TestModel(), usage=RunUsage(), run_id=run_id)


class TestLifecycle:
    async def test_acquire_reuses_one_named_sandbox_and_returns_id_refs(self, fake_daytona: FakeDaytona) -> None:
        capability = DaytonaSandbox[None]()
        first = await capability.acquire_sandbox(_ctx())
        second = await capability.acquire_sandbox(_ctx())
        assert first == second
        assert first.sandbox_id == fake_daytona.sandboxes[0].id
        assert len(fake_daytona.sandboxes) == 1
        assert fake_daytona.closed_clients == 3

    async def test_different_runs_use_different_names(self, fake_daytona: FakeDaytona) -> None:
        capability = DaytonaSandbox[None]()
        await capability.acquire_sandbox(_ctx('run-1'))
        await capability.acquire_sandbox(_ctx('run-2'))
        assert fake_daytona.create_params[0].name != fake_daytona.create_params[1].name

    async def test_get_sandbox_reconnects_by_ref(self, fake_daytona: FakeDaytona) -> None:
        sandbox = fake_daytona.sandbox()
        backend = await DaytonaSandbox[None]().get_sandbox(_ctx(), SandboxRef(sandbox_id=sandbox.id))
        assert isinstance(backend, DaytonaSandboxBackend)
        assert backend.sandbox_id == sandbox.id

    async def test_release_deletes_without_starting(self, fake_daytona: FakeDaytona) -> None:
        capability = DaytonaSandbox[None]()
        ref = await capability.acquire_sandbox(_ctx())
        sandbox = fake_daytona.sandboxes[0]
        await capability.release_sandbox(_ctx(), ref)
        assert sandbox.start_calls == []
        assert sandbox.deleted is True

    async def test_attached_sandbox_is_not_owned(self, fake_daytona: FakeDaytona) -> None:
        capability = DaytonaSandbox[None](sandbox_id='existing')
        ref = await capability.acquire_sandbox(_ctx())
        await capability.release_sandbox(_ctx(), ref)
        assert ref == SandboxRef(sandbox_id='existing')
        assert fake_daytona.sandboxes == []

    async def test_failed_acquisition_close_does_not_delete(self, fake_daytona: FakeDaytona) -> None:
        fake_daytona.close_error = RuntimeError('close failed')
        with pytest.raises(RuntimeError, match='close failed'):
            await DaytonaSandbox[None]().acquire_sandbox(_ctx())
        assert fake_daytona.sandboxes[0].deleted is False


class TestConfiguration:
    def test_defaults_and_normalization(self) -> None:
        capability = DaytonaSandbox(workdir='/workspace/../repo')
        assert capability.snapshot is None
        assert capability.auto_stop_minutes == 60
        assert capability.network_block_all is False
        assert capability.workdir == '/repo'

    def test_configuration_is_keyword_only(self) -> None:
        assert all(
            parameter.kind is inspect.Parameter.KEYWORD_ONLY
            for parameter in inspect.signature(DaytonaSandbox).parameters.values()
        )

    @pytest.mark.parametrize('minutes', [0, -1])
    def test_rejects_nonpositive_auto_stop(self, minutes: int) -> None:
        with pytest.raises(ValueError, match='auto_stop_minutes'):
            DaytonaSandbox(auto_stop_minutes=minutes)

    def test_rejects_relative_workdir(self) -> None:
        with pytest.raises(ValueError, match='absolute'):
            DaytonaSandbox(workdir='repo')

    @pytest.mark.parametrize(
        'settings',
        [
            {'snapshot': 'base'},
            {'auto_stop_minutes': 30},
            {'env': {'A': 'b'}},
            {'network_block_all': True},
        ],
    )
    def test_attach_rejects_creation_settings(self, settings: dict[str, object]) -> None:
        with pytest.raises(ValueError, match='only apply when creating'):
            DaytonaSandbox(sandbox_id='existing', **settings)  # type: ignore[arg-type]

    def test_copies_environment_mapping(self) -> None:
        source = {'A': 'one'}
        capability = DaytonaSandbox(env=source)
        source['A'] = 'two'
        assert capability.env == {'A': 'one'}

    def test_public_surface(self) -> None:
        assert not hasattr(pydantic_ai_harness, 'DaytonaSandbox')
        assert set(daytona_sandbox.__all__) == {
            'DaytonaSandbox',
            'DaytonaSandboxAuthError',
            'DaytonaSandboxBackend',
            'DaytonaSandboxError',
            'DaytonaSandboxUnavailableError',
        }
        assert DaytonaSandbox.get_serialization_name() == 'DaytonaSandbox'
