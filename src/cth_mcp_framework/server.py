"""create_gateway_server() — factory for FastMCP servers with workspace conventions.

Creates a FastMCP server pre-configured with:
- WorkspaceSearchTransform (collapses tool catalog into search_tools + call_tool)
- ErrorHandlingMiddleware + TimingMiddleware (default cross-cutting concerns)
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

from fastmcp import FastMCP
from fastmcp.server.middleware import Middleware

from cth_mcp_framework.middleware import ErrorHandlingMiddleware, TimingMiddleware
from cth_mcp_framework.transforms import WorkspaceSearchTransform


def create_gateway_server(
    name: str,
    instructions: str = "",
    *,
    lifespan: Any = None,
    always_visible: list[str] | None = None,
    middlewares: Sequence[Middleware] | None = None,
    max_results: int = 10,
    schema_abbreviated: bool = False,
) -> FastMCP:
    """Create a FastMCP server with workspace-standard Search Transform and middleware.

    Args:
        name: Server name (e.g. "cth.home", "yawn.vps").
        instructions: Human-readable description shown to LLM clients.
        lifespan: Optional async context manager for server lifespan (e.g. backend init).
        always_visible: Tool names that stay in list_tools output alongside
        search_tools + call_tool. Pin the most-used discovery tools here.
        middlewares: Custom middleware sequence. Defaults to
        [ErrorHandlingMiddleware(), TimingMiddleware()].
        max_results: Maximum tools returned per search query. Default 10.
        schema_abbreviated: After the first list_tools call, abbreviate
        pinned (always_visible) tool schemas to name + 1-line description +
        required params only. Saves ~60% per-turn schema tokens.

    Returns:
        A configured FastMCP instance ready for @mcp.tool() registration.
    """
    transform = WorkspaceSearchTransform(
        max_results=max_results,
        always_visible=always_visible,
        abbreviate_visible=schema_abbreviated,
    )

    if middlewares is None:
        middlewares = [ErrorHandlingMiddleware(), TimingMiddleware()]

    kwargs: dict[str, Any] = dict(
        name=name,
        instructions=instructions,
        transforms=[transform],
        middleware=list(middlewares),
    )
    if lifespan is not None:
        kwargs["lifespan"] = lifespan

    mcp = FastMCP(**kwargs)

    return mcp
