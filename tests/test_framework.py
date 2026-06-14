"""Tests for cth.mcp.framework — shared MCP server conventions."""

from __future__ import annotations

import asyncio
import subprocess
import tempfile
from pathlib import Path

import pytest

from cth_mcp_framework import (
    create_gateway_server,
    WorkspaceSearchTransform,
    ErrorHandlingMiddleware,
    TimingMiddleware,
    StructuredLoggingMiddleware,
    GitMixin,
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

    def test_abbreviate_visible_defaults_off(self):
        t = WorkspaceSearchTransform()
        assert t._abbreviate_visible is False
        assert t._warmup_calls == 1
        assert t._call_count == 0

    def test_abbreviate_visible_enabled(self):
        t = WorkspaceSearchTransform(abbreviate_visible=True, warmup_calls=2)
        assert t._abbreviate_visible is True
        assert t._warmup_calls == 2


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

    @pytest.mark.asyncio
    async def test_schema_abbreviated_passed_to_transform(self):
        mcp = create_gateway_server(
            "test-server",
            instructions="Test.",
            always_visible=["list_home_devices"],
            schema_abbreviated=True,
        )
        transform = mcp._transforms[0]
        assert isinstance(transform, WorkspaceSearchTransform)
        assert transform._abbreviate_visible is True

    @pytest.mark.asyncio
    async def test_abbreviate_visible_shortens_pinned_schemas(self):
        mcp = create_gateway_server(
            "test-server",
            instructions="Test.",
            always_visible=["set_light"],
            schema_abbreviated=True,
        )

        @mcp.tool()
        async def set_light(light_id: str, on: bool) -> str:
            """Set a Philips Hue light on or off. Requires the light ID."""
            return f"Light {light_id} set."

        transform = mcp._transforms[0]
        transform._call_count = 0
        tools_warmup = await mcp.list_tools()
        pinned_warmup = [t for t in tools_warmup if t.name == "set_light"][0]
        warmup_schema = pinned_warmup.to_mcp_tool().inputSchema
        assert "light_id" in warmup_schema.get("properties", {})

        # Second call = abbreviated
        tools_abbrev = await mcp.list_tools()
        pinned_abbrev = [t for t in tools_abbrev if t.name == "set_light"][0]
        abbrev_schema = pinned_abbrev.to_mcp_tool().inputSchema
        assert abbrev_schema.get("properties", {}) == {}
        assert len(pinned_abbrev.description) < len(pinned_warmup.description)


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


class _GitMixinHarness(GitMixin):
    pass


class TestGitMixin:
    def test_git_auto_commit_scopes_commit_to_requested_paths(self, monkeypatch: pytest.MonkeyPatch):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            subprocess.run(["git", "init"], cwd=repo_root, check=True, capture_output=True, text=True)
            subprocess.run(
                ["git", "config", "user.email", "test@example.com"],
                cwd=repo_root,
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test User"],
                cwd=repo_root,
                check=True,
                capture_output=True,
                text=True,
            )

            baseline = repo_root / "baseline.txt"
            baseline.write_text("baseline\n", encoding="utf-8")
            subprocess.run(["git", "add", "--", "baseline.txt"], cwd=repo_root, check=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo_root, check=True, capture_output=True, text=True)

            unrelated = repo_root / "unrelated.txt"
            unrelated.write_text("unrelated\n", encoding="utf-8")
            subprocess.run(["git", "add", "--", "unrelated.txt"], cwd=repo_root, check=True)

            artifact = repo_root / "artifact.txt"
            artifact.write_text("artifact\n", encoding="utf-8")

            monkeypatch.setenv("GIT_AUTO_COMMIT", "1")
            monkeypatch.setenv("GIT_REPO_ROOT", str(repo_root))

            mixin = _GitMixinHarness()
            result = mixin.git_auto_commit(artifact, "write", "plans/demo.md")

            assert result["committed"] is True

            head_files = subprocess.run(
                ["git", "show", "--name-only", "--pretty=format:", "HEAD"],
                cwd=repo_root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.splitlines()
            committed_files = {line.strip() for line in head_files if line.strip()}
            assert committed_files == {"artifact.txt"}

            status_output = subprocess.run(
                ["git", "status", "--short"],
                cwd=repo_root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            assert "A  unrelated.txt" in status_output
