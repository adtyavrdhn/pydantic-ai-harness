# Modal Sandbox

`ModalSandbox` supplies a Modal container as the run's `ctx.sandbox` and retains
its released `run_command`, `read_file`, `write_file`, and `list_directory`
tools. Additional tools and capabilities can consume `ctx.sandbox` directly.

[Source code](https://github.com/pydantic/pydantic-ai-harness/tree/main/pydantic_ai_harness/modal_sandbox/)

> While Pydantic AI Harness is on 0.x releases, the API may change between minor releases; when it does, deprecation warnings and release-note migration guidance tell you (or your agent) exactly how to upgrade. See the [version policy](https://github.com/pydantic/pydantic-ai-harness#version-policy).

## Install and authenticate

```bash
uv add "pydantic-ai-harness[modal]"
modal token new
```
In CI, set `MODAL_TOKEN_ID` and `MODAL_TOKEN_SECRET` instead.

## Use with an agent

```python
from pydantic_ai import Agent
from pydantic_ai_harness import ModalSandbox

agent = Agent(
    'anthropic:claude-sonnet-4-6',
    capabilities=[ModalSandbox(image='python:3.12-slim')],
)
```

The released command and file tools remain available to the model. Custom tools
can also use `ctx.sandbox`; prefer argv for commands unless shell syntax is an
intentional part of the tool's schema.

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

`ModalSandboxSession` and `ModalSandboxExecResult` remain available for code
written against the released direct session API. New code should use
`ModalSandboxBackend`, which implements the shared sandbox protocol.

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
- `ModalSandboxTerminalError` as the catchable base for failures a retry cannot fix.
- `ModalSandboxAuthError` when credentials are rejected.
- `ModalSandboxUnavailableError` when the referenced sandbox is not running.
- `ModalSandboxCommandTimeoutError` for command deadlines, also a built-in `TimeoutError`.

Filesystem misses use the built-in `FileNotFoundError` contract.

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
    default_command_timeout=60.0,
    max_command_timeout=None,
    max_output_bytes=51200,
    max_output_lines=2000,
    max_read_bytes=5242880,
    instructions=None,
)
```
