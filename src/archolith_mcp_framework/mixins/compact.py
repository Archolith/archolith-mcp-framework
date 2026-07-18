"""CompactMixin — shared compact-mode support for MCP gateway servers.

Provides:
- resolve_compact(_compact): tri-state resolution (per-call > session > env > False)
- set_response_mode(mode): in-memory session default
- strip_compact(kwargs): removes _compact and fields before forwarding downstream

Compose with BaseGatewayServer via multiple inheritance. Subclasses MUST set
_compact_env_prefix (e.g. "WORKSPACE_ARTIFACTS") to enable env var resolution.
If not set, env var resolution is skipped (session-only mode).

Convention: See CONVENTIONS.md "Compact-Mode Calling Convention" for the full
rule set. This mixin implements Rules 1, 3, 5, 7, 9.
"""

from __future__ import annotations

import os
from typing import Any

from archolith_mcp_framework.response import ToolResponse, ERR_INVALID_INPUT


class CompactMixin:
    """Mixin for MCP servers that support _compact response mode.

    Usage::

        class MyGateway(BaseGatewayServer, CompactMixin, ...):
            _compact_env_prefix = "WORKSPACE_ARTIFACTS"

        # In tool functions:
        if _gateway.resolve_compact(_compact):
            return compact_response
        return full_response
    """

    # Server subclass sets this (e.g. "WORKSPACE_ARTIFACTS", "ARCHOLITH_HARNESS", "YAWN_VPS").
    # When empty, env var resolution is skipped.
    _compact_env_prefix: str = ""

    # In-memory session default. Changed via set_response_mode().
    # _session_mode_set tracks whether set_response_mode was called —
    # if so, session mode takes priority over env var.
    _response_mode: str = "verbose"
    _session_mode_set: bool = False

    def resolve_compact(self, _compact: bool | None = None) -> bool:
        """Resolve tri-state _compact to final bool.

        Resolution chain (first match wins):
        1. Explicit per-call _compact (True or False)
        2. In-memory session mode (if set via set_response_mode)
        3. <PREFIX>_RESPONSE_MODE env var == "compact"
        4. Default False (verbose)

        Args:
            _compact: True (compact), False (verbose), or None (defer to
                session/env default).

        Returns:
            True if compact mode should be used, False for verbose.
        """
        if _compact is not None:
            return _compact
        # If session mode was explicitly set via set_response_mode(), it wins.
        # Also check if _response_mode was set to something other than default
        # (supports direct attribute assignment in tests/config).
        if self._session_mode_set or self._response_mode != "verbose":
            return self._response_mode == "compact"
        if self._compact_env_prefix:
            env_val = os.environ.get(
                f"{self._compact_env_prefix}_RESPONSE_MODE", ""
            )
            if env_val == "compact":
                return True
        return False

    def set_response_mode(self, mode: str) -> dict[str, Any]:
        """Set the in-memory default response mode for this server instance.

        Args:
            mode: "verbose" or "compact"

        Returns:
            ToolResponse dict confirming the new mode.
        """
        if mode not in ("verbose", "compact"):
            return ToolResponse.err(
                "mode must be 'verbose' or 'compact', got %r" % mode,
                ERR_INVALID_INPUT,
            ).to_dict()
        previous = self._response_mode
        self._response_mode = mode
        self._session_mode_set = True
        return ToolResponse.ok(
            mode=mode,
            previous_mode=previous,
        ).to_dict()

    @staticmethod
    def strip_compact(kwargs: dict) -> dict:
        """Remove _compact and fields from kwargs before forwarding downstream.

        These are meta-parameters about response format, not domain arguments.
        Downstream services (HTTP proxies, subprocesses, etc.) should never
        receive them.

        Args:
            kwargs: Dict that may contain _compact and/or fields keys.

        Returns:
            New dict with _compact and fields removed.
        """
        return {k: v for k, v in kwargs.items() if k not in ("_compact", "fields")}
