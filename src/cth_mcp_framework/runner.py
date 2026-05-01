"""Shared runner for cth.* MCP servers.

Provides a standard main() function that starts a FastMCP server
on stdio transport, which is the workspace default.
"""

from __future__ import annotations

from fastmcp import FastMCP


def run_server(mcp: FastMCP) -> None:
    """Start a FastMCP server on stdio transport.

    Usage in server entry points::

        from cth_mcp_framework.runner import run_server
        from my_server import mcp

        def main() -> None:
            run_server(mcp)
    """
    mcp.run(transport="stdio")
