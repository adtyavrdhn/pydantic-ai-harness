"""Eager `CodeMode` toolset and its streamed-call state."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field, replace
from typing import Any, Generic, Protocol, runtime_checkable

from pydantic import TypeAdapter, ValidationError
from pydantic_ai import AbstractToolset, RunContext, WrapperToolset
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.messages import AgentStreamEvent, PartDeltaEvent, PartStartEvent, ToolCallPart, ToolCallPartDelta
from pydantic_ai.tools import AgentDepsT
from pydantic_ai.toolsets.abstract import ToolsetTool
from pydantic_core import from_json
from typing_extensions import NotRequired, TypedDict

from ._streaming import MAX_SCAN_CHARS, closed_statements, decode_partial_code
from ._toolset import CodeModeToolset, RunCodeExecution


class RestartArgs(TypedDict):
    restart: NotRequired[object]


RESTART_ARGS_ADAPTER: TypeAdapter[RestartArgs] = TypeAdapter(RestartArgs)
MAX_SCAN_WORK_CHARS = 1 << 20


@runtime_checkable
class _Durability(Protocol):
    in_durable_context: bool


def in_durable_execution(ctx: RunContext[object]) -> bool:
    """Whether a durable executor is active, where eager streaming must stay disabled."""
    return any(
        any(base.__module__.startswith('pydantic_ai.durable_exec') for base in type(capability).__mro__)
        and isinstance(capability, _Durability)
        and capability.in_durable_context
        for capability in ctx.capabilities.values()
    )


@dataclass(kw_only=True)
class StreamedCodeCall:
    """State for one `run_code` call while its arguments are streaming."""

    tool_call_id: str
    execution: RunCodeExecution
    args_text: str = ''
    args_dict: dict[str, Any] | None = None
    consumed: int = 0
    scanned_source: str | None = None
    halted: bool = False
    fed_line_count: int = 0
    fed_prefix: str = ''
    scan_work_chars: int = 0
    queue: deque[str] = field(default_factory=deque[str])
    pump: asyncio.Task[None] | None = None
    error: BaseException | None = None

    def fail(self, error: BaseException) -> None:
        self.error = error
        self.queue.clear()


@dataclass
class EagerExecution(Generic[AgentDepsT]):
    """Coordinate streamed calls and background feeds for one agent run."""

    calls: dict[str, StreamedCodeCall] = field(default_factory=dict[str, StreamedCodeCall], init=False)
    call_ids_by_part_index: dict[int, str] = field(default_factory=dict[int, str], init=False)
    pumps: set[asyncio.Task[None]] = field(default_factory=set[asyncio.Task[None]], init=False)
    feed_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    run_step: int | None = field(default=None, init=False)
    first_tool_part_index: int | None = field(default=None, init=False)

    async def observe(
        self,
        event: AgentStreamEvent,
        ctx: RunContext[AgentDepsT],
        executor: EagerCodeModeToolset[AgentDepsT],
    ) -> None:
        """Consume one stream event and schedule newly completed statements."""
        if self.run_step != ctx.run_step:
            # Argument validation can reject a call before `call_tool` consumes its stream state.
            # Retire that orphan before it can disable eager execution on the retry step.
            stale_calls = list(self.calls.values())
            self.calls.clear()
            self.call_ids_by_part_index.clear()
            self.first_tool_part_index = None
            for call in stale_calls:
                await self.discard(call)
            self.run_step = ctx.run_step

        match event:
            case PartStartEvent(part=ToolCallPart() as part):
                if self.first_tool_part_index is None:
                    self.first_tool_part_index = event.index
                if part.tool_name != 'run_code':
                    return
                call = self.calls.get(part.tool_call_id)
                if call is None:
                    call = StreamedCodeCall(
                        tool_call_id=part.tool_call_id,
                        execution=RunCodeExecution(
                            parent_tool_call_id=part.tool_call_id,
                        ),
                        # Eager work may start only for the response's first tool call. Anything
                        # earlier must reach normal dispatch before this sequential tool can run.
                        halted=event.index != self.first_tool_part_index or bool(self.calls),
                    )
                    self.calls[part.tool_call_id] = call
                    self.call_ids_by_part_index[event.index] = part.tool_call_id
                if isinstance(part.args, str):
                    call.args_text += part.args
                elif isinstance(part.args, dict):
                    call.args_dict = part.args
                if call.halted:
                    return
                await self.scan(call, ctx, executor)

            case PartDeltaEvent(delta=ToolCallPartDelta() as delta):
                call = self.find_call(event.index, delta.tool_call_id)
                if call is None or call.halted:
                    return
                if isinstance(delta.args_delta, str):
                    call.args_text += delta.args_delta
                elif isinstance(delta.args_delta, dict):
                    call.args_dict = {**(call.args_dict or {}), **delta.args_delta}
                if len(call.args_text) > MAX_SCAN_CHARS:
                    call.halted = True
                    return
                await self.scan(call, ctx, executor)

            case _:
                return

    def find_call(self, part_index: int, tool_call_id: str | None) -> StreamedCodeCall | None:
        indexed_call_id = self.call_ids_by_part_index.get(part_index)
        if indexed_call_id is None:
            return None
        if tool_call_id is not None and tool_call_id != indexed_call_id:
            return None
        return self.calls.get(indexed_call_id)

    @staticmethod
    def current_code(call: StreamedCodeCall) -> str | None:
        if call.args_dict is not None:
            code = call.args_dict.get('code')
            return code if isinstance(code, str) else None
        return decode_partial_code(call.args_text)

    @staticmethod
    def restart_requested(call: StreamedCodeCall) -> bool:
        if call.args_dict is not None:
            return bool(call.args_dict.get('restart'))
        if '"restart"' in call.args_text:
            return True
        if not call.args_text or len(call.args_text) > MAX_SCAN_CHARS:
            return False
        try:
            decoded = RESTART_ARGS_ADAPTER.validate_python(from_json(call.args_text, allow_partial='trailing-strings'))
        except (ValueError, ValidationError):  # pragma: no cover - malformed fragments from a provider
            return False
        return bool(decoded.get('restart'))

    async def scan(
        self,
        call: StreamedCodeCall,
        ctx: RunContext[AgentDepsT],
        executor: EagerCodeModeToolset[AgentDepsT],
    ) -> None:
        """Queue top-level statements that have become stable in the stream."""
        input_size = len(call.args_text)
        dict_code: object = None
        if call.args_dict is not None:
            dict_code = call.args_dict.get('code')
            input_size = len(dict_code) if isinstance(dict_code, str) else 0
        # Both partial JSON decoding and AST parsing revisit the growing input. Stop eager
        # scanning once their cumulative input is bounded; final dispatch still runs all code.
        if input_size > MAX_SCAN_WORK_CHARS - call.scan_work_chars:
            call.halted = True
            if isinstance(dict_code, str) and call.fed_prefix and not dict_code.startswith(call.fed_prefix):
                await self.discard(call)
            return
        call.scan_work_chars += input_size

        if self.restart_requested(call):
            call.halted = True
            await self.discard(call)
            return

        code = self.current_code(call)
        if code is None:
            return
        if len(code) > MAX_SCAN_CHARS:
            call.halted = True
            if call.fed_prefix and not code.startswith(call.fed_prefix):
                await self.discard(call)
            return

        source_prefix = code[: code.rfind('\n') + 1]
        if source_prefix == call.scanned_source:
            return
        call.scanned_source = source_prefix

        lines = code.split('\n')
        if call.fed_line_count and '\n'.join(lines[: call.fed_line_count]) != call.fed_prefix:
            call.halted = True
            await self.discard(call)
            return

        statements, call.consumed = closed_statements(code, call.consumed)
        for statement in statements:
            end = statement.end_lineno or statement.lineno
            call.queue.append('\n'.join(lines[call.fed_line_count : end]))
            call.fed_line_count = end
        call.fed_prefix = '\n'.join(lines[: call.fed_line_count])

        if call.queue and (call.pump is None or call.pump.done()):
            pump = asyncio.create_task(self.run_pump(call, ctx, executor))
            call.pump = pump
            self.pumps.add(pump)
            pump.add_done_callback(self.pumps.discard)

    async def run_pump(
        self,
        call: StreamedCodeCall,
        ctx: RunContext[AgentDepsT],
        executor: EagerCodeModeToolset[AgentDepsT],
    ) -> None:
        """Feed a streamed call's queued statements in program order."""
        while call.queue and call.error is None:
            source = call.queue.popleft()
            feed_ctx = replace(ctx, tool_call_id=call.tool_call_id, tool_name='run_code')
            try:
                async with self.feed_lock:
                    await executor.feed_fragment(call, source, feed_ctx)
            except Exception as error:
                call.fail(error)

    def pop_call(self, tool_call_id: str, code: str) -> StreamedCodeCall | None:
        """Take the streamed state for a completed call, tolerating a rewritten call ID."""
        call = self.calls.pop(tool_call_id, None)
        if call is not None:
            return call
        rekeyed = next(
            (
                call_id
                for call_id, candidate in self.calls.items()
                if candidate.fed_prefix and code.startswith(candidate.fed_prefix)
            ),
            None,
        )
        return self.calls.pop(rekeyed) if rekeyed is not None else None

    @staticmethod
    async def drain(call: StreamedCodeCall) -> None:
        if call.pump is not None:
            await call.pump

    async def discard(self, call: StreamedCodeCall) -> None:
        call.queue.clear()
        if call.pump is not None:
            await self.cancel_pump(call.pump)

    @staticmethod
    async def cancel_pump(pump: asyncio.Task[None]) -> None:
        pump.cancel()
        try:
            await pump
        except (asyncio.CancelledError, Exception):  # pragma: no cover - cancellation race
            pass

    async def close(self) -> None:
        """Cancel all background work owned by this run."""
        self.calls.clear()
        self.call_ids_by_part_index.clear()
        self.run_step = None
        self.first_tool_part_index = None
        pumps = list(self.pumps)
        self.pumps.clear()
        for pump in pumps:
            await self.cancel_pump(pump)


@dataclass
class EagerCodeModeToolset(CodeModeToolset[AgentDepsT]):
    """Run-scoped `CodeModeToolset` specialization for streamed execution."""

    execution: EagerExecution[AgentDepsT] = field(default_factory=EagerExecution, init=False, repr=False)

    async def for_run_step(self, ctx: RunContext[AgentDepsT]) -> AbstractToolset[AgentDepsT]:
        new_self = await super().for_run_step(ctx)
        if new_self is self:
            return self
        if not isinstance(new_self, EagerCodeModeToolset):  # pragma: no cover - `replace` preserves the class
            raise TypeError('EagerCodeModeToolset.for_run_step() returned a different toolset type')
        # Step-specific toolset copies participate in the same run-owned stream coordination.
        new_self.execution = self.execution
        return new_self

    async def __aexit__(self, *args: Any) -> bool | None:
        result = None
        try:
            await self.execution.close()
        finally:
            result = await super().__aexit__(*args)
        return result

    async def observe_stream_event(self, event: AgentStreamEvent, ctx: RunContext[AgentDepsT]) -> None:
        await self.execution.observe(event, ctx, self)

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext[AgentDepsT],
        tool: ToolsetTool[AgentDepsT],
    ) -> Any:
        run_code_tool = self._as_run_code_tool(tool)
        if run_code_tool is None or in_durable_execution(ctx):
            return await super().call_tool(name, tool_args, ctx, tool)

        code = tool_args.get('code')
        call = self.execution.pop_call(ctx.tool_call_id or 'pyd_ai_code_mode', code) if isinstance(code, str) else None
        if call is None:
            return await super().call_tool(name, tool_args, ctx, tool)

        if tool_args.get('restart', False):
            await self.execution.discard(call)
            async with self.execution.feed_lock:
                return await super().call_tool(name, tool_args, ctx, tool)

        assert isinstance(code, str)
        lines = code.split('\n')
        if '\n'.join(lines[: call.fed_line_count]) != call.fed_prefix:
            await self.execution.discard(call)
            run_state = self._run_state
            assert run_state is not None, '`CodeModeToolset` must be entered before calling `run_code`'
            run_state.reset()
            raise ModelRetry(
                'The submitted code no longer matches the prefix eager execution already ran, '
                'so the session was restarted. Send the snippet again.'
            )

        await self.execution.drain(call)
        if call.error is not None:
            raise call.error  # pragma: no cover - fragment execution normalizes exceptions to `ModelRetry`

        tail = '\n'.join(lines[call.fed_line_count :])
        async with self.execution.feed_lock:
            result = await self._execute_code(tail, ctx, run_code_tool, call.execution)
        return call.execution.build_tool_return(result)

    async def feed_fragment(
        self,
        call: StreamedCodeCall,
        source: str,
        ctx: RunContext[AgentDepsT],
    ) -> None:
        tools = await self.get_tools(ctx)
        tool = tools['run_code']
        run_code_tool = self._as_run_code_tool(tool)
        if run_code_tool is None:  # pragma: no cover - constructed by `get_tools`
            raise TypeError('CodeModeToolset returned an invalid run_code tool')
        await self._execute_code(source, ctx, run_code_tool, call.execution)


def eager_toolset_from_context(ctx: RunContext[AgentDepsT]) -> EagerCodeModeToolset[AgentDepsT] | None:
    """Return the active step's eager toolset without storing or rebinding it."""
    tool_manager = ctx.tool_manager
    if tool_manager is None or tool_manager.tools is None:
        return None  # pragma: no cover - the agent installs its tool manager before streaming
    tool = tool_manager.tools.get('run_code')
    if tool is None:
        return None  # pragma: no cover - eager `CodeMode` always contributes `run_code`
    toolset = tool.toolset
    while isinstance(toolset, WrapperToolset):
        if isinstance(toolset, EagerCodeModeToolset):
            return toolset
        toolset = toolset.wrapped
    return None
