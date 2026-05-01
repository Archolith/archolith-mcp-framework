"""Shared middleware for cth.* MCP servers.

- ErrorHandlingMiddleware: catches exceptions from tool calls, returns structured error messages
- TimingMiddleware: logs tool call duration
- StructuredLoggingMiddleware: logs action name + payload shape + result status
"""

from __future__ import annotations

import logging
import time
from typing import Any, Sequence

from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.tools.base import ToolResult
from mcp import types as mt

logger = logging.getLogger("cth.mcp.framework")


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
