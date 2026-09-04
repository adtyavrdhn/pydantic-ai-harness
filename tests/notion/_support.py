"""Shared typed state for Notion boundary fakes."""

from typing import Literal, TypedDict


class NotionState(TypedDict):
    """Mutable state recorded by the in-process Notion server."""

    calls: list[tuple[str, dict[str, str]]]
    ai_search_status: Literal['available', 'not_enabled']
    missing_access_tools: set[str]
    unavailable_tools: set[str]
    page_content: str
    user_id: str
    workspace_id: str
