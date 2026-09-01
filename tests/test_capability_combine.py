"""How two harness capabilities that resolve to the same `id` compose.

Every capability this package ships is listed in `COMBINE_POLICY`, and
`test_every_capability_declares_a_combine_policy` fails when one is missing. Adding a capability is
therefore a decision about what two of it mean, taken once, here.

The core half of this lives in `pydantic-ai`'s `tests/test_capability_combine.py`.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
import warnings
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from importlib.util import find_spec
from typing import Any, TypeGuard

import pytest
from pydantic_ai import Agent
from pydantic_ai.capabilities import CombinedCapability
from pydantic_ai.capabilities.abstract import (
    AbstractCapability,
    combine_duplicate_capabilities,
    leaf_capabilities,
)
from pydantic_ai.models.test import TestModel

import pydantic_ai_harness
from pydantic_ai_harness import (
    Advisor,
    Coder,
    Memory,
    Planning,
    Researcher,
    SpendLimits,
    StepPersistence,
    SubAgent,
    SubAgents,
    SummarizingCompaction,
    SystemReminders,
    ToolOutputLimits,
)
from pydantic_ai_harness.system_reminders import Reminder

pytestmark = pytest.mark.anyio


@dataclass
class Anonymous:
    """No default `id`: two of these are two different things, so `combine` is never reached."""

    reason: str


@dataclass
class Combines:
    """A default `id`: two of these are one configuration stated twice, and `combine` resolves them."""

    reason: str
    make: Callable[[], tuple[AbstractCapability[Any], AbstractCapability[Any]]]
    check: Callable[[Any], None]


Policy = Anonymous | Combines


def _check_memory(merged: Any) -> None:
    assert merged.heading == 'Second'


def _check_planning(merged: Any) -> None:
    assert merged.inject is False


def _check_spend_limits(merged: Any) -> None:
    assert merged.expose_tools is False


def _check_step_persistence(merged: Any) -> None:
    assert merged.agent_name == 'second'


def _check_summarizing(merged: Any) -> None:
    assert merged.keep_messages == 7


def _check_system_reminders(merged: Any) -> None:
    # Additive sequences union, so every reminder either side declared still fires.
    assert [r.content for r in merged.reminders] == ['first', 'second']


def _check_tool_output_limits(merged: Any) -> None:
    assert merged.strip_ansi is True


def _check_advisor(merged: Any) -> None:
    assert merged.max_tokens == 4096


def _check_sub_agents(merged: Any) -> None:
    # Rosters union: an agent either side could reach stays reachable through one delegate tool.
    assert [entry.agent.name for entry in merged.agents] == ['alpha', 'beta']


COMBINE_POLICY: dict[str, Policy] = {
    # -- One per agent: a default `id`, and `combine` says what two of them mean. --
    'Memory': Combines(
        'one memory configuration per agent; its toolset registers fixed tool names',
        lambda: (Memory[Any](heading='First'), Memory[Any](heading='Second')),
        _check_memory,
    ),
    'Planning': Combines(
        'one plan per agent; `PlanningToolset` registers fixed tool names',
        lambda: (Planning[Any](), Planning[Any](inject=False)),
        _check_planning,
    ),
    'SpendLimits': Combines(
        'one spend authority per agent',
        lambda: (SpendLimits[Any](), SpendLimits[Any](expose_tools=False)),
        _check_spend_limits,
    ),
    'StepPersistence': Combines(
        'one run identity per agent',
        lambda: (StepPersistence[Any](agent_name='first'), StepPersistence[Any](agent_name='second')),
        _check_step_persistence,
    ),
    'SummarizingCompaction': Combines(
        'two would each make a model call, the second summarizing the first summary',
        lambda: (
            SummarizingCompaction[Any](max_messages=50, keep_messages=3),
            SummarizingCompaction[Any](max_messages=50, keep_messages=7),
        ),
        _check_summarizing,
    ),
    'SystemReminders': Combines(
        'reminders are additive, so both sides keep firing',
        lambda: (
            SystemReminders[Any](reminders=[Reminder('first')]),
            SystemReminders[Any](reminders=[Reminder('second')]),
        ),
        _check_system_reminders,
    ),
    'ToolOutputLimits': Combines(
        'one output-limit policy; `tool_filter`/`per_tool` are how one instance varies by tool',
        lambda: (ToolOutputLimits[Any](), ToolOutputLimits[Any](strip_ansi=True)),
        _check_tool_output_limits,
    ),
    'SubAgents': Combines(
        'one delegate tool per agent, so two rosters become one',
        lambda: (
            SubAgents[Any](agents=[SubAgent(Agent(TestModel(), name='alpha'))]),
            SubAgents[Any](agents=[SubAgent(Agent(TestModel(), name='beta'))]),
        ),
        _check_sub_agents,
    ),
    'Advisor': Combines(
        'one advisor per agent; its tool name is fixed',
        lambda: (
            Advisor[Any]('anthropic:claude-fable-5', max_tokens=2048),
            Advisor[Any]('anthropic:claude-fable-5', max_tokens=4096),
        ),
        _check_advisor,
    ),
    # -- Several of these is the normal case, so they stay anonymous. --
    'Coder': Anonymous('a packaged harness; composing two is composing their members'),
    'Researcher': Anonymous('a packaged harness; composing two is composing their members'),
    'CapabilityCreation': Anonymous('one per authoring directory'),
    'ClampOversizedMessages': Anonymous('clamping twice is a no-op; several thresholds compose'),
    'ClearToolResults': Anonymous('several form an escalation ladder, like `TieredCompaction` tiers'),
    'DeduplicateFileReads': Anonymous('file-read identification is agent-specific; one per `file_key`'),
    'CodeMode': Anonymous('one per sandboxed execution surface'),
    'ConversationSearch': Anonymous('one per searchable source'),
    'DynamicWorkflow': Anonymous('one per workflow definition'),
    'FileSystem': Anonymous('one per rooted directory, with its own allow/deny patterns'),
    'InputGuardrail': Anonymous('several guards is the design'),
    'OutputGuardrail': Anonymous('several guards is the design'),
    'ToolGuardrail': Anonymous('several guards is the design'),
    'LocalStack': Anonymous('one per endpoint'),
    'Macroscope': Anonymous('one per configured command'),
    'ManagedPrompt': Anonymous('one per prompt name'),
    'ModalSandbox': Anonymous('one per sandbox'),
    'PydanticAIDocs': Anonymous('one per docs source'),
    'PyaiDocs': Anonymous('deprecated alias of `PydanticAIDocs`'),
    'RepoContext': Anonymous('one per workspace root'),
    'ReportContextUsage': Anonymous('a passive observer; several callbacks compose'),
    'Shell': Anonymous('one per working directory, with its own allow/deny lists'),
    'Skills': Anonymous('one per skills directory'),
    'SlidingWindowCompaction': Anonymous('composes as a tier under `TieredCompaction`'),
    'TieredCompaction': Anonymous('drives other strategies; one per tier list'),
    'WarnNearLimits': Anonymous('a passive observer; several thresholds compose'),
    'WarnOnCacheBusts': Anonymous('a passive observer; several thresholds compose'),
}


def _is_capability_class(obj: object) -> TypeGuard[type[AbstractCapability[Any]]]:
    """Whether `obj` is a capability class, and not something that merely looks like one.

    A module's namespace holds type aliases and parameterized generics beside its classes, and on
    Python 3.10 some of those satisfy `inspect.isclass` while `issubclass` then raises on them.
    """
    if not isinstance(obj, type):
        return False
    try:
        return issubclass(obj, AbstractCapability)
    except TypeError:  # pragma: no cover
        return False


def _shipped_capability_types() -> dict[str, type[AbstractCapability[Any]]]:
    """Every capability class in `pydantic_ai_harness`, public or not.

    Walks the package rather than reading an export list, so a capability that is never re-exported
    is still covered. Modules whose optional dependency group is not installed are skipped -- the
    import error means the capability could not have been used either.
    """
    found: dict[str, type[AbstractCapability[Any]]] = {}
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        for module_info in pkgutil.walk_packages(pydantic_ai_harness.__path__, f'{pydantic_ai_harness.__name__}.'):
            try:
                module = importlib.import_module(module_info.name)
            except Exception:  # pragma: no cover
                continue
            for obj in vars(module).values():
                if _is_capability_class(obj) and obj.__module__.startswith(pydantic_ai_harness.__name__):
                    found[obj.__name__] = obj
    return found


def _default_id(capability_type: type[AbstractCapability[Any]]) -> str | None:
    """The `id` an instance gets when the caller names none.

    Read from the class attribute *and* from `__init__`'s signature: a dataclass declares the
    default as a field, but a capability with a hand-written `__init__` (`Advisor`, the
    `NativeOrLocalTool` subclasses) passes it to `super().__init__` instead, where a class-attribute
    check would not see it.
    """
    class_default = getattr(capability_type, 'id', None)
    if isinstance(class_default, str):
        return class_default
    try:
        init_default = inspect.signature(capability_type.__init__).parameters['id'].default
    except (ValueError, TypeError, KeyError):  # pragma: no cover
        return None
    return init_default if isinstance(init_default, str) else None


def test_every_capability_declares_a_combine_policy() -> None:
    """A new capability must say what two of it mean before it can ship.

    Without this the answer defaults to whatever `AbstractCapability` does, which is the one
    outcome nobody chose. Add an entry to `COMBINE_POLICY`: `Anonymous` when several per agent is
    normal, `Combines` when it carries a default `id`.
    """
    shipped = set(_shipped_capability_types())
    declared = set(COMBINE_POLICY)
    assert not (shipped - declared), (
        f'capabilities with no `COMBINE_POLICY` entry: {sorted(shipped - declared)}. '
        'Decide what two of them mean and add an entry.'
    )
    assert not (declared - shipped), (
        f'`COMBINE_POLICY` names capabilities that no longer exist: {sorted(declared - shipped)}.'
    )


@pytest.mark.parametrize('name', sorted(COMBINE_POLICY))
def test_capability_combine_policy_holds(name: str) -> None:
    """Each capability composes -- or refuses to -- the way its policy says."""
    policy = COMBINE_POLICY[name]
    shipped = _shipped_capability_types()
    if name not in shipped:  # pragma: no cover
        pytest.skip(f'{name} needs an optional dependency group that is not installed')
    capability_type = shipped[name]

    if isinstance(policy, Anonymous):
        assert _default_id(capability_type) is None, (
            f'{name} is declared `Anonymous` but carries a default id {_default_id(capability_type)!r}'
        )
        return

    first, second = policy.make()
    assert first.id is not None and first.id == second.id, (
        f'{name} is declared `Combines` but two instances do not share an id'
    )
    policy.check(type(first).combine([first, second]))


@pytest.mark.skipif(
    find_spec('ddgs') is None or find_spec('markdownify') is None,
    reason='`Researcher` needs the `researcher` optional group.',
)
async def test_coder_and_researcher_compose() -> None:
    """The composition #7781 was filed for: two packaged harnesses on one agent.

    Both build a `ToolOutputLimits` and both delegate, so before `combine` they collided twice --
    on the capability id, and then on the `delegate_task` tool name.
    """
    tree = CombinedCapability([Coder[Any](), Researcher[Any]()])
    counts = Counter(type(leaf).__name__ for leaf in leaf_capabilities(tree))
    assert counts['ToolOutputLimits'] == 2
    assert counts['SubAgents'] == 2

    combined = combine_duplicate_capabilities(tree)

    leaves = leaf_capabilities(combined)
    merged_counts = Counter(type(leaf).__name__ for leaf in leaves)
    assert merged_counts['ToolOutputLimits'] == 1
    assert merged_counts['SubAgents'] == 1
    # Neither harness loses a delegate: the rosters union under one `delegate_task` tool.
    sub_agents = next(leaf for leaf in leaves if isinstance(leaf, SubAgents))
    assert [entry.agent.name for entry in sub_agents.agents] == ['explorer', 'researcher']
