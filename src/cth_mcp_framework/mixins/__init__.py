"""Mixins for cth.* MCP gateway servers.

Pick-and-mix classes for recurring patterns: path validation, chunked I/O,
audit logging. Compose with BaseGatewayServer via multiple inheritance.
"""

from cth_mcp_framework.mixins.paths import PathValidationMixin
from cth_mcp_framework.mixins.chunked_io import ChunkedIOMixin
from cth_mcp_framework.mixins.audit import AuditLogMixin

__all__ = [
    "PathValidationMixin",
    "ChunkedIOMixin",
    "AuditLogMixin",
]
