"""create_gateway_server() — factory for FastMCP servers with workspace conventions.

Creates a FastMCP server pre-configured with:
- WorkspaceSearchTransform (collapses tool catalog into search_tools + call_tool)
- ErrorHandlingMiddleware + TimingMiddleware (default cross-cutting concerns)
"""

from __future__ import annotations

from collections.abc import Sequence

from fastmcp import FastMCP
from fastmcp.server.middleware import Middleware

from cth_mcp_framework.middleware import ErrorHandlingMiddleware, TimingMiddleware
from cth_mcp_framework.transforms import WorkspaceSearchTransform


def create_gateway_server(
    name: str,
    instructions: str,
    *,
    always_visible: list[str] | None = None,
    middlewares: Sequence[Middleware] | None = None,
    max_results: int = 10,
) -> FastMCP:
    """Create a FastMCP server with workspace-standard Search Transform and middleware.

    Args:
        name: Server name (e.g. "cth.home", "yawn.vps").
        instructions: Human-readable description shown to LLM clients.
        always_visible: Tool names that stay in list_tools output alongside
            search_tools + call_tool. Pin the most-used discovery tools here.
        middlewares: Custom middleware sequence. Defaults to
            [ErrorHandlingMiddleware(), TimingMiddleware()].
        max_results: Maximum tools returned per search query. Default 10.

    Returns:
        A configured FastMCP instance ready for @mcp.tool() registration.
    """
    transform = WorkspaceSearchTransform(
        max_results=max_results,
        always_visible=always_visible,
    )

    if middlewares is None:
        middlewares = [ErrorHandlingMiddleware(), TimingMiddleware()]

    mcp = FastMCP(
        name=name,
        instructions=instructions,
        transforms=[transform],
        middleware=list(middlewares),
    )

    return mcp
