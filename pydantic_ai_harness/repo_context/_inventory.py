"""Locate (not parse) a repo's coding-assistant CE assets."""

from __future__ import annotations

import posixpath
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_ai.sandboxes import Sandbox

_ROOT_NOTES = {
    '.codex': 'Codex uses TOML config; assets are derived from the .claude/.agents setup.',
    '.grok': 'Grok setup is derived from the .claude/.agents setup.',
}


class AssetRoot(BaseModel):
    """Where CE assets live under a single root directory (e.g. `.claude`)."""

    root: str = Field(description='The root directory name, relative to the workspace, e.g. ".claude".')
    exists: bool = Field(description='Whether the root directory is present in the workspace.')
    skills: list[str] = Field(default_factory=list, description='Paths to SKILL.md files found under skills/.')
    agents: list[str] = Field(default_factory=list, description='Paths to agent .md files found under agents/.')
    settings: str | None = Field(default=None, description='Path to settings.json (hooks), if present.')
    notes: str | None = Field(default=None, description='Format or derivation notes for this root, if any.')


class AgentContextInventory(BaseModel):
    """A map of where a repo's CE assets live, for an orchestrator to read or translate."""

    roots: list[AssetRoot] = Field(default_factory=list[AssetRoot], description='One entry per scanned root directory.')


async def scan_assets(sandbox: Sandbox, workspace_dir: Path, asset_roots: Sequence[str]) -> AgentContextInventory:
    """Scan `asset_roots` under `workspace_dir`, locating skills, agents, and hooks.

    This locates assets only; it does not open or parse SKILL.md, agent `.md`, or
    `settings.json` contents.
    """
    workspace = await sandbox.resolve(workspace_dir.as_posix())
    roots: list[AssetRoot] = []
    for name in asset_roots:
        directory = posixpath.normpath(posixpath.join(workspace, name))
        notes = _ROOT_NOTES.get(name)
        try:
            entry = await sandbox.fs.stat(directory)
        except FileNotFoundError:
            roots.append(AssetRoot(root=name, exists=False, notes=notes))
            continue
        if not entry.is_dir:
            roots.append(AssetRoot(root=name, exists=False, notes=notes))
            continue

        result = await sandbox.run(
            [
                'find',
                '-L',
                directory,
                '-type',
                'f',
                '(',
                '-name',
                'SKILL.md',
                '-o',
                '-name',
                '*.md',
                '-o',
                '-name',
                'settings.json',
                ')',
            ]
        )
        if result.exit_code != 0:
            raise RuntimeError(f'Could not inventory {directory}: {result.stderr.strip()}')
        skills: list[str] = []
        agents: list[str] = []
        settings: str | None = None
        prefix = f'{workspace.rstrip("/")}/'
        for absolute in result.stdout.splitlines():
            relative = absolute.removeprefix(prefix)
            parts = relative.split('/')
            if len(parts) >= 4 and parts[1] == 'skills' and parts[-1] == 'SKILL.md':
                skills.append(relative)
            elif len(parts) == 3 and parts[1] == 'agents' and parts[-1].endswith('.md'):
                agents.append(relative)
            elif len(parts) == 2 and parts[1] == 'settings.json':
                settings = relative
        skills.sort()
        agents.sort()
        roots.append(AssetRoot(root=name, exists=True, skills=skills, agents=agents, settings=settings, notes=notes))
    return AgentContextInventory(roots=roots)
