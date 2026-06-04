"""Shared middleware for cth.* MCP servers.

- ErrorHandlingMiddleware: catches exceptions from tool calls, returns structured error messages
- TimingMiddleware: logs tool call duration
- StructuredLoggingMiddleware: logs action name + payload shape + result status
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Sequence

from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.tools.base import ToolResult
from mcp import types as mt

logger = logging.getLogger("cth.mcp.framework")


class TimeoutMiddleware(Middleware):
    """Cancel tool calls that exceed a hard deadline, keeping the MCP connection alive.

    When a sync tool runs in anyio's threadpool and the underlying subprocess
    (e.g. SSH via PowerShell) hangs, the asyncio future can be cancelled via
    ``asyncio.wait_for`` even if the worker thread is still blocked — anyio
    discards the thread result and the event loop resumes immediately. This
    prevents the client from timing out the entire MCP connection (Claude Code's
    default is 180s) due to a single stuck tool call.

    Place this INSIDE ``ErrorHandlingMiddleware`` in the middleware stack so that
    any unexpected errors during timeout handling are still caught cleanly.
    """

    def __init__(self, timeout_s: float = 90.0) -> None:
        self.timeout_s = timeout_s

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: Any,
    ) -> ToolResult:
        try:
            return await asyncio.wait_for(call_next(context), timeout=self.timeout_s)
        except (asyncio.TimeoutError, TimeoutError):
            tool_name = context.message.name
            logger.error(
                "Tool %s timed out after %.0fs — returning error to preserve MCP connection",
                tool_name,
                self.timeout_s,
            )
            return ToolResult(
                content=[
                    mt.TextContent(
                        type="text",
                        text=(
                            f"Error: Tool '{tool_name}' timed out after {self.timeout_s:.0f}s. "
                            "The underlying operation may still be completing in the background."
                        ),
                    )
                ]
            )


class ErrorHandlingMiddleware(Middleware):
    """Catch unhandled exceptions from tool calls and return structured error messages.

    Prevents raw tracebacks from leaking to LLM clients while preserving
    the error message for debugging.
    """

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: Any,
    ) -> ToolResult:
        try:
            return await call_next(context)
        except ValueError as e:
            logger.warning("Tool %s validation error: %s", context.message.name, e)
            return ToolResult(content=[mt.TextContent(type="text", text=f"Error: {e}")])
        except Exception as e:
            logger.exception("Tool %s unexpected error", context.message.name)
            return ToolResult(
                content=[
                    mt.TextContent(
                        type="text",
                        text=f"Internal error in {context.message.name}: {type(e).__name__}: {e}",
                    )
                ]
            )


class TimingMiddleware(Middleware):
    """Log tool call duration in milliseconds."""

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: Any,
    ) -> ToolResult:
        start = time.perf_counter()
        try:
            result = await call_next(context)
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.info(
                "Tool %s completed in %.1f ms",
                context.message.name,
                elapsed_ms,
            )
            return result
        except Exception:
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.info(
                "Tool %s failed in %.1f ms",
                context.message.name,
                elapsed_ms,
            )
            raise


class StructuredLoggingMiddleware(Middleware):
    """Log action name, payload shape (keys only), and result status for every tool call."""

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: Any,
    ) -> ToolResult:
        args = context.message.arguments or {}
        arg_keys = list(args.keys()) if isinstance(args, dict) else ["non-dict"]

        logger.info(
            "Tool call: %s(%s)",
            context.message.name,
            ", ".join(arg_keys) if arg_keys else "no args",
        )
        result = await call_next(context)

        # Determine result status
        has_error = any(
            getattr(block, "isError", False)
            for block in result.content
            if hasattr(block, "isError")
        )
        status = "error" if has_error else "ok"

        logger.info(
            "Tool result: %s -> %s (%d content blocks)",
            context.message.name,
            status,
            len(result.content),
        )
        return result
