"""Shared scanner for streamed `run_code` arguments.

Streaming tiers of `CodeMode` (eager execution now, speculative execution planned) watch
the model stream the `run_code` tool call and act on the code before the call completes.
Both need the same two primitives: recovering the `code` string prefix from partially
streamed JSON arguments, and deciding which top-level statements of that prefix are
complete enough to act on.
"""

from __future__ import annotations

import ast

from pydantic import TypeAdapter, ValidationError
from pydantic_core import from_json
from typing_extensions import NotRequired, TypedDict


class _PartialArgs(TypedDict):
    """Lenient view of partially streamed `run_code` arguments: only the code we scan."""

    code: NotRequired[object]


_PARTIAL_ARGS_ADAPTER: TypeAdapter[_PartialArgs] = TypeAdapter(_PartialArgs)

_MAX_SCAN_BYTES = 1 << 18
"""Largest streamed prefix the host will `ast.parse`; larger snippets run whole at dispatch.

The host parser has no sandbox around it, so its work must be bounded: repeated parses of a
growing prefix are quadratic, and adversarial nesting exercises parser depth limits.
"""


def decode_partial_code(args_text: str) -> str | None:
    """Recover the `code` string prefix from partially streamed JSON arguments."""
    if not args_text:
        return None
    try:
        decoded = _PARTIAL_ARGS_ADAPTER.validate_python(from_json(args_text, allow_partial='trailing-strings'))
    except (ValueError, ValidationError):
        return None
    code = decoded.get('code')
    return code if isinstance(code, str) else None


def closed_statements(code: str, consumed: int) -> tuple[list[ast.stmt], int]:
    """Return newly closed top-level statements in `code`, past the first `consumed`.

    Only fully streamed lines participate, and the final parsed statement always stays
    provisional: a trailing compound (`for`, `if`, `try`) can still grow an indented body, so a
    statement counts as closed only once a later top-level statement starts on a later line. A prefix that
    does not parse yields nothing -- either an open bracket/triple-quote closes later, or the
    model wrote broken code and the real run will surface the error.

    The full prefix is re-parsed on each delta. That is quadratic in snippet length, which is
    acceptable for model-written snippets; the reference implementation's incremental scanner is
    the known upgrade path.
    """
    end = code.rfind('\n')
    if end < 0 or end >= _MAX_SCAN_BYTES:
        # Oversized prefixes skip host-side parsing entirely: the dispatch hands the code to
        # the sandbox parser, which applies its own resource limits. The cost is losing
        # eagerness for snippets this large, not correctness.
        return [], consumed
    try:
        tree = ast.parse(code[: end + 1])
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        # `ValueError` covers source `ast.parse` rejects before parsing, such as a NUL
        # character that JSON happily encodes; `RecursionError`/`MemoryError` cover parser
        # depth limits on adversarial nesting. The dispatch feed surfaces the real error.
        return [], consumed
    closed: list[ast.stmt] = []
    for stmt, following in zip(tree.body, tree.body[1:]):
        if following.lineno <= (stmt.end_lineno or stmt.lineno):
            # Semicolon-separated statements share a line, and the executed prefix is a
            # line slice: feeding this statement would drag the rest of its line (possibly
            # the provisional final expression) along with it. Hold the whole line back.
            break
        closed.append(stmt)
    return closed[consumed:], max(consumed, len(closed))
