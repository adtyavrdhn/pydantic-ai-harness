"""Shared helpers for nested agent usage accounting."""

from __future__ import annotations

from dataclasses import replace

from pydantic_ai.usage import UsageLimits


def reserved_usage_limits(limits: UsageLimits | None, *, reserve: int = 1) -> UsageLimits | None:
    """Reserve already-claimed requests before a nested model call made from a hook.

    The hook may run after the parent request's limit check, so the parent's pending request is
    always one reservation. A caller launching nested calls concurrently reserves one more per
    call already in flight, since each of those has passed its own limit check without its spend
    being recorded on the shared usage yet. Reducing a finite request limit prevents the nested
    call from spending requests that were already approved elsewhere.
    """
    if limits is None or limits.request_limit is None:
        return limits
    return replace(limits, request_limit=max(0, limits.request_limit - reserve))
