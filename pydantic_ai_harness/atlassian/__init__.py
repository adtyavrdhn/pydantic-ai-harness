"""Atlassian Rovo MCP integration for Jira and selected related products.

Requires Pydantic AI's MCP dependencies: `uv add "pydantic-ai-slim[mcp]"`.
"""

from pydantic_ai_harness.atlassian._capability import Atlassian
from pydantic_ai_harness.atlassian._toolset import (
    ATLASSIAN_MCP_URL,
    AtlassianAccess,
    AtlassianProduct,
    AtlassianToolset,
)

__all__ = [
    'ATLASSIAN_MCP_URL',
    'Atlassian',
    'AtlassianAccess',
    'AtlassianProduct',
    'AtlassianToolset',
]
