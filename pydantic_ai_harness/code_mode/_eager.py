"""Eager execution for `CodeMode`: feed streamed `run_code` statements into the live REPL.

Eager execution runs the real program, in order, in the real session, as each top-level
statement closes in the stream. Nothing is predicted, so nothing can miss or be wasted --
and nothing can be rolled back: side effects land before the tool call is committed, which
is why the tier is opt-in.

A failed statement leaves the session exactly as a failed whole-snippet feed does today:
assignments made before the failing line persist and the error surfaces to the model as the
`run_code` result. The only semantic difference from non-eager execution is timing.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from pydantic_ai import RunContext
from pydantic_ai.messages import (
    AgentStreamEvent,
    PartDeltaEvent,
    PartStartEvent,
    ToolCallPart,
    ToolCallPartDelta,
    ToolReturnPart,
)

from ._streaming import closed_statements, decode_partial_code

if TYPE_CHECKING:
    from ._toolset import CodeModeToolset


@dataclass(kw_only=True)
class _EagerPart:
    """Accumulated state for one streamed `run_code` tool call part under eager execution."""

    tool_call_id: str
    args_text: str = ''
    args_dict: dict[str, Any] | None = None
    consumed: int = 0
    """Top-level statements handed to the feed pump so far."""

    scanned_newlines: int = -1
    """Newline count at the last scan; statements only close on line boundaries.

    Starts at -1 so the first scan runs even for zero-newline code: 0 completed lines is a
    real observation, distinct from never having scanned.
    """

    fed_line_count: int = 0
    """Source lines covered by pumped statements; the executed prefix ends here."""

    fed_prefix: str = ''
    """Exact source prefix handed to the pump, for divergence detection at execution."""

    queue: deque[str] = field(default_factory=deque[str])
    """Statement sources waiting for the pump, in program order."""

    pump: asyncio.Task[None] | None = None
    error: BaseException | None = None
    feed_count: int = 0
    output: str = ''
    """Concatenated print output from completed fragment feeds."""

    nested_calls: dict[str, ToolCallPart] = field(default_factory=dict[str, ToolCallPart])
    """Tool calls the pumped statements dispatched, keyed by nested tool call id."""

    nested_returns: dict[str, ToolReturnPart] = field(default_factory=dict[str, ToolReturnPart])
    """Their return parts, under the same nested tool call ids."""


@dataclass
class EagerState:
    """Watch streamed `run_code` parts and feed closed statements into the REPL serially.

    One instance per run, owned by the `CodeMode` clone and bound to its `CodeModeToolset`.
    The stream watcher enqueues statement sources; a single pump task per part feeds them
    through the toolset's normal `run_code` pipeline, so budget accounting, error mapping,
    and tool hooks behave exactly as a model-issued snippet would.
    """

    _parts: dict[str, _EagerPart] = field(default_factory=dict[str, '_EagerPart'], init=False)
    _toolset: CodeModeToolset[Any] | None = field(default=None, init=False)

    def bind(self, toolset: CodeModeToolset[Any]) -> None:
        """Attach the owning toolset; feeds go through its `run_code` pipeline."""
        self._toolset = toolset

    # -- stream side ------------------------------------------------------------------------

    async def observe(self, event: AgentStreamEvent, ctx: RunContext[Any]) -> None:
        """Track one stream event, enqueueing newly closed statements for the pump."""
        match event:
            case PartStartEvent(part=ToolCallPart() as part):
                if part.tool_name != 'run_code':
                    return
                part_id = part.tool_call_id
                watch = self._parts.setdefault(part_id, _EagerPart(tool_call_id=part_id))
                args = part.args
                if isinstance(args, str):
                    watch.args_text += args
                elif isinstance(args, dict):
                    watch.args_dict = args
                self._scan(watch, ctx)
            case PartDeltaEvent(delta=ToolCallPartDelta() as delta):
                watch = self._find_watch(delta.tool_call_id)
                if watch is None:
                    return
                args_delta = delta.args_delta
                if isinstance(args_delta, str):
                    watch.args_text += args_delta
                elif isinstance(args_delta, dict):
                    merged = dict(watch.args_dict or {})
                    merged.update(args_delta)
                    watch.args_dict = merged
                self._scan(watch, ctx)
            case _:
                return

    def _find_watch(self, tool_call_id: str | None) -> _EagerPart | None:
        if tool_call_id is not None:
            return self._parts.get(tool_call_id)
        if len(self._parts) == 1:  # pragma: no cover - delta without id, single-part fallback
            return next(iter(self._parts.values()))
        return None

    def _current_code(self, watch: _EagerPart) -> str | None:
        if watch.args_dict is not None:
            code = watch.args_dict.get('code')
            return code if isinstance(code, str) else None
        return decode_partial_code(watch.args_text)

    def _scan(self, watch: _EagerPart, ctx: RunContext[Any]) -> None:
        """Re-parse the streamed code and enqueue any statements that newly closed."""
        code = self._current_code(watch)
        if code is None:
            return
        newlines = code.count('\n')
        if newlines == watch.scanned_newlines:
            # Statements only close on completed lines; skip re-parsing mid-line deltas.
            return
        watch.scanned_newlines = newlines
        fresh, watch.consumed = closed_statements(code, watch.consumed)
        # Each queued source is a contiguous line slice from the end of the previous
        # statement, so comments and blank lines between statements feed with them and the
        # concatenation of all slices reproduces the prefix exactly.
        lines = code.split('\n')
        for statement in fresh:
            end = statement.end_lineno or statement.lineno
            source = '\n'.join(lines[watch.fed_line_count : end])
            watch.queue.append(source)
            watch.fed_line_count = end
        watch.fed_prefix = '\n'.join(lines[: watch.fed_line_count])
        # One pump per part: enqueueing happens before this check, so a finished pump
        # always means an empty queue, and a live one will drain what was just added.
        if watch.queue and (watch.pump is None or watch.pump.done()):
            watch.pump = asyncio.create_task(self._run_pump(watch, ctx))

    async def _run_pump(self, watch: _EagerPart, ctx: RunContext[Any]) -> None:
        """Feed queued statements one at a time; an error stops the part for good."""
        toolset = self._toolset
        if toolset is None:  # pragma: no cover - `__aenter__` binds before any stream event
            raise RuntimeError('`EagerState` is not bound to a `CodeModeToolset`; the toolset was never entered')
        while watch.queue and watch.error is None:
            source = watch.queue.popleft()
            watch.feed_count += 1
            feed_ctx = replace(ctx, tool_call_id=f'{watch.tool_call_id}~e{watch.feed_count}', tool_name='run_code')
            try:
                await toolset.feed_eager_fragment(watch, source, feed_ctx)
            except Exception as e:  # cancellation propagates; everything else is held for the dispatch
                watch.error = e
                watch.queue.clear()

    # -- execution side ---------------------------------------------------------------------

    def pop_watch(self, tool_call_id: str, code: str) -> _EagerPart | None:
        """Remove and return the watch for an executing part, tolerating provider-rewritten ids.

        Falls back to any part whose executed prefix matches the submitted code, since a
        re-keyed part is still the same program.
        """
        watch = self._parts.pop(tool_call_id, None)
        if watch is not None:
            return watch
        rekeyed = next((pid for pid, c in self._parts.items() if code.startswith(c.fed_prefix)), None)
        return self._parts.pop(rekeyed) if rekeyed is not None else None

    async def drain(self, watch: _EagerPart) -> None:
        """Wait until every enqueued statement has been fed (or the part errored).

        At most one pump exists per part: `_scan` enqueues before ensuring the pump, so a
        finished pump means an empty queue, and no new statements arrive once the part's
        arguments are complete.
        """
        if watch.pump is not None:
            await watch.pump

    async def close(self) -> None:
        """Cancel pumps and drop all watches at run end."""
        parts, self._parts = list(self._parts.values()), {}
        for watch in parts:
            pump = watch.pump
            if pump is None or pump.done():
                continue
            pump.cancel()
            try:
                await pump
            except (asyncio.CancelledError, Exception):  # pragma: no cover - cancellation race
                pass
