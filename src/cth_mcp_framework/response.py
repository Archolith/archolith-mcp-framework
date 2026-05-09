"""ToolResponse — standard response contract for cth.* MCP gateway servers.

All tool returns should pass through ToolResponse so LLM clients always see
the same shape: {success: bool, ...data, error?: str, code?: str}.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ── Standard error codes ──────────────────────────────────────────────────

ERR_NOT_FOUND = "NOT_FOUND"
ERR_INVALID_INPUT = "INVALID_INPUT"
ERR_PERMISSION = "PERMISSION_DENIED"
ERR_TOO_LARGE = "PAYLOAD_TOO_LARGE"
ERR_TIMEOUT = "TIMEOUT"
ERR_INTERNAL = "INTERNAL_ERROR"


# ── ToolResponse dataclass ───────────────────────────────────────────────

@dataclass
class ToolResponse:
    """Standard tool response for cth.* MCP servers.

    Usage::

        return ToolResponse.ok(filename="plan.md", size=1024).to_dict()
        return ToolResponse.err("File not found", ERR_NOT_FOUND).to_dict()
    """

    success: bool
    data: dict[str, Any] | None = field(default=None)
    error: str | None = field(default=None)
    code: str | None = field(default=None)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a flat dict suitable for FastMCP tool returns."""
        out: dict[str, Any] = {"success": self.success}
        if self.data:
            out.update(self.data)
        if self.error:
            out["error"] = self.error
        if self.code:
            out["code"] = self.code
        return out

    @classmethod
    def ok(cls, **data: Any) -> ToolResponse:
        """Create a successful response with arbitrary key-value data."""
        return cls(success=True, data=data)

    @classmethod
    def err(cls, error: str, code: str | None = None) -> ToolResponse:
        """Create an error response with an optional machine-readable code."""
        return cls(success=False, error=error, code=code)
