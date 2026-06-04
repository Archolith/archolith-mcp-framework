"""cth.mcp.framework — shared MCP server conventions for the cth.* workspace.

Provides:
- create_gateway_server(): Factory for FastMCP servers with Search Transform + Middleware
- WorkspaceSearchTransform: BM25SearchTransform with workspace-default settings
- ErrorHandlingMiddleware, TimingMiddleware, StructuredLoggingMiddleware, TimeoutMiddleware
- run_server(): Start a FastMCP server on stdio transport
- ToolResponse: Standard response dataclass for tool returns
- BaseGatewayServer: Abstract base class for MCP gateway servers
- PathValidationMixin, ChunkedIOMixin, AuditLogMixin, GitMixin, CompactMixin: Pick-and-mix patterns
"""

from cth_mcp_framework.server import create_gateway_server
from cth_mcp_framework.transforms import WorkspaceSearchTransform
from cth_mcp_framework.middleware import (
    ErrorHandlingMiddleware,
    TimingMiddleware,
    StructuredLoggingMiddleware,
    TimeoutMiddleware,
)
from cth_mcp_framework.runner import run_server
from cth_mcp_framework.response import (
    ToolResponse,
    ERR_NOT_FOUND,
    ERR_INVALID_INPUT,
    ERR_PERMISSION,
    ERR_TOO_LARGE,
    ERR_TIMEOUT,
    ERR_INTERNAL,
)
from cth_mcp_framework.base import BaseGatewayServer
from cth_mcp_framework.mixins import (
    PathValidationMixin,
    ChunkedIOMixin,
    AuditLogMixin,
    GitMixin,
    CompactMixin,
)

__all__ = [
    # Factory + runner
    "create_gateway_server",
    "run_server",
    # Transforms + middleware
    "WorkspaceSearchTransform",
    "ErrorHandlingMiddleware",
    "TimingMiddleware",
    "StructuredLoggingMiddleware",
    "TimeoutMiddleware",
    # OOP layer
    "ToolResponse",
    "ERR_NOT_FOUND",
    "ERR_INVALID_INPUT",
    "ERR_PERMISSION",
    "ERR_TOO_LARGE",
    "ERR_TIMEOUT",
    "ERR_INTERNAL",
    "BaseGatewayServer",
    # Mixins
    "PathValidationMixin",
    "ChunkedIOMixin",
    "AuditLogMixin",
    "GitMixin",
    "CompactMixin",
]
