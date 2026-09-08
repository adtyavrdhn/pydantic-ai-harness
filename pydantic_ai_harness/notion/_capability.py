"""Notion capability."""

from __future__ import annotations

from dataclasses import KW_ONLY, dataclass, field

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.tools import AgentDepsT

from pydantic_ai_harness.notion._toolset import MCPToolsetClient, NotionToolset

_DESCRIPTION = "Search, read, and change the authenticated user's Notion workspace."


@dataclass
class Notion(AbstractCapability[AgentDepsT]):
    """Search, read, and change Notion through its official hosted MCP server.

    Every tool Notion offers the connected user is exposed by default; `read_only=True`
    narrows the toolset to search and read tools. Authentication and MCP session state
    stay in Pydantic AI/FastMCP, including when a caller supplies a prebuilt client.
    """

    _: KW_ONLY

    client: MCPToolsetClient = field(repr=False)
    """Caller-owned OAuth client or in-process server for Notion's hosted MCP contract."""

    read_only: bool = False
    """Expose only Notion's search and read tools, dropping the ones that create, update, or move pages,
    databases, views, comments, attachments, and Custom Agent sessions."""

    include_instructions: bool = True
    """Inject Notion identity, search-routing, and mutation guidance."""

    expected_identity: tuple[str, str] | None = None
    """Expected `(workspace_id, user_id)` for a restored connection or deferred mutation."""

    id: str | None = None
    """Optional stable capability and toolset ID.

    Notion tools have fixed names and one MCP client is one authenticated identity, so
    anonymous instances collide instead of merging their access policies. Set an ID for
    deferred loading or durable execution. Build a separate agent or dynamic toolset for
    each connected user rather than sharing an instance across users.
    """

    description: str | None = _DESCRIPTION
    """Routing description used when the capability is loaded on demand."""

    def get_toolset(self) -> NotionToolset[AgentDepsT]:
        """Build the Notion MCP toolset."""
        return NotionToolset[AgentDepsT](
            client=self.client,
            read_only=self.read_only,
            include_instructions=self.include_instructions,
            expected_identity=self.expected_identity,
            id=self.id,
        )

    @classmethod
    def get_serialization_name(cls) -> str | None:
        """Not spec-serializable: the capability holds a live authenticated MCP client."""
        return None
