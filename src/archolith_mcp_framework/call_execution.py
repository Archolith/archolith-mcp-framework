"""Reusable execution and recording boundary for MCP calls."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Protocol, TypeVar, cast

T = TypeVar("T")
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class McpCallEvent:
    """One completed MCP call, suitable for a consumer-owned telemetry store."""

    kind: str
    operation: str
    started_at: str
    completed_at: str
    duration_ms: int
    success: bool
    error: str | None
    input_size: int | None
    result_size: int | None
    payload_preview: str | None


class McpCallRecorder(Protocol):
    """Consumer adapter for recording MCP call events."""

    def record(self, event: McpCallEvent) -> None:
        """Persist or emit an event without raising into the MCP request path."""


def _json_default(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def json_size(value: Any) -> int:
    """Return the serialized JSON character count of a renderable value."""

    return len(json.dumps(value, default=_json_default))


def json_preview(value: Any, *, limit: int = 500) -> str:
    """Return a bounded JSON preview suitable for telemetry."""

    rendered = json.dumps(value, default=_json_default, sort_keys=True)
    if len(rendered) <= limit:
        return rendered
    return rendered[: limit - 3] + "..."


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _record(recorder: McpCallRecorder | None, event: McpCallEvent) -> None:
    if recorder is None:
        return
    try:
        recorder.record(event)
    except Exception:
        logger.exception("Failed to record MCP %s/%s telemetry", event.kind, event.operation)


async def run_mcp_call(
    *,
    kind: str,
    operation: str,
    payload: Any,
    runner: Callable[[], Awaitable[T]],
    recorder: McpCallRecorder | None = None,
    timeout: float = 120,
    error_mapper: Callable[[str], T] | None = None,
) -> T:
    """Run one async MCP call with timeout, telemetry, and tool-safe failures.

    The framework owns only the call boundary. Consumers provide their own recorder,
    storage policy, and any domain-specific error representation through ``error_mapper``.
    """

    started_at = _utc_now_iso()
    started = time.perf_counter()
    input_size = json_size(payload) if payload is not None else None
    payload_preview = json_preview(payload) if payload is not None else None

    try:
        result = await asyncio.wait_for(runner(), timeout=timeout)
    except asyncio.TimeoutError:
        completed_at = _utc_now_iso()
        duration_ms = int((time.perf_counter() - started) * 1000)
        error_text = f"TIMEOUT: {operation} exceeded {timeout}s limit"
        logger.error("%s (duration=%dms)", error_text, duration_ms)
        _record(
            recorder,
            McpCallEvent(
                kind=kind,
                operation=operation,
                started_at=started_at,
                completed_at=completed_at,
                duration_ms=duration_ms,
                success=False,
                error=error_text,
                input_size=input_size,
                result_size=None,
                payload_preview=payload_preview,
            ),
        )
        if error_mapper is not None:
            return error_mapper(error_text)
        return cast(T, f"Error: {error_text}")
    except Exception as exc:
        completed_at = _utc_now_iso()
        duration_ms = int((time.perf_counter() - started) * 1000)
        error_text = f"{type(exc).__name__}: {exc}"
        logger.error("MCP %s/%s failed after %dms: %s", kind, operation, duration_ms, error_text)
        _record(
            recorder,
            McpCallEvent(
                kind=kind,
                operation=operation,
                started_at=started_at,
                completed_at=completed_at,
                duration_ms=duration_ms,
                success=False,
                error=error_text,
                input_size=input_size,
                result_size=None,
                payload_preview=payload_preview,
            ),
        )
        if error_mapper is not None:
            return error_mapper(error_text)
        return cast(T, f"Error: {error_text}")

    completed_at = _utc_now_iso()
    duration_ms = int((time.perf_counter() - started) * 1000)
    result_size = json_size(result) if result is not None else None
    logger.info("MCP %s/%s completed in %dms", kind, operation, duration_ms)
    _record(
        recorder,
        McpCallEvent(
            kind=kind,
            operation=operation,
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=duration_ms,
            success=True,
            error=None,
            input_size=input_size,
            result_size=result_size,
            payload_preview=payload_preview,
        ),
    )
    return result