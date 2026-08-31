# Daytona Sandbox

`DaytonaSandbox` supplies a Daytona sandbox as the run's `ctx.sandbox`. It owns
only provider connection and lifecycle behavior. Add tools or capabilities that
consume `ctx.sandbox` for the model-facing interface you want.

[Source code](https://github.com/pydantic/pydantic-ai-harness/tree/main/pydantic_ai_harness/daytona_sandbox/)

## Install and authenticate

```bash
uv add "pydantic-ai-harness[daytona]"
export DAYTONA_API_KEY=...
```

## Use with an agent

```python
from pydantic_ai import Agent, RunContext
from pydantic_ai_harness.daytona_sandbox import DaytonaSandbox

agent = Agent(
    'anthropic:claude-sonnet-4-6',
    capabilities=[DaytonaSandbox(snapshot='base')],
)


@agent.tool
async def run_command(ctx: RunContext[None], argv: list[str]) -> str:
    result = await ctx.sandbox.run(argv, timeout=60)
    return result.stdout
```

The example deliberately uses argv, which the backend safely quotes into the
shell command Daytona accepts. Use `shell=True` only when the tool deliberately
exposes shell syntax.

## Lifecycle

Owned acquisition derives a Daytona-safe name from the logical run ID. A durable
retry first reconnects by that name. If creation races, a failed create is
followed by one reconnect to the winner. The serialized `SandboxRef` contains the
provider and sandbox ID; later workers reconnect by ID and never create from
`get_sandbox`. Acquisition closes its SDK client after recording the ref, and
release opens a fresh client, resolves the sandbox by ID without starting it, deletes it, and closes the client again.

An already missing sandbox counts as successfully released. Unexpected delete or
client-close failures are surfaced. `auto_stop_minutes` is an idle backstop, not
a substitute for release.

Attach to a sandbox managed elsewhere by ID or name when the capability must not
own its lifetime:

```python
DaytonaSandbox(sandbox_id='existing', workdir='/workspace')
```

Creation-only settings cannot be combined with `sandbox_id`. Attached sandboxes
are not deleted at run end, and concurrent runs share their filesystem and
process space.

## Direct backend use

`DaytonaSandboxBackend` implements Pydantic AI's `SandboxBackend` protocol and
its filesystem and process-start opt-ins:

```python
from pydantic_ai_harness.daytona_sandbox import DaytonaSandboxBackend

backend = await DaytonaSandboxBackend.create(
    snapshot='base',
    auto_stop_minutes=60,
)
try:
    result = await backend.run(['python', '--version'], timeout=60)
    print(result.stdout)
finally:
    await backend.close(terminate=True)
```

Use `connect(sandbox_id_or_name)` for a fresh handle to an existing sandbox; it
never provisions a replacement.

## Process and output behavior

Daytona process sessions provide separate stdout and stderr callbacks. The
backend preserves that separation and joins each stream once when the complete
result is requested. Command setup, log collection, and the final exit-status RPC
share one absolute deadline. Timeout or caller cancellation attempts to delete
the remote process session before returning. If deletion fails, the backend
retains the session identity so `close(terminate=False)` can retry it.

Complete command output is buffered in memory. The backend does not add a second
presentation policy or claim that transport is bounded. Model-facing tools should
apply their own byte or line budget, and commands that can produce very large
output should bound it at the source.

The public error surface is deliberately narrow:

- `DaytonaSandboxError` for provider operations that fail.
- `DaytonaSandboxTerminalError` as the catchable base for failures a retry cannot fix.
- `DaytonaSandboxAuthError` when credentials are rejected.
- `DaytonaSandboxUnavailableError` when the referenced sandbox is missing.
- `DaytonaSandboxCommandTimeoutError` for command deadlines, also a built-in `TimeoutError`.

Filesystem misses use the built-in `FileNotFoundError` contract.

## Configuration

```python
DaytonaSandbox(
    sandbox_id=None,
    snapshot=None,
    auto_stop_minutes=60,
    workdir=None,
    env=None,
    network_block_all=False,
)
```
