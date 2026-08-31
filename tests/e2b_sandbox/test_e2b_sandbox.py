"""Tests for the public E2B sandbox capability."""

from __future__ import annotations

import inspect

import pytest
from pydantic_ai.models.test import TestModel
from pydantic_ai.sandboxes import SandboxRef
from pydantic_ai.tools import RunContext
from pydantic_ai.usage import RunUsage

import pydantic_ai_harness
import pydantic_ai_harness.e2b_sandbox as e2b_sandbox
from pydantic_ai_harness.e2b_sandbox import E2BSandbox, E2BSandboxBackend

from .fake_e2b import FakeE2B


def _ctx(run_id: str = 'run-1') -> RunContext[None]:
    return RunContext(deps=None, model=TestModel(), usage=RunUsage(), run_id=run_id)


class TestLifecycle:
    async def test_acquire_is_idempotent_for_one_logical_run(self, fake_e2b: FakeE2B) -> None:
        capability = E2BSandbox[None]()

        first = await capability.acquire_sandbox(_ctx())
        second = await capability.acquire_sandbox(_ctx())

        assert first == second
        assert len(fake_e2b.create_calls) == 1
        assert fake_e2b.create_calls[0].metadata == {'pydantic-ai-run-id': 'run-1'}

    async def test_user_metadata_is_preserved(self, fake_e2b: FakeE2B) -> None:
        await E2BSandbox[None](metadata={'owner': 'tests'}).acquire_sandbox(_ctx())

        assert fake_e2b.create_calls[0].metadata == {
            'owner': 'tests',
            'pydantic-ai-run-id': 'run-1',
        }

    async def test_different_runs_create_different_sandboxes(self, fake_e2b: FakeE2B) -> None:
        capability = E2BSandbox[None]()

        first = await capability.acquire_sandbox(_ctx('run-1'))
        second = await capability.acquire_sandbox(_ctx('run-2'))

        assert first != second
        assert len(fake_e2b.create_calls) == 2

    async def test_acquire_chooses_oldest_matching_sandbox(self, fake_e2b: FakeE2B) -> None:
        metadata = {'pydantic-ai-run-id': 'run-1'}
        fake_e2b.new_sandbox('oldest', metadata)
        fake_e2b.new_sandbox('newest', metadata)

        ref = await E2BSandbox[None]().acquire_sandbox(_ctx())

        assert ref.sandbox_id == 'oldest'
        assert fake_e2b.list_calls == [(metadata, 1, 'asc')]

    async def test_get_sandbox_reconnects_by_ref(self, fake_e2b: FakeE2B) -> None:
        backend = await E2BSandbox[None]().get_sandbox(_ctx(), SandboxRef(provider='e2b', sandbox_id='sbx-existing'))

        assert isinstance(backend, E2BSandboxBackend)
        assert fake_e2b.connect_calls == [('sbx-existing', None)]

    async def test_get_sandbox_declines_other_providers(self, fake_e2b: FakeE2B) -> None:
        backend = await E2BSandbox[None]().get_sandbox(_ctx(), SandboxRef(provider='modal', sandbox_id='x'))

        assert backend is None
        assert fake_e2b.sandboxes == []

    async def test_release_reconnects_and_kills(self, fake_e2b: FakeE2B) -> None:
        capability = E2BSandbox[None]()
        ref = await capability.acquire_sandbox(_ctx())

        await capability.release_sandbox(_ctx(), ref)

        assert fake_e2b.connect_calls[-1] == (ref.sandbox_id, None)
        assert fake_e2b.sandboxes[-1].killed is True

    async def test_release_is_idempotent_when_sandbox_is_gone(self, fake_e2b: FakeE2B) -> None:
        fake_e2b.connect_error = fake_e2b.sandbox_gone_type('gone')

        await E2BSandbox[None]().release_sandbox(_ctx(), SandboxRef(provider='e2b', sandbox_id='gone'))

    async def test_attached_sandbox_is_not_owned(self, fake_e2b: FakeE2B) -> None:
        capability = E2BSandbox[None](sandbox_id='sbx-existing')

        ref = await capability.acquire_sandbox(_ctx())
        await capability.release_sandbox(_ctx(), ref)

        assert ref == SandboxRef(provider='e2b', sandbox_id='sbx-existing')
        assert fake_e2b.sandboxes == []


class TestConfiguration:
    def test_defaults(self) -> None:
        capability = E2BSandbox()

        assert capability.template is None
        assert capability.sandbox_timeout == 300
        assert capability.allow_internet_access is True

    def test_configuration_is_keyword_only(self) -> None:
        assert all(
            parameter.kind is inspect.Parameter.KEYWORD_ONLY
            for parameter in inspect.signature(E2BSandbox).parameters.values()
        )

    @pytest.mark.parametrize('timeout', [0, -1, 1.5, True])
    def test_rejects_invalid_sandbox_timeout(self, timeout: object) -> None:
        with pytest.raises(ValueError, match='sandbox_timeout'):
            E2BSandbox(sandbox_timeout=timeout)  # type: ignore[arg-type]

    def test_rejects_relative_workdir(self) -> None:
        with pytest.raises(ValueError, match='absolute'):
            E2BSandbox(workdir='repo')

    def test_reserves_retry_identity_metadata(self) -> None:
        with pytest.raises(ValueError, match='reserved'):
            E2BSandbox(metadata={'pydantic-ai-run-id': 'mine'})

    @pytest.mark.parametrize(
        'settings',
        [
            {'template': 'base'},
            {'sandbox_timeout': 600},
            {'env': {'A': 'b'}},
            {'metadata': {'owner': 'tests'}},
            {'allow_internet_access': False},
        ],
    )
    def test_attach_rejects_creation_settings(self, settings: dict[str, object]) -> None:
        with pytest.raises(ValueError, match='only apply when creating'):
            E2BSandbox(sandbox_id='sbx-existing', **settings)  # type: ignore[arg-type]

    def test_copies_input_mappings(self) -> None:
        env = {'A': 'one'}
        metadata = {'owner': 'one'}
        capability = E2BSandbox(env=env, metadata=metadata)
        env['A'] = 'two'
        metadata['owner'] = 'two'

        assert capability.env == {'A': 'one'}
        assert capability.metadata == {'owner': 'one'}

    def test_public_exports_are_narrow(self) -> None:
        assert pydantic_ai_harness.E2BSandbox is E2BSandbox
        assert set(e2b_sandbox.__all__) == {
            'E2BSandbox',
            'E2BSandboxAuthError',
            'E2BSandboxBackend',
            'E2BSandboxError',
            'E2BSandboxUnavailableError',
        }

    def test_serialization_name(self) -> None:
        assert E2BSandbox.get_serialization_name() == 'E2BSandbox'
