# Tested Composition

Use this construction smoke test only when a local coding-agent composition is a plausible target. `Coder(workspace)` is shorter when its documented defaults match the source.

The repository's skill-example test executes this example and forbids execution or lint skips, so imports and public constructor signatures cannot drift unnoticed. It does not prove behavioral parity with a source application.

```python
from tempfile import TemporaryDirectory

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from pydantic_ai_harness import (
    FileSystem,
    Planning,
    SlidingWindowCompaction,
    SubAgent,
    SubAgents,
)

worker = Agent(TestModel(), name='researcher', description='Research a bounded question.')

with TemporaryDirectory() as workspace:
    migrated = Agent(
        TestModel(),
        capabilities=[
            Planning(),
            FileSystem(workspace, read_only=True),
            SubAgents(agents=[SubAgent(worker, max_calls=1)], agent_folders=None),
            SlidingWindowCompaction(max_messages=20, keep_messages=10),
        ],
    )
```
