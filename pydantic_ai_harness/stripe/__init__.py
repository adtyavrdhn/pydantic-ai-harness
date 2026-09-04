"""Stripe capability for account-scoped API access over Stripe's hosted MCP server.

Install Pydantic AI's MCP support with `uv add "pydantic-ai-slim[mcp]"`.
"""

from pydantic_ai_harness.stripe._capability import Stripe

__all__ = ['Stripe']
