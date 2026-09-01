"""Tests for running filesystem tools through the core sandbox facade."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator, Mapping, Sequence
from pathlib import Path

import pytest
from pydantic_ai import RunContext
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.models.test import TestModel
from pydantic_ai.sandboxes import (
    CommandResult,
    LocalSandbox,
    Sandbox,
    SandboxCommand,
    SandboxFileEntry,
    SandboxResult,
    SandboxUnavailableError,
)
from pydantic_ai.usage import RunUsage

from pydantic_ai_harness.filesystem import FileSystemToolset

pytestmark = pytest.mark.anyio


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()[:12]


@pytest.fixture
def anyio_backend() -> str:
    return 'asyncio'


@pytest.fixture
async def sandbox(tmp_path: Path) -> AsyncIterator[Sandbox]:
    async with LocalSandbox(root=tmp_path) as backend:
        yield Sandbox.wrap(backend)


def _ctx(sandbox: Sandbox | None = None) -> RunContext[None]:
    if sandbox is None:
        return RunContext[None](deps=None, model=TestModel(), usage=RunUsage(), prompt=None, messages=[], run_step=0)
    return RunContext[None](
        deps=None,
        model=TestModel(),
        usage=RunUsage(),
        prompt=None,
        messages=[],
        run_step=0,
        sandbox=sandbox,
    )


def _toolset(
    root: Path,
    *,
    allowed_patterns: Sequence[str] = (),
    denied_patterns: Sequence[str] = (),
    protected_patterns: Sequence[str] = (),
    max_list_results: int = 100,
    max_search_results: int = 100,
    max_find_results: int = 100,
) -> FileSystemToolset[None]:
    return FileSystemToolset(
        root_dir=root,
        allowed_patterns=allowed_patterns,
        denied_patterns=denied_patterns,
        protected_patterns=protected_patterns,
        max_read_lines=100,
        max_list_results=max_list_results,
        max_search_results=max_search_results,
        max_find_results=max_find_results,
    )


async def _call(
    toolset: FileSystemToolset[None],
    ctx: RunContext[None],
    name: str,
    tool_args: dict[str, object],
) -> str:
    tools = await toolset.get_tools(ctx)
    result: object = await toolset.call_tool(name, tool_args, ctx, tools[name])
    assert isinstance(result, str)
    return result


class _FailingFilesystem:
    """Only the operations the tools reach before failing; `stat` guards most paths."""

    def __init__(self, error: RuntimeError) -> None:
        self.error = error

    async def read_bytes(self, path: str) -> bytes:
        raise self.error

    async def stat(self, path: str) -> SandboxFileEntry:
        raise self.error

    async def make_dir(self, path: str) -> None:
        raise self.error


class _FailingBackend:
    provider = 'failing'
    sandbox_id = 'failing-1'

    def __init__(self, error: RuntimeError) -> None:
        self.error = error
        self.fs = _FailingFilesystem(error)

    async def working_dir(self) -> str:
        return '/work'

    async def run(
        self,
        command: SandboxCommand,
        *,
        shell: bool = False,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> SandboxResult:
        raise self.error


class _ResultBackend:
    provider = 'result'
    sandbox_id = 'result-1'

    def __init__(self, backend: LocalSandbox, result: CommandResult) -> None:
        self.backend = backend
        self.fs = backend.fs
        self.result = result

    async def working_dir(self) -> str:
        return await self.backend.working_dir()

    async def run(
        self,
        command: SandboxCommand,
        *,
        shell: bool = False,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> SandboxResult:
        return self.result


class TestSandboxFileOperations:
    async def test_read_paging_is_zero_based_and_reports_more(self, tmp_path: Path, sandbox: Sandbox) -> None:
        (tmp_path / 'lines.txt').write_text('one\ntwo\nthree\nfour\n')
        toolset = _toolset(tmp_path / 'host-only')

        first = await _call(toolset, _ctx(sandbox), 'read_file', {'path': 'lines.txt', 'offset': 0, 'limit': 2})
        second = await _call(toolset, _ctx(sandbox), 'read_file', {'path': 'lines.txt', 'offset': 2, 'limit': 2})

        assert '     1\tone\n' in first
        assert '     2\ttwo\n' in first
        assert 'Use offset=2' in first
        assert '     3\tthree\n' in second
        assert '     4\tfour\n' in second
        assert 'Use offset=' not in second
        # A partial window's header omits the hash: it would never match the full-file
        # hash that write_file and edit_file verify against.
        assert first.startswith('[lines.txt | lines 1-2]\n')
        assert 'hash:' not in first

        full = await _call(toolset, _ctx(sandbox), 'read_file', {'path': 'lines.txt'})
        assert full.startswith('[lines.txt | 4 lines | hash:')

    async def test_zero_based_offset_matches_host_mode(self, tmp_path: Path, sandbox: Sandbox) -> None:
        (tmp_path / 'same.txt').write_text('zero\none\ntwo\n')
        sandbox_result = await _call(
            _toolset(tmp_path / 'ignored'), _ctx(sandbox), 'read_file', {'path': 'same.txt', 'offset': 1, 'limit': 1}
        )
        host_result = await _call(
            _toolset(tmp_path), _ctx(), 'read_file', {'path': 'same.txt', 'offset': 1, 'limit': 1}
        )
        assert '     2\tone\n' in sandbox_result
        assert '     2\tone\n' in host_result

        # The count is omitted: reporting it would require reading the whole file,
        # defeating the bounded read. An empty file behaves the same as host mode.
        for name in ('same.txt', 'empty.txt'):
            (tmp_path / 'empty.txt').write_text('')
            with pytest.raises(ModelRetry, match='Offset 4 exceeds file length'):
                await _call(
                    _toolset(tmp_path / 'ignored'),
                    _ctx(sandbox),
                    'read_file',
                    {'path': name, 'offset': 4, 'limit': 1},
                )

    async def test_binary_read_uses_stat_size(self, tmp_path: Path, sandbox: Sandbox) -> None:
        (tmp_path / 'binary.bin').write_bytes(b'hello\x00world')
        result = await _call(_toolset(tmp_path), _ctx(sandbox), 'read_file', {'path': 'binary.bin'})
        assert result == '[Binary file: 11 bytes. Use a binary-aware tool to inspect.]'

    @pytest.mark.parametrize(
        ('path', 'message'),
        [('missing.txt', 'File not found'), ('directory', 'is a directory')],
    )
    async def test_read_errors_match_host_messages(
        self, tmp_path: Path, sandbox: Sandbox, path: str, message: str
    ) -> None:
        (tmp_path / 'directory').mkdir()
        with pytest.raises(ModelRetry, match=message):
            await _call(_toolset(tmp_path), _ctx(sandbox), 'read_file', {'path': path})

    async def test_write_edit_and_expected_hash_conflicts(self, tmp_path: Path, sandbox: Sandbox) -> None:
        toolset = _toolset(tmp_path / 'host-only')
        ctx = _ctx(sandbox)
        before = 'before\n'
        after = 'after\n'
        result = await _call(toolset, ctx, 'write_file', {'path': 'note.txt', 'content': before})
        assert result.endswith(f'[hash:{_hash(before)}]')

        with pytest.raises(ModelRetry, match='Conflict'):
            await _call(
                toolset,
                ctx,
                'write_file',
                {'path': 'note.txt', 'content': 'bad\n', 'expected_hash': 'stale'},
            )

        result = await _call(
            toolset,
            ctx,
            'write_file',
            {'path': 'note.txt', 'content': before, 'expected_hash': _hash(before)},
        )
        assert result.startswith('Wrote')

        result = await _call(
            toolset,
            ctx,
            'edit_file',
            {
                'path': 'note.txt',
                'old_text': 'before',
                'new_text': 'after',
                'expected_hash': _hash(before),
            },
        )
        assert result.endswith(f'[hash:{_hash(after)}]')
        assert (tmp_path / 'note.txt').read_text() == after

        with pytest.raises(ModelRetry, match='Conflict'):
            await _call(
                toolset,
                ctx,
                'edit_file',
                {'path': 'note.txt', 'old_text': 'after', 'new_text': 'bad', 'expected_hash': 'stale'},
            )

    @pytest.mark.parametrize(
        ('path', 'message'),
        [
            ('directory', 'not a regular file'),
            ('missing/child.txt', 'Parent directory'),
            ('parent.txt/child.txt', 'Parent directory'),
        ],
    )
    async def test_write_target_and_parent_errors(
        self, tmp_path: Path, sandbox: Sandbox, path: str, message: str
    ) -> None:
        (tmp_path / 'directory').mkdir()
        (tmp_path / 'parent.txt').write_text('file')
        with pytest.raises(ModelRetry, match=message):
            await _call(_toolset(tmp_path), _ctx(sandbox), 'write_file', {'path': path, 'content': 'content'})

    @pytest.mark.parametrize(
        ('path', 'old_text', 'message'),
        [
            ('missing.txt', 'x', 'File not found'),
            ('once.txt', 'missing', 'old_text not found'),
            ('twice.txt', 'same', 'found 2 times'),
        ],
    )
    async def test_edit_errors(
        self,
        tmp_path: Path,
        sandbox: Sandbox,
        path: str,
        old_text: str,
        message: str,
    ) -> None:
        (tmp_path / 'once.txt').write_text('once')
        (tmp_path / 'twice.txt').write_text('same same')
        with pytest.raises(ModelRetry, match=message):
            await _call(
                _toolset(tmp_path),
                _ctx(sandbox),
                'edit_file',
                {'path': path, 'old_text': old_text, 'new_text': 'new'},
            )

    async def test_list_filters_patterns_and_caps_results(self, tmp_path: Path, sandbox: Sandbox) -> None:
        (tmp_path / 'a.py').write_text('a')
        (tmp_path / 'b.py').write_text('b')
        (tmp_path / 'notes.txt').write_text('notes')
        (tmp_path / '.hidden.py').write_text('hidden')
        toolset = _toolset(tmp_path, allowed_patterns=('*.py',), denied_patterns=('b.py',), max_list_results=1)

        result = await _call(toolset, _ctx(sandbox), 'list_directory', {'path': '.'})
        assert result == 'a.py  (1 bytes)'

    async def test_list_directory_errors_and_truncation(self, tmp_path: Path, sandbox: Sandbox) -> None:
        (tmp_path / 'file.txt').write_text('file')
        (tmp_path / 'one.txt').write_text('one')
        (tmp_path / 'two.txt').write_text('two')
        ctx = _ctx(sandbox)

        for path in ('missing', 'file.txt'):
            with pytest.raises(ModelRetry, match='Not a directory'):
                await _call(_toolset(tmp_path), ctx, 'list_directory', {'path': path})

        result = await _call(_toolset(tmp_path, max_list_results=1), ctx, 'list_directory', {'path': '.'})
        assert result.splitlines()[-1] == '[... truncated at 1 entries]'

    async def test_create_directory_and_file_info(self, tmp_path: Path, sandbox: Sandbox) -> None:
        toolset = _toolset(tmp_path / 'host-only')
        ctx = _ctx(sandbox)
        assert await _call(toolset, ctx, 'create_directory', {'path': 'deep/nested'}) == (
            'Created directory: deep/nested'
        )
        assert (tmp_path / 'deep' / 'nested').is_dir()

        (tmp_path / 'info.txt').write_text('first\nsecond\n')
        info_content = 'first\nsecond\n'
        result = await _call(toolset, ctx, 'file_info', {'path': 'info.txt'})
        assert result == (
            f'path: info.txt\ntype: file\nsize: 13 bytes\nbinary: False\nlines: 2\nhash: {_hash(info_content)}'
        )

    async def test_search_match_no_match_glob_and_denied_filter(self, tmp_path: Path, sandbox: Sandbox) -> None:
        (tmp_path / 'main.py').write_text('needle here\n')
        (tmp_path / 'notes.md').write_text('needle docs\n')
        (tmp_path / 'secret.py').write_text('needle secret\n')
        toolset = _toolset(tmp_path, denied_patterns=('secret.py',))
        ctx = _ctx(sandbox)

        result = await _call(toolset, ctx, 'search_files', {'pattern': 'needle'})
        assert 'main.py:1:needle here' in result
        assert 'notes.md:1:needle docs' in result
        assert 'secret.py' not in result

        result = await _call(
            toolset,
            ctx,
            'search_files',
            {'pattern': 'needle', 'include_glob': '*.py'},
        )
        assert result == 'main.py:1:needle here'
        assert await _call(toolset, ctx, 'search_files', {'pattern': 'absent'}) == 'No matches found.'

    async def test_search_truncates_results(self, tmp_path: Path, sandbox: Sandbox) -> None:
        (tmp_path / 'many.txt').write_text('hit\nhit\n')
        result = await _call(
            _toolset(tmp_path, max_search_results=1), _ctx(sandbox), 'search_files', {'pattern': 'hit'}
        )
        assert result.splitlines()[-1] == '[... truncated at 1 matches]'

    async def test_find_files_filters_and_marks_directories(self, tmp_path: Path, sandbox: Sandbox) -> None:
        (tmp_path / 'src').mkdir()
        (tmp_path / 'src' / 'main.py').write_text('')
        (tmp_path / 'secret.py').write_text('')
        (tmp_path / '.hidden.py').write_text('')
        toolset = _toolset(tmp_path, denied_patterns=('secret.py',))

        # `*.py` stays in one directory (host glob semantics): the top-level candidates are
        # denied or hidden, and the nested file needs the recursive form.
        assert await _call(toolset, _ctx(sandbox), 'find_files', {'pattern': '*.py'}) == 'No matches found.'
        assert await _call(toolset, _ctx(sandbox), 'find_files', {'pattern': '**/*.py'}) == 'src/main.py'
        assert await _call(toolset, _ctx(sandbox), 'find_files', {'pattern': 'src/*.py'}) == 'src/main.py'
        assert await _call(toolset, _ctx(sandbox), 'find_files', {'pattern': 'src'}) == 'src/'

    async def test_find_errors_and_truncation(self, tmp_path: Path, sandbox: Sandbox) -> None:
        (tmp_path / 'file.txt').write_text('file')
        (tmp_path / 'one.py').write_text('')
        (tmp_path / 'two.py').write_text('')
        ctx = _ctx(sandbox)

        with pytest.raises(ModelRetry, match='must be relative'):
            await _call(_toolset(tmp_path), ctx, 'find_files', {'pattern': '/absolute'})
        for path in ('missing', 'file.txt'):
            with pytest.raises(ModelRetry, match='Not a directory'):
                await _call(_toolset(tmp_path), ctx, 'find_files', {'pattern': '*', 'path': path})

        result = await _call(_toolset(tmp_path, max_find_results=1), ctx, 'find_files', {'pattern': '*.py'})
        assert result.splitlines()[-1] == '[... truncated at 1 matches]'

    async def test_find_skips_entries_deleted_mid_walk(self, tmp_path: Path) -> None:
        (tmp_path / 'real.py').write_text('')
        async with LocalSandbox(root=tmp_path) as local:
            listing = CommandResult(exit_code=0, stdout=f'{tmp_path}/ghost.py\n{tmp_path}/real.py\n', stderr='')
            sandbox = Sandbox.wrap(_ResultBackend(local, listing))
            result = await _call(_toolset(tmp_path), _ctx(sandbox), 'find_files', {'pattern': '*.py'})
        assert result == 'real.py'

    async def test_file_info_missing_directory_and_binary(self, tmp_path: Path, sandbox: Sandbox) -> None:
        (tmp_path / 'directory').mkdir()
        (tmp_path / 'binary.bin').write_bytes(b'\x00')
        ctx = _ctx(sandbox)

        with pytest.raises(ModelRetry, match='Path not found'):
            await _call(_toolset(tmp_path), ctx, 'file_info', {'path': 'missing'})
        assert await _call(_toolset(tmp_path), ctx, 'file_info', {'path': 'directory'}) == (
            'path: directory\ntype: directory\nsize: 0 bytes'
        )
        assert await _call(_toolset(tmp_path), ctx, 'file_info', {'path': 'binary.bin'}) == (
            'path: binary.bin\ntype: file\nsize: 1 bytes\nbinary: True'
        )


class TestSandboxPolicyAndErrors:
    async def test_dotdot_escape_is_rejected(self, tmp_path: Path, sandbox: Sandbox) -> None:
        with pytest.raises(ModelRetry, match='resolves outside the root directory'):
            await _call(_toolset(tmp_path), _ctx(sandbox), 'read_file', {'path': '../outside.txt'})

    async def test_search_ignores_malformed_command_output(self, tmp_path: Path) -> None:
        async with LocalSandbox(root=tmp_path) as local:
            sandbox = Sandbox.wrap(_ResultBackend(local, CommandResult(exit_code=1, stdout='malformed\n', stderr='')))
            result = await _call(_toolset(tmp_path), _ctx(sandbox), 'search_files', {'pattern': 'anything'})
        assert result == 'No matches found.'

    @pytest.mark.parametrize(
        ('name', 'args', 'exit_code', 'stderr', 'message'),
        [
            ('search_files', {'pattern': 'x'}, 2, 'grep failed', 'grep failed'),
            ('search_files', {'pattern': 'x'}, 2, '', 'grep exited with code 2'),
            ('find_files', {'pattern': '*'}, 1, 'find failed', 'find failed'),
            ('find_files', {'pattern': '*'}, 1, '', 'find exited with code 1'),
        ],
    )
    async def test_command_failure_mapping(
        self,
        tmp_path: Path,
        name: str,
        args: dict[str, object],
        exit_code: int,
        stderr: str,
        message: str,
    ) -> None:
        async with LocalSandbox(root=tmp_path) as local:
            result = CommandResult(exit_code=exit_code, stdout='', stderr=stderr)
            sandbox = Sandbox.wrap(_ResultBackend(local, result))
            with pytest.raises(ModelRetry, match=message):
                await _call(_toolset(tmp_path), _ctx(sandbox), name, args)

    @pytest.mark.parametrize(
        ('name', 'args', 'message'),
        [
            ('read_file', {'path': 'denied.txt'}, 'denied by pattern'),
            ('read_file', {'path': 'notes.md'}, 'does not match any allowed pattern'),
            ('write_file', {'path': 'locked.py', 'content': 'x'}, 'protected'),
        ],
    )
    async def test_pattern_enforcement_matches_host_mode(
        self,
        tmp_path: Path,
        sandbox: Sandbox,
        name: str,
        args: dict[str, object],
        message: str,
    ) -> None:
        (tmp_path / 'denied.txt').write_text('denied')
        (tmp_path / 'notes.md').write_text('notes')
        (tmp_path / 'locked.py').write_text('locked')
        with pytest.raises(ModelRetry, match=message) as sandbox_error:
            await _call(
                _toolset(
                    tmp_path / 'ignored',
                    allowed_patterns=('*.py', 'denied.txt'),
                    denied_patterns=('denied.txt',),
                    protected_patterns=('locked.py',),
                ),
                _ctx(sandbox),
                name,
                args,
            )
        with pytest.raises(ModelRetry, match=message) as host_error:
            await _call(
                _toolset(
                    tmp_path,
                    allowed_patterns=('*.py', 'denied.txt'),
                    denied_patterns=('denied.txt',),
                    protected_patterns=('locked.py',),
                ),
                _ctx(),
                name,
                args,
            )
        assert str(sandbox_error.value) == str(host_error.value)

    @pytest.mark.parametrize(
        ('error', 'expected'),
        [
            (SandboxUnavailableError('gone'), SandboxUnavailableError),
            (RuntimeError('temporary failure'), ModelRetry),
        ],
    )
    @pytest.mark.parametrize(
        ('name', 'args'),
        [
            ('read_file', {'path': 'file.txt'}),
            ('write_file', {'path': 'file.txt', 'content': 'x'}),
            ('edit_file', {'path': 'file.txt', 'old_text': 'a', 'new_text': 'b'}),
            ('list_directory', {'path': '.'}),
            ('search_files', {'pattern': 'x'}),
            ('find_files', {'pattern': '*.py'}),
            ('create_directory', {'path': 'sub'}),
            ('file_info', {'path': 'file.txt'}),
        ],
    )
    async def test_backend_error_mapping(
        self,
        tmp_path: Path,
        error: RuntimeError,
        expected: type[RuntimeError],
        name: str,
        args: dict[str, object],
    ) -> None:
        with pytest.raises(expected, match=str(error)):
            await _call(
                _toolset(tmp_path),
                _ctx(Sandbox.wrap(_FailingBackend(error))),
                name,
                args,
            )

    async def test_host_mode_smoke(self, tmp_path: Path) -> None:
        (tmp_path / 'host.txt').write_text('host only\n')
        result = await _call(_toolset(tmp_path), _ctx(), 'read_file', {'path': 'host.txt'})
        assert 'host only' in result
