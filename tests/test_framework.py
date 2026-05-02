"""Tests for cth.mcp.framework — shared MCP server conventions."""

from __future__ import annotations

import asyncio

import pytest

from cth_mcp_framework import (
    create_gateway_server,
    WorkspaceSearchTransform,
    ErrorHandlingMiddleware,
    TimingMiddleware,
    StructuredLoggingMiddleware,
)


# ---------------------------------------------------------------------------
# WorkspaceSearchTransform
# ---------------------------------------------------------------------------


class TestWorkspaceSearchTransform:
    def test_default_max_results(self):
        t = WorkspaceSearchTransform()
        assert t._max_results == 10

    def test_custom_max_results(self):
        t = WorkspaceSearchTransform(max_results=5)
        assert t._max_results == 5

    def test_always_visible(self):
        t = WorkspaceSearchTransform(always_visible=["list_things", "recall"])
        assert t._always_visible == {"list_things", "recall"}

    def test_tool_names_default(self):
        t = WorkspaceSearchTransform()
        assert t._search_tool_name == "search_tools"
        assert t._call_tool_name == "call_tool"

    def test_compact_search_serializer(self):
        from cth_mcp_framework.transforms import compact_search_serializer

        # compact_search_serializer expects Tool objects — tested indirectly
        # via the full server test below
        assert callable(compact_search_serializer)


# ---------------------------------------------------------------------------
# create_gateway_server
# ---------------------------------------------------------------------------


class TestCreateGatewayServer:
    @pytest.mark.asyncio
    async def test_creates_server_with_transform(self):
        mcp = create_gateway_server(
            "test-server",
            instructions="Test instructions.",
            always_visible=["list_things"],
        )
        assert mcp.name == "test-server"
        assert len(mcp._transforms) == 1
        assert isinstance(mcp._transforms[0], WorkspaceSearchTransform)

    @pytest.mark.asyncio
    async def test_list_tools_returns_pinned_plus_synthetic(self):
        mcp = create_gateway_server(
            "test-server",
            instructions="Test.",
            always_visible=["list_home_devices"],
        )

        @mcp.tool()
        async def list_home_devices() -> dict:
            return {"lights": []}

        @mcp.tool()
        async def set_light(light_id: str, on: bool) -> str:
            return f"Light {light_id} set."

        @mcp.tool()
        async def lock_door(lock_id: str, lock: bool) -> str:
            return f"Lock {lock_id} set."

        tools = await mcp.list_tools()
        tool_names = [t.name for t in tools]

        # Pinned tool + 2 synthetic tools
        assert "list_home_devices" in tool_names
        assert "search_tools" in tool_names
        assert "call_tool" in tool_names

        # Hidden tools should NOT appear
        assert "set_light" not in tool_names
        assert "lock_door" not in tool_names

        # Total = 1 pinned + 2 synthetic = 3
        assert len(tools) == 3

    @pytest.mark.asyncio
    async def test_default_middleware(self):
        mcp = create_gateway_server(
            "test-server",
            instructions="Test.",
        )
        # Middleware list should include our defaults
        mw = mcp.middleware
        mw_types = [type(m).__name__ for m in mw]
        assert "ErrorHandlingMiddleware" in mw_types
        assert "TimingMiddleware" in mw_types

    @pytest.mark.asyncio
    async def test_custom_middleware(self):
        custom_mw = [StructuredLoggingMiddleware()]
        mcp = create_gateway_server(
            "test-server",
            instructions="Test.",
            middlewares=custom_mw,
        )
        mw_types = [type(m).__name__ for m in mcp.middleware]
        assert "StructuredLoggingMiddleware" in mw_types
        # Defaults should NOT be present when custom is provided
        assert "ErrorHandlingMiddleware" not in mw_types

    @pytest.mark.asyncio
    async def test_lifespan_passthrough(self):
        """Verify lifespan context manager is forwarded to FastMCP."""
        from contextlib import asynccontextmanager

        startup_called = False

        @asynccontextmanager
        async def my_lifespan(app):
            nonlocal startup_called
            startup_called = True
            yield {"initialized": True}

        mcp = create_gateway_server(
            "test-server",
            instructions="Test.",
            lifespan=my_lifespan,
        )

        # The lifespan should be stored on the MCP server
        assert mcp._lifespan is my_lifespan


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class TestErrorHandlingMiddleware:
    @pytest.mark.asyncio
    async def test_value_error_caught(self):
        from fastmcp.server.middleware import MiddlewareContext
        from mcp import types as mt

        mw = ErrorHandlingMiddleware()
        # Simulate a middleware context with a ValueError in call_next
        msg = mt.CallToolRequestParams(name="bad_tool", arguments={"x": 1})
        context = MiddlewareContext(
            message=msg,
            method="tools/call",
            source="client",
            type="request",
        )

        async def failing_call_next(ctx):
            raise ValueError("bad input")

        result = await mw.on_call_tool(context, failing_call_next)
        assert len(result.content) == 1
        assert "bad input" in result.content[0].text

    @pytest.mark.asyncio
    async def test_unexpected_error_caught(self):
        from fastmcp.server.middleware import MiddlewareContext
        from mcp import types as mt

        mw = ErrorHandlingMiddleware()
        msg = mt.CallToolRequestParams(name="crash_tool", arguments={})
        context = MiddlewareContext(
            message=msg,
            method="tools/call",
            source="client",
            type="request",
        )

        async def crashing_call_next(ctx):
            raise RuntimeError("boom")

        result = await mw.on_call_tool(context, crashing_call_next)
        assert len(result.content) == 1
        assert "Internal error" in result.content[0].text
        assert "RuntimeError" in result.content[0].text
