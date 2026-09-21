"""Behavior tests for the copyable cookbook recipes."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest
from pydantic_ai import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

COOKBOOK_DIR = Path(__file__).parent.parent / 'cookbook'


def _load_recipe(name: str) -> ModuleType:
    path = COOKBOOK_DIR / f'{name}.py'
    spec = importlib.util.spec_from_file_location(f'cookbook_{name}', path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_research_brief_returns_cited_structured_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('OPENAI_API_KEY', 'test-key')
    recipe = _load_recipe('research_brief')
    seen_info: list[AgentInfo] = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen_info.append(info)
        return ModelResponse(
            parts=[
                ToolCallPart(
                    info.output_tools[0].name,
                    {
                        'answer': 'Use PostgreSQL when several application instances need concurrent writes.',
                        'evidence': [
                            {
                                'claim': 'PostgreSQL uses multiversion concurrency control.',
                                'source': 'https://www.postgresql.org/docs/current/mvcc-intro.html',
                            }
                        ],
                        'caveats': ['Measure with the application workload before migrating.'],
                    },
                )
            ]
        )

    with recipe.agent.override(model=FunctionModel(respond)):
        result = recipe.agent.run_sync('Should this service move from SQLite to PostgreSQL?')

    assert result.output == recipe.ResearchBrief(
        answer='Use PostgreSQL when several application instances need concurrent writes.',
        evidence=[
            recipe.Evidence(
                claim='PostgreSQL uses multiversion concurrency control.',
                source='https://www.postgresql.org/docs/current/mvcc-intro.html',
            )
        ],
        caveats=['Measure with the application workload before migrating.'],
    )
    assert len(seen_info) == 1
    assert seen_info[0].output_tools[0].name == 'final_result'
    assert {tool.kind for tool in seen_info[0].model_request_parameters.native_tools} == {'web_fetch', 'web_search'}
