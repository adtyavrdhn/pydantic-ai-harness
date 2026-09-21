"""Research a decision and return a brief whose evidence links to its sources."""

from pydantic import BaseModel, HttpUrl
from pydantic_ai import Agent

from pydantic_ai_harness import Researcher


class Evidence(BaseModel):
    """A factual claim and the source that supports it."""

    claim: str
    source: HttpUrl


class ResearchBrief(BaseModel):
    """A source-backed answer to the research question."""

    answer: str
    evidence: list[Evidence]
    caveats: list[str]


agent = Agent(
    'openai:gpt-5.6-sol',
    output_type=ResearchBrief,
    capabilities=[Researcher()],
)


if __name__ == '__main__':
    result = agent.run_sync(
        'Should a growing SaaS move its primary database from SQLite to PostgreSQL '
        'before it starts running multiple application instances? Use current official sources.'
    )
    print(result.output.model_dump_json(indent=2))
