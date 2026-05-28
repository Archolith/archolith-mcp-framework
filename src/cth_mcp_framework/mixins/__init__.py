"""Mixins for cth.* MCP gateway servers.

Pick-and-mix classes for recurring patterns: path validation, chunked I/O,
audit logging, git auto-commit, compact mode. Compose with BaseGatewayServer
via multiple inheritance.
"""

from cth_mcp_framework.mixins.paths import PathValidationMixin
from cth_mcp_framework.mixins.chunked_io import ChunkedIOMixin
from cth_mcp_framework.mixins.audit import AuditLogMixin
from cth_mcp_framework.mixins.git import GitMixin
from cth_mcp_framework.mixins.compact import CompactMixin

__all__ = [
    "PathValidationMixin",
    "ChunkedIOMixin",
    "AuditLogMixin",
    "GitMixin",
    "CompactMixin",
]
