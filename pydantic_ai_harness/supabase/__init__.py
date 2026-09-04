"""Supabase capability for the official hosted MCP server.

Requires Pydantic AI's `mcp` extra: `uv add pydantic-ai-harness "pydantic-ai-slim[mcp]"`.
"""

from pydantic_ai_harness.supabase._capability import Supabase, SupabaseFeature

__all__ = ['Supabase', 'SupabaseFeature']
