"""Tests for JobControlMixin — opt-in polling support for OOP gateway servers."""

from __future__ import annotations

import asyncio

import pytest

from cth_mcp_framework import BaseGatewayServer, JobControlMixin, jobs


class _PollingServer(JobControlMixin, BaseGatewayServer):
    name = "test-poller"
    instructions = "Polling server."
    job_tool_prefix = "tp_"
    eta_defaults = {"build": 240, "test": 180}
    always_visible = ["tp_job_status"]

    def _register_tools(self) -> None:
        super()._register_tools()

        @self.tool()
        def do_thing(x: str) -> str:
            return f"did {x}"


class _PlainServer(BaseGatewayServer):
    name = "test-plain"
    instructions = "Non-polling server."

    def _register_tools(self) -> None:
        @self.tool()
        def quick(x: str) -> str:
            return x


@pytest.fixture(autouse=True)
def _clear_jobs():
    with jobs._lock:
        jobs._jobs.clear()
    yield
    with jobs._lock:
        jobs._jobs.clear()


def _all_tool_names(srv) -> set[str]:
    """Full untransformed tool set (the Search Transform hides most from list_tools)."""
    return {t.name for t in asyncio.run(srv.mcp._list_tools())}


class TestToolRegistration:
    def test_polling_server_registers_prefixed_job_tools(self):
        srv = _PollingServer()
        names = _all_tool_names(srv)
        assert "tp_job_status" in names
        assert "tp_job_cancel" in names
        assert "do_thing" in names  # the server's own tool still registers

    def test_job_status_pinned_in_collapsed_surface(self):
        srv = _PollingServer()
        pinned = {t.name for t in asyncio.run(srv.mcp.list_tools())}
        assert "tp_job_status" in pinned  # via always_visible
        assert {"search_tools", "call_tool"} <= pinned

    def test_plain_server_has_no_job_tools(self):
        srv = _PlainServer()
        names = _all_tool_names(srv)
        assert not any("job_status" in n or "job_cancel" in n for n in names)
        assert "quick" in names


class TestStartJobDefaults:
    def test_eta_defaults_applied(self, monkeypatch):
        captured = {}

        def _fake_start(label, fn, *args, **kwargs):
            captured["label"] = label
            captured.update(kwargs)
            return "jid123"

        monkeypatch.setattr(jobs, "start_job", _fake_start)

        srv = _PollingServer()
        jid = srv.start_job("test:build", lambda: "x", eta_bucket="build")
        assert jid == "jid123"
        assert captured["eta_bucket"] == "build"
        assert captured["eta_tool"] == "tp_build"
        assert captured["eta_default"] == 240

    def test_unknown_bucket_uses_fallback(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(jobs, "start_job", lambda label, fn, *a, **kw: captured.update(kw) or "j")
        srv = _PollingServer()
        srv.start_job("test:weird", lambda: "x", eta_bucket="weird")
        assert captured["eta_default"] == JobControlMixin.eta_default_fallback

    def test_explicit_eta_default_wins(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(jobs, "start_job", lambda label, fn, *a, **kw: captured.update(kw) or "j")
        srv = _PollingServer()
        srv.start_job("test:build", lambda: "x", eta_bucket="build", eta_default=99)
        assert captured["eta_default"] == 99

    def test_no_bucket_passes_through_clean(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(jobs, "start_job", lambda label, fn, *a, **kw: captured.update(kw) or "j")
        srv = _PollingServer()
        srv.start_job("test:plain", lambda: "x", streaming=True)
        assert "eta_bucket" not in captured
        assert captured.get("streaming") is True


class TestHelpers:
    def test_started_message_includes_eta_and_poll_tool(self):
        srv = _PollingServer()
        import time
        with jobs._lock:
            jobs._jobs["m1"] = {
                "label": "test:build", "status": "running", "output": "",
                "live_lines": [], "lines_emitted": 0, "started_at": jobs._now(),
                "started_monotonic": time.monotonic(), "completed_at": None,
                "last_progress_ts": time.monotonic(),
                "eta_tool": "tp_build", "eta_bucket": "build", "eta_default": 240,
                "timeout_s": None, "timed_out": False,
            }
        msg = srv.started_message("m1")
        assert "Job started: m1" in msg
        assert "tp_job_status" in msg
        assert "wakeup" in msg  # ETA guidance present

    def test_registered_job_status_tool_invokes_registry(self):
        srv = _PollingServer()
        tool = asyncio.run(srv.mcp.get_tool("tp_job_status"))
        result = asyncio.run(tool.run({"job_id": "does-not-exist"}))
        # ToolResult content -> text; assert the registry's "Unknown job" reply.
        text = " ".join(
            getattr(block, "text", "") for block in result.content
        )
        assert "Unknown job" in text
