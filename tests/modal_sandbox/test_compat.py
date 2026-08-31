"""Compatibility tests for the released direct Modal session API."""

from __future__ import annotations

import pytest

from pydantic_ai_harness.modal_sandbox import ModalSandboxExecResult, ModalSandboxSession

from .fake_modal import FakeModal, FileInfo


async def test_owned_session_executes_and_terminates(fake_modal: FakeModal) -> None:
    async with ModalSandboxSession(workdir='/work') as session:
        assert session.sandbox_id == 'sb-owned'
        assert await session.exec(['echo', 'hello']) == ModalSandboxExecResult(
            stdout='echo hello\n', stderr='', returncode=0
        )

    assert fake_modal.sandboxes[0].terminated is True
    assert fake_modal.sandboxes[0].detached is True


async def test_attached_session_detaches_without_terminating(fake_modal: FakeModal) -> None:
    async with ModalSandboxSession(sandbox_id='sb-existing'):
        pass

    assert fake_modal.sandboxes[0].terminated is False
    assert fake_modal.sandboxes[0].detached is True


@pytest.mark.parametrize('operation', ['terminate', 'detach'])
async def test_teardown_failure_does_not_escape(fake_modal: FakeModal, operation: str) -> None:
    async with ModalSandboxSession():
        setattr(fake_modal.sandboxes[0], f'{operation}_error', RuntimeError('cleanup failed'))


async def test_exec_preserves_timeout_result_and_output_limit(fake_modal: FakeModal) -> None:
    fake_modal.responder = lambda argv, timeout: (b'abcdef', b'uvwxyz', -1)

    async with ModalSandboxSession() as session:
        result = await session.exec(['slow'], timeout=0.5, max_output_bytes=3)

    assert result == ModalSandboxExecResult(
        stdout='def',
        stderr='xyz',
        returncode=-1,
        stdout_truncated=True,
        stderr_truncated=True,
        timed_out=True,
        applied_timeout=1,
    )


async def test_file_methods_use_session_working_directory(fake_modal: FakeModal) -> None:
    async with ModalSandboxSession(workdir='/work') as session:
        await session.write_bytes('/work/dir/data.bin', b'data')
        fake_modal.sandboxes[0].listing = [FileInfo('data.bin', False)]

        assert await session.file_size('/work/dir/data.bin') == 4
        assert await session.read_bytes('/work/dir/data.bin') == b'data'
        assert await session.list_files('/work/dir') == [('data.bin', False)]
