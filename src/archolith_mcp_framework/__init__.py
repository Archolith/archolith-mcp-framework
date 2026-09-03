"""archolith-mcp-framework — shared FastMCP infrastructure.

Provides:
- create_gateway_server(): Factory for FastMCP servers with Search Transform + Middleware
- WorkspaceSearchTransform: BM25SearchTransform with workspace-default settings
- ErrorHandlingMiddleware, TimingMiddleware, StructuredLoggingMiddleware, TimeoutMiddleware
- run_server(): Start a FastMCP server on stdio transport
- ToolResponse: Standard response dataclass for tool returns
- BaseGatewayServer: Abstract base class for MCP gateway servers
- PathValidationMixin, ChunkedIOMixin, AuditLogMixin, GitMixin, CompactMixin: Pick-and-mix patterns
"""

from archolith_mcp_framework.server import create_gateway_server
from archolith_mcp_framework.transforms import WorkspaceSearchTransform
from archolith_mcp_framework.middleware import (
    ErrorHandlingMiddleware,
    TimingMiddleware,
    StructuredLoggingMiddleware,
    TimeoutMiddleware,
)
from archolith_mcp_framework.runner import run_server
from archolith_mcp_framework.response import (
    ToolResponse,
    ERR_NOT_FOUND,
    ERR_INVALID_INPUT,
    ERR_PERMISSION,
    ERR_TOO_LARGE,
    ERR_TIMEOUT,
    ERR_INTERNAL,
)
from archolith_mcp_framework.base import BaseGatewayServer
from archolith_mcp_framework.duration_stats import (
    DurationEstimate,
    record_duration,
    estimate_duration,
)
from archolith_mcp_framework.jobs import (
    start_job,
    job_status,
    job_eta,
    cancel_job,
)
from archolith_mcp_framework.mixins import (
    PathValidationMixin,
    ChunkedIOMixin,
    AuditLogMixin,
    GitMixin,
    CompactMixin,
    JobControlMixin,
)
from archolith_mcp_framework.http import (
    RequestContextMiddleware,
    get_request_id,
    get_request_id_from_scope,
)
from archolith_mcp_framework.resilience import (
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
    CircuitTransition,
    should_trip_circuit,
)
from archolith_mcp_framework.pagination import (
    DEFAULT_PAGE_LIMIT,
    MAX_PAGE_LIMIT,
    PageWindow,
    PaginationMixin,
    page_slice,
    paginate,
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
    # Duration stats / ETA
    "DurationEstimate",
    "record_duration",
    "estimate_duration",
    # Async job registry
    "start_job",
    "job_status",
    "job_eta",
    "cancel_job",
    # Mixins
    "PathValidationMixin",
    "ChunkedIOMixin",
    "AuditLogMixin",
    "GitMixin",
    "CompactMixin",
    "JobControlMixin",
    # Pagination
    "DEFAULT_PAGE_LIMIT",
    "MAX_PAGE_LIMIT",
    "PageWindow",
    "PaginationMixin",
    "page_slice",
    "paginate",
    # Request correlation
    "RequestContextMiddleware",
    "get_request_id",
    "get_request_id_from_scope",
    # Resilience
    "CircuitBreaker",
    "CircuitOpenError",
    "CircuitState",
    "CircuitTransition",
    "should_trip_circuit",
]
