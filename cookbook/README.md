# Pydantic AI Cookbook

Small, complete programs for common agent tasks. Each recipe uses a current model and the recommended Pydantic AI and Harness interfaces.

## Research a decision with sources

[`research_brief.py`](research_brief.py) searches the web, reads relevant pages, delegates focused research when useful, and returns a validated brief with a source URL for each factual claim.

```bash
uv run --with 'pydantic-ai-harness[researcher]' cookbook/research_brief.py
```

Set `OPENAI_API_KEY` before running it. Change the question passed to `run_sync()` to research your own decision.
