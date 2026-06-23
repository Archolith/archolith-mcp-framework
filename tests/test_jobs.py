"""Tests for cth_mcp_framework.jobs (shared background job registry)."""

from __future__ import annotations

import time

import pytest

from cth_mcp_framework import jobs


@pytest.fixture(autouse=True)
def _clear_jobs():
    with jobs._lock:
        jobs._jobs.clear()
    yield
    with jobs._lock:
        jobs._jobs.clear()


def _wait_terminal(job_id: str, timeout: float = 5.0) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with jobs._lock:
            st = jobs._jobs[job_id]["status"]
        if st != "running":
            return st
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not finish within {timeout}s")


# ---------------------------------------------------------------------------
# Backward-compatible behavior (no ETA params)
# ---------------------------------------------------------------------------

class TestBasicLifecycle:
    def test_success_marks_done(self):
        jid = jobs.start_job("noop", lambda: "result-text")
        assert _wait_terminal(jid) == "done"
        out = jobs.job_status(jid)
        assert "[DONE]" in out
        assert "result-text" in out

    def test_error_marks_error(self):
        def boom():
            raise RuntimeError("kaboom")

        jid = jobs.start_job("boom", boom)
        assert _wait_terminal(jid) == "error"
        out = jobs.job_status(jid)
        assert "[ERROR]" in out
        assert "kaboom" in out

    def test_unknown_job(self):
        assert "Unknown job" in jobs.job_status("nope")

    def test_status_only_omits_output(self):
        jid = jobs.start_job("noop", lambda: "secret-output")
        _wait_terminal(jid)
        out = jobs.job_status(jid, status_only=True)
        assert "secret-output" not in out


# ---------------------------------------------------------------------------
# ETA integration
# ---------------------------------------------------------------------------

class TestEta:
    def test_success_records_duration(self, monkeypatch):
        recorded = []
        monkeypatch.setattr(jobs, "record_duration", lambda t, b, s: recorded.append((t, b, s)))

        jid = jobs.start_job("deploy:rip", lambda: "ok", eta_tool="vps_deploy", eta_bucket="rip", eta_default=120)
        assert _wait_terminal(jid) == "done"
        # give the finally-block a beat
        time.sleep(0.05)
        assert len(recorded) == 1
        assert recorded[0][0] == "vps_deploy"
        assert recorded[0][1] == "rip"
        assert recorded[0][2] >= 0

    def test_failure_does_not_record(self, monkeypatch):
        recorded = []
        monkeypatch.setattr(jobs, "record_duration", lambda t, b, s: recorded.append((t, b, s)))

        def boom():
            raise RuntimeError("x")

        jid = jobs.start_job("deploy:rip", boom, eta_tool="vps_deploy", eta_bucket="rip", eta_default=120)
        assert _wait_terminal(jid) == "error"
        time.sleep(0.05)
        assert recorded == []

    def test_no_eta_params_does_not_record(self, monkeypatch):
        recorded = []
        monkeypatch.setattr(jobs, "record_duration", lambda t, b, s: recorded.append((t, b, s)))
        jid = jobs.start_job("plain", lambda: "ok")
        _wait_terminal(jid)
        time.sleep(0.05)
        assert recorded == []

    def test_job_eta_returns_estimate(self, monkeypatch):
        monkeypatch.setattr(jobs, "record_duration", lambda *a, **k: None)
        # Seed a running job with eta params directly.
        with jobs._lock:
            jobs._jobs["e1"] = {
                "label": "deploy:rip", "status": "running", "output": "",
                "live_lines": [], "lines_emitted": 0, "started_at": jobs._now(),
                "started_monotonic": time.monotonic(), "completed_at": None,
                "last_progress_ts": time.monotonic(),
                "eta_tool": "vps_deploy", "eta_bucket": "rip", "eta_default": 120,
                "timeout_s": None, "timed_out": False,
            }
        est = jobs.job_eta("e1")
        assert est is not None
        assert est.p50 > 0

    def test_job_eta_none_without_params(self):
        with jobs._lock:
            jobs._jobs["e2"] = {
                "label": "x", "status": "running", "output": "", "live_lines": None,
                "lines_emitted": 0, "started_at": jobs._now(), "completed_at": None,
                "eta_tool": None, "eta_bucket": None,
            }
        assert jobs.job_eta("e2") is None

    def test_status_shows_eta_for_running_tracked_job(self):
        with jobs._lock:
            jobs._jobs["e3"] = {
                "label": "deploy:rip", "status": "running", "output": "",
                "live_lines": [], "lines_emitted": 0, "started_at": jobs._now(),
                "started_monotonic": time.monotonic(), "completed_at": None,
                "last_progress_ts": time.monotonic(),
                "eta_tool": "vps_deploy", "eta_bucket": "rip", "eta_default": 120,
                "timeout_s": None, "timed_out": False,
            }
        out = jobs.job_status("e3", status_only=True)
        assert "ETA:" in out
        assert "ON TRACK" in out


# ---------------------------------------------------------------------------
# Compatibility: hand-built dicts without new keys (mirrors vps tests)
# ---------------------------------------------------------------------------

class TestLegacyDictCompat:
    def test_streaming_cursor_without_new_keys(self):
        jobs._jobs["p-first"] = {
            "label": "deploy", "status": "running", "output": "",
            "live_lines": ["log0", "log1", "log2"], "lines_emitted": 3,
            "started_at": "t0", "completed_at": None,
        }
        out = jobs.job_status("p-first")
        assert "log0" in out and "log2" in out
        assert "since_line=3" in out
        # No ETA line for non-tracked jobs.
        assert "ETA:" not in out

    def test_done_dict_slices(self):
        jobs._jobs["p-done"] = {
            "label": "pull", "status": "done", "output": "o0\no1\no2\no3",
            "live_lines": None, "lines_emitted": 0,
            "started_at": "t0", "completed_at": "t1",
        }
        out = jobs.job_status("p-done", since_line=4)
        assert "no new output" in out


# ---------------------------------------------------------------------------
# Timeout watchdog
# ---------------------------------------------------------------------------

class TestTimeout:
    def test_timeout_kills_and_marks(self, monkeypatch):
        recorded = []
        monkeypatch.setattr(jobs, "record_duration", lambda *a, **k: recorded.append(a))

        class _FakeProc:
            def __init__(self):
                self.killed = False

            def kill(self):
                self.killed = True

            def wait(self, timeout=None):
                pass

        proc = _FakeProc()

        def streaming_fn(line_callback, proc_callback):
            proc_callback(proc)
            # Simulate a process that runs longer than the timeout.
            for _ in range(50):
                time.sleep(0.05)
                if proc.killed:
                    break
            return "partial"

        jid = jobs.start_job("slow", streaming_fn, streaming=True, timeout_s=1)
        st = _wait_terminal(jid, timeout=5)
        assert st == "timeout"
        assert proc.killed is True
        time.sleep(0.05)
        # Timed-out runs must not be recorded even without eta params anyway.
        assert recorded == []
