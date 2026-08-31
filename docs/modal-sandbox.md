---
title: Modal Sandbox
description: Supply a Modal container through ctx.sandbox.
---

# Modal Sandbox

`ModalSandbox` supplies a Modal container as the run's `ctx.sandbox`. It owns
only provider connection and lifecycle behavior. Add tools or capabilities that
consume `ctx.sandbox` for the model-facing interface you want.

[Source code](https://github.com/pydantic/pydantic-ai-harness/tree/main/pydantic_ai_harness/modal_sandbox/)

## Install and authenticate

```bash
uv add "pydantic-ai-harness[modal]"
modal token new
```
In CI, set `MODAL_TOKEN_ID` and `MODAL_TOKEN_SECRET` instead.

## Use with an agent

```python
from pydantic_ai import Agent, RunContext
from pydantic_ai_harness import ModalSandbox

agent = Agent(
    'anthropic:claude-sonnet-4-6',
    capabilities=[ModalSandbox(image='python:3.12-slim')],
)


@agent.tool
async def run_command(ctx: RunContext[None], argv: list[str]) -> str:
    result = await ctx.sandbox.run(argv, timeout=60)
    return result.stdout
```

The example deliberately uses argv rather than a shell string. If your tool needs
shell syntax, call `ctx.sandbox.run(command, shell=True, timeout=...)` and make
that power explicit in the tool's schema and description.

## Lifecycle

For an owned sandbox, acquisition derives a Modal-safe name from the logical run
ID. A retry reconnects to that named sandbox instead of provisioning a duplicate.
The serialized `SandboxRef` contains the provider and sandbox ID; later workers
reconnect by ID and never create from `get_sandbox`. Release also reconnects by ID
and terminates the sandbox, so it works in a different worker and is safe to retry.
An already terminated sandbox counts as successfully released.

Modal's `sandbox_timeout` is a server-side cleanup backstop. Unexpected control
plane or authentication failures during release are surfaced rather than silently
reported as successful cleanup.

Attach to a sandbox managed elsewhere by ID when the capability must not own its
lifetime:

```python
ModalSandbox(sandbox_id='sb-abc123')
```

Creation-only settings cannot be combined with `sandbox_id`. Attached sandboxes
are not terminated at run end, and concurrent runs share their filesystem and
process space.

## Direct backend use

`ModalSandboxBackend` implements Pydantic AI's `SandboxBackend` protocol and the
filesystem, process-start, and output-streaming opt-ins:

```python
from pydantic_ai_harness.modal_sandbox import ModalSandboxBackend

backend = await ModalSandboxBackend.create(
    image='python:3.12-slim',
    sandbox_timeout=1800,
)
try:
    result = await backend.run(['python', '--version'], timeout=60)
    print(result.stdout)
finally:
    await backend.close(terminate=True)
```

`create_or_connect(name=...)` is available for callers that need the same
retry-safe named lifecycle used by the capability. `connect(sandbox_id)` and
`connect_name(app_name, name)` only reconnect.

## Limits and cancellation

Modal does not expose a way to kill one command. `SandboxProcess.kill()` therefore
raises `NotImplementedError`. Give every command a finite `timeout`; cancellation
of the client wait does not otherwise guarantee that the remote process stops.
Modal accepts whole-second deadlines, so fractional values round up.

Command results are buffered by Modal and returned in full. The backend does not
invent a model-output policy or pretend the transport is bounded. Tools that put
output into model context should enforce their own byte or line budget, and
commands that may produce very large output should bound it at the source.
Streaming decodes UTF-8 incrementally and replaces invalid byte sequences.

The public error surface is deliberately narrow:

- `ModalSandboxError` for provider operations that fail.
- `ModalSandboxAuthError` when credentials are rejected.
- `ModalSandboxUnavailableError` when the referenced sandbox is not running.

Filesystem misses use the built-in `FileNotFoundError`, and command deadlines use
the built-in `TimeoutError` contract.

## Configuration

```python
ModalSandbox(
    image='python:3.12-slim',
    sandbox_id=None,
    app_name='pydantic-ai-harness',
    create_app_if_missing=True,
    sandbox_timeout=300,
    workdir=None,
    env=None,
)
```
