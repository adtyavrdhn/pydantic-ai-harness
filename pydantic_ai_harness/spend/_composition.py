"""Report a capability arrangement that can hide billed responses on older Pydantic AI releases.

Without provider-response accounting from core, `SpendLimits` accrues the response returned
through its own `wrap_model_request`, so anything nested further in can reject a response the
counter has not recorded yet. Pydantic AI sorts the `innermost` tier against everything else
but not against itself, so the arrangement is reached by listing capabilities in a particular
order rather than by anything going wrong, and the resulting chain is readable from
[`RunContext.root_capability`][pydantic_ai.tools.RunContext.root_capability].

What is read here is the ordering. Whether a nested wrapper actually rejects a billed
response depends on its own configuration and on how a given run races, none of which is
visible from the chain, so this reports an arrangement rather than an under-count that has
happened. That is also why it warns rather than refuses.
"""

from __future__ import annotations

import warnings

from pydantic_ai.capabilities import AbstractCapability, CombinedCapability, Hooks, WrapperCapability
from pydantic_ai.durable_exec._base import BaseDurabilityCapability  # pyright: ignore[reportPrivateUsage]
from pydantic_ai.tools import AgentDepsT

from pydantic_ai_harness.spend._exceptions import SpendCompositionWarning


def warn_about_inner_wrappers(
    root: AbstractCapability[AgentDepsT] | None,
    capability: AbstractCapability[AgentDepsT],
    reported: set[str],
) -> None:
    """Warn when a capability in `root` wraps inside `capability`'s model wrapper.

    `reported` accumulates the arrangements already warned about and is read and written here,
    so the same one reports once however many requests or runs it survives. Deduplicating on
    the arrangement rather than on the first call is what lets a reused agent be read again:
    `agent.run(capabilities=[...])` can put a different chain around the same capability
    instance on every run, and a flag set by the first, safe chain would hide every later one.

    The arrangement is recorded after `warnings.warn` returns rather than before, so that an
    application escalating this category to an error with `filterwarnings('error', ...)` keeps
    getting one on every run of the arrangement. Recording it first would let the raise happen
    once and then mark the arrangement as reported, which turns a refusal into a first-run-only
    one.
    """
    inner = _inner_wrappers(root, capability)
    if not inner:
        return
    listed = ', '.join(inner)
    if listed in reported:
        return
    name = type(capability).__name__
    warnings.warn(
        f'These capabilities are listed after `{name}`, so they wrap inside it: {listed}. '
        'If one of them rejects a response it has already awaited, the provider billed that '
        'response and the accrual never sees it. This reads the ordering, not what those '
        f'capabilities do with it. List `{name}` last among the innermost capabilities to rule it out.',
        SpendCompositionWarning,
        stacklevel=2,
    )
    reported.add(listed)


def _inner_wrappers(
    root: AbstractCapability[AgentDepsT] | None,
    capability: AbstractCapability[AgentDepsT],
) -> list[str]:
    """Names of capabilities in `root` whose model wrappers run inside `capability`'s."""
    if not isinstance(root, CombinedCapability):
        return []
    chain: list[AbstractCapability[AgentDepsT]] = list(root.capabilities)
    for position, member in enumerate(chain):
        if _stands_in_for(member, capability):
            return [type(inner).__name__ for inner in chain[position + 1 :] if _may_reject_a_billed_response(inner)]
    return []


def _stands_in_for(member: AbstractCapability[AgentDepsT], capability: AbstractCapability[AgentDepsT]) -> bool:
    """Whether this chain member is `capability`, or a wrapper chain that reaches it."""
    while member is not capability:
        if not isinstance(member, WrapperCapability):
            return False
        member = member.wrapped
    return True


def _may_reject_a_billed_response(capability: AbstractCapability[AgentDepsT]) -> bool:
    """Whether nesting this capability inside the accrual is worth reporting.

    `Hooks` defines the wrapper unconditionally, so its definition cannot reveal whether a
    model-request hook was registered. `WrapperCapability` delegates to what it wraps, so the
    question moves to that capability unless the wrapper subclass supplies its own method.
    Durable-execution capabilities are excluded because core requires their dispatch wrapper
    to remain innermost; reordering is not an available correction for them.
    """
    implementation = type(capability).wrap_model_request
    if isinstance(capability, WrapperCapability) and implementation is WrapperCapability.wrap_model_request:
        return _may_reject_a_billed_response(capability.wrapped)
    if isinstance(capability, BaseDurabilityCapability):
        return False
    return (
        implementation is not AbstractCapability.wrap_model_request and implementation is not Hooks.wrap_model_request
    )
