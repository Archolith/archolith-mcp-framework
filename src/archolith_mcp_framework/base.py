"""BaseGatewayServer — abstract base class for MCP gateway servers.

Subclass this, set class attributes (name, instructions, always_visible),
override _register_tools(), then call run() or pass self.mcp to run_server().

The factory function create_gateway_server() remains the foundation; this
base class is a convenience layer on top.
"""

from __future__ import annotations

from typing import Any, Sequence

from fastmcp import FastMCP
from fastmcp.server.middleware import Middleware

from archolith_mcp_framework.runner import run_server
from archolith_mcp_framework.server import create_gateway_server


class BaseGatewayServer:
    """Abstract base for MCP gateway servers.

    Subclass this, call super().__init__(), register tools with
    @self.mcp.tool() or @self.tool(), then call run() or pass
    self.mcp to run_server().

    Attributes:
        name: Server name (e.g. "example.gateway", "yawn.vps").
        instructions: Human-readable description shown to LLM clients.
        always_visible: Tool names that stay visible alongside
            search_tools + call_tool.
    """

    name: str = ""
    instructions: str = ""
    always_visible: list[str] = []
    schema_abbreviated: bool = False

    def __init__(
        self,
        *,
        lifespan: Any = None,
        middlewares: Sequence[Middleware] | None = None,
        max_results: int = 10,
    ) -> None:
        self.mcp: FastMCP = create_gateway_server(
            self.name,
            instructions=self.instructions,
            always_visible=self.always_visible or None,
            lifespan=lifespan,
            middlewares=middlewares,
            max_results=max_results,
            schema_abbreviated=self.schema_abbreviated,
        )
        self._register_tools()

    def _register_tools(self) -> None:
        """Override to register @self.mcp.tool() functions.

        This is called automatically during __init__().
        """

    def tool(self, fn=None, **kwargs: Any) -> Any:
        """Decorator shorthand: @self.tool() instead of @self.mcp.tool()."""
        return self.mcp.tool(fn, **kwargs)

    def run(self) -> None:
        """Start the server on stdio transport."""
        run_server(self.mcp)
