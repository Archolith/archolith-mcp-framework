"""Mixins for MCP gateway servers.

Pick-and-mix classes for recurring patterns: path validation, chunked I/O,
audit logging, git auto-commit, compact mode. Compose with BaseGatewayServer
via multiple inheritance.
"""

from archolith_mcp_framework.mixins.paths import PathValidationMixin
from archolith_mcp_framework.mixins.chunked_io import ChunkedIOMixin
from archolith_mcp_framework.mixins.audit import AuditLogMixin
from archolith_mcp_framework.mixins.git import GitMixin
from archolith_mcp_framework.mixins.compact import CompactMixin
from archolith_mcp_framework.mixins.job_control import JobControlMixin

__all__ = [
    "PathValidationMixin",
    "ChunkedIOMixin",
    "AuditLogMixin",
    "GitMixin",
    "CompactMixin",
    "JobControlMixin",
]
