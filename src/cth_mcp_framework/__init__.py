"""cth.mcp.framework — shared MCP server conventions for the cth.* workspace.

Provides:
- create_gateway_server(): Factory for FastMCP servers with Search Transform + Middleware
- WorkspaceSearchTransform: BM25SearchTransform with workspace-default settings
- ErrorHandlingMiddleware, TimingMiddleware, StructuredLoggingMiddleware
- run_server(): Start a FastMCP server on stdio transport
"""

from cth_mcp_framework.server import create_gateway_server
from cth_mcp_framework.transforms import WorkspaceSearchTransform
from cth_mcp_framework.middleware import (
    ErrorHandlingMiddleware,
    TimingMiddleware,
    StructuredLoggingMiddleware,
)
from cth_mcp_framework.runner import run_server

__all__ = [
    "create_gateway_server",
    "WorkspaceSearchTransform",
    "ErrorHandlingMiddleware",
    "TimingMiddleware",
    "StructuredLoggingMiddleware",
    "run_server",
]
