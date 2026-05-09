"""AuditLogMixin — JSON-line audit logging for cth.* MCP servers.

Extracted from artifact_gateway.py. Provides:
- log_write(): appends a JSON line to the audit log
- Reads AUDIT_LOG_PATH env var; silently skips if unset
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


class AuditLogMixin:
    """Mixin for audit logging in cth.* MCP servers.

    Compose with BaseGatewayServer via multiple inheritance::

        class MyServer(BaseGatewayServer, AuditLogMixin):
            ...

    Set the AUDIT_LOG_PATH environment variable to enable logging.
    If unset, log_write() is a no-op.
    """

    _audit_log_path: str | None = None

    def _get_audit_log_path(self) -> str | None:
        """Return the audit log path from env, caching on first call."""
        if self._audit_log_path is None:
            self._audit_log_path = os.environ.get("AUDIT_LOG_PATH")
        return self._audit_log_path

    def log_write(self, label: str, byte_count: int) -> None:
        """Log a write operation as a JSON line to the audit log.

        Each call appends one JSON object on its own line::

            {"timestamp": "2026-05-08T12:00:00Z", "label": "wrapup", "byte_count": 1024}

        Silently skips if AUDIT_LOG_PATH is not set. Silently skips
        on I/O errors (logs a warning instead of crashing).

        Args:
            label: A short label identifying what was written (e.g. artifact type).
            byte_count: Number of bytes written.
        """
        log_path = self._get_audit_log_path()
        if not log_path:
            return

        try:
            timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            log_entry = {
                "timestamp": timestamp,
                "label": label,
                "byte_count": byte_count,
            }
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(log_entry) + "\n")
            logger.info("Audit: wrote %d bytes for %s", byte_count, label)
        except Exception as e:
            logger.warning("Failed to write audit log: %s", e)
