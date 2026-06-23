"""Background job registry for long-running MCP operations.

Long-running tools (deploy, gradle build) start a background thread and return a
job ID immediately so the MCP call completes well within the client's protocol
timeout. Callers poll :func:`job_status` for progress and completion.

This is the shared workspace implementation, promoted from ``yawn.vps/vps/jobs.py``.
It adds, on top of the original registry:

- **ETA hints**: pass ``eta_tool``/``eta_bucket``/``eta_default`` to :func:`start_job`
  and the job records its real duration on success (see
  :mod:`cth_mcp_framework.duration_stats`). :func:`job_eta` and :func:`job_status`
  surface a p50/p90 estimate so the client can wait once and check once instead of
  polling tightly.
- **Heartbeat**: ``last_progress_ts`` distinguishes a genuinely stuck job (no output
  for a long time) from a merely slow one (still emitting, just past p90).
- **Timeout-kill**: pass ``timeout_s`` for a watchdog that kills the job's process
  (registered via ``proc_callback``) and marks the job ``timeout``.

Behavior for jobs without ETA params is identical to the original module, so the
VPS server can re-export these symbols unchanged.
"""

from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timezone

from cth_mcp_framework.duration_stats import (
    DurationEstimate,
    estimate_duration,
    record_duration,
)


_jobs: dict[str, dict] = {}
_lock = threading.Lock()

# Keep at most this many completed jobs in memory.
_MAX_JOBS = 50

# Max live lines kept per running job (oldest are dropped).
_MAX_LIVE_LINES = 500

# Stall threshold: if a running job has produced 0 output lines for this many
# seconds, the stall flag is set in job_status output.
_STALL_THRESHOLD_S = 600


def start_job(
    label: str,
    fn,
    *args,
    streaming: bool = False,
    eta_tool: str | None = None,
    eta_bucket: str | None = None,
    eta_default: float | None = None,
    timeout_s: int | None = None,
    **kwargs,
) -> str:
    """Run fn(*args, **kwargs) in a background daemon thread.

    streaming=True: fn must accept a ``line_callback`` keyword argument (and may
    accept ``proc_callback``). Each line of output is appended to the job's live
    buffer and shown by job_status() while the job is still running.

    ETA: when ``eta_tool`` and ``eta_bucket`` are given, the job's wall-clock
    duration is recorded on **successful** completion (not on error/timeout), and
    job_status()/job_eta() report a p50/p90 estimate using ``eta_default`` as the
    cold-start value.

    timeout_s: when set, a watchdog kills the job's registered process after the
    deadline and marks the job ``timeout`` (requires the fn to register its
    process via ``proc_callback``).

    Returns a short job_id (8 hex chars) that can be passed to job_status().
    """
    job_id = uuid.uuid4().hex[:8]
    with _lock:
        _jobs[job_id] = {
            "label": label,
            "status": "running",
            "output": "",
            "live_lines": [] if streaming else None,
            "lines_emitted": 0,
            "started_at": _now(),
            "started_monotonic": time.monotonic(),
            "completed_at": None,
            "last_progress_ts": time.monotonic(),
            "eta_tool": eta_tool,
            "eta_bucket": eta_bucket,
            "eta_default": eta_default,
            "timeout_s": timeout_s,
            "timed_out": False,
        }
        _evict_old()

    def _append_line(line: str) -> None:
        with _lock:
            j = _jobs.get(job_id)
            if j and j["live_lines"] is not None:
                j["live_lines"].append(line)
                j["lines_emitted"] += 1
                j["last_progress_ts"] = time.monotonic()
                if len(j["live_lines"]) > _MAX_LIVE_LINES:
                    j["live_lines"] = j["live_lines"][-_MAX_LIVE_LINES:]

    def _set_proc(proc) -> None:
        with _lock:
            j = _jobs.get(job_id)
            if j:
                j["_proc"] = proc

    def _worker() -> None:
        result = None
        err: str | None = None
        try:
            if streaming:
                result = fn(*args, line_callback=_append_line, proc_callback=_set_proc, **kwargs)
            else:
                result = fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            err = str(exc)
        finally:
            with _lock:
                j = _jobs.get(job_id)
                if j is not None:
                    if j.get("timed_out"):
                        j["status"] = "timeout"
                        j["output"] = result if result is not None else (err or "[TIMEOUT]")
                    elif err is not None:
                        j["status"] = "error"
                        j["output"] = err
                    else:
                        j["status"] = "done"
                        j["output"] = result
                    j["completed_at"] = _now()
                    _maybe_record(j)

    threading.Thread(target=_worker, daemon=True, name=f"job-{job_id}").start()

    if timeout_s is not None:
        threading.Thread(
            target=_watchdog, args=(job_id, timeout_s), daemon=True, name=f"job-wd-{job_id}"
        ).start()

    return job_id


def _maybe_record(job: dict) -> None:
    """Record duration for a successful, ETA-tracked job. Caller holds _lock."""
    if job.get("status") != "done":
        return
    tool = job.get("eta_tool")
    bucket = job.get("eta_bucket")
    if not tool or not bucket:
        return
    started = job.get("started_monotonic")
    if started is None:
        return
    elapsed = time.monotonic() - started
    try:
        record_duration(tool, bucket, elapsed)
    except Exception:  # noqa: BLE001 — stats must never break a job
        pass


def _watchdog(job_id: str, timeout_s: int) -> None:
    """Kill a job's process if it outlives the deadline; mark it timed out."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        with _lock:
            j = _jobs.get(job_id)
            if j is None or j["status"] != "running":
                return
        time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))

    with _lock:
        j = _jobs.get(job_id)
        if j is None or j["status"] != "running":
            return
        j["timed_out"] = True
        proc = j.get("_proc")
    if proc is not None:
        try:
            proc.kill()
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            pass


def job_eta(job_id: str) -> DurationEstimate | None:
    """Return the current duration estimate for an ETA-tracked job, else None."""
    with _lock:
        j = _jobs.get(job_id)
        if j is None or not j.get("eta_tool") or not j.get("eta_bucket"):
            return None
        tool = j["eta_tool"]
        bucket = j["eta_bucket"]
        default = j.get("eta_default") or 120
    return estimate_duration(tool, bucket, default=default)


def job_status(
    job_id: str | None = None,
    since_line: int = 0,
    status_only: bool = False,
) -> str:
    """Return a formatted status string for one job or a summary of all jobs.

    Polling-efficient: pass ``since_line`` (the ``next_line`` cursor printed by the
    previous poll) to receive only output produced since the last check instead of
    re-sending the rolling tail / full output every poll. Pass ``status_only=True``
    to skip output entirely and get just the status line + cursor.
    """
    with _lock:
        if job_id:
            j = _jobs.get(job_id)
            if j is None:
                return f"Unknown job: {job_id}"

            running = j["status"] == "running"
            emitted = j.get("lines_emitted", 0)
            start = max(0, since_line)

            if running and j.get("live_lines") is not None:
                # Rolling buffer: base index of the first line still retained.
                buf = j["live_lines"]
                base = emitted - len(buf)
                dropped = start < base
                new_lines = buf if dropped else buf[start - base:]
                next_line = emitted
            else:
                # Finished (or non-streaming): the final output is the line stream.
                out_lines = j["output"].splitlines() if j["output"] else []
                next_line = len(out_lines)
                # Cross-phase cursor (carried over from streaming) — replay once.
                dropped = False
                new_lines = out_lines if start > len(out_lines) else out_lines[start:]

            lines = [
                f"Job {job_id}: [{j['status'].upper()}] {j['label']}",
                f"Started: {j['started_at']}",
            ]
            started_dt = _parse_iso(j["started_at"])
            now_utc = datetime.now(tz=timezone.utc)
            elapsed_s = int((now_utc - started_dt).total_seconds()) if started_dt else None
            if elapsed_s is not None:
                lines.append(f"Elapsed: {elapsed_s}s")
            if j["completed_at"]:
                lines.append(f"Completed: {j['completed_at']}")
            lines.append(f"Lines: {next_line} total (next poll: since_line={next_line})")

            # ETA + heartbeat — only for ETA-tracked jobs that are still running.
            if running and j.get("eta_tool") and j.get("eta_bucket"):
                est = estimate_duration(
                    j["eta_tool"], j["eta_bucket"], default=j.get("eta_default") or 120
                )
                src = f"{est.samples} runs" if est.source == "stats" else "cold start"
                lines.append(
                    f"ETA: p50={int(round(est.p50))}s p90={int(round(est.p90))}s ({src})"
                )
                lines.append(_progress_state(j, est, elapsed_s))

            if running and emitted == 0 and elapsed_s is not None and elapsed_s > _STALL_THRESHOLD_S:
                lines.append(f"WARNING STALLED: {elapsed_s}s elapsed with 0 output lines")

            if not status_only:
                if dropped:
                    lines.append(
                        "(cursor predates the retained buffer; showing all retained lines)"
                    )
                label = "Live output" if running else "Output"
                if new_lines:
                    lines += ["", f"{label} ({len(new_lines)} new lines):"] + new_lines
                elif start >= next_line:
                    lines += ["", "(no new output since last poll)"]
            return "\n".join(lines)

        if not _jobs:
            return "No background jobs."

        rows = ["job_id   status    label"]
        rows.append("-" * 60)
        for jid, j in sorted(_jobs.items(), key=lambda kv: kv[1]["started_at"], reverse=True):
            rows.append(f"{jid}  {j['status']:8}  {j['label']}")
        return "\n".join(rows)


def _progress_state(job: dict, est: DurationEstimate, elapsed_s: int | None) -> str:
    """One-line stuck-vs-slow assessment for a running ETA-tracked job."""
    last = job.get("last_progress_ts")
    idle_s = int(time.monotonic() - last) if last is not None else None
    if idle_s is not None and idle_s > _STALL_THRESHOLD_S:
        return f"STUCK: no output for {idle_s}s — investigate (do not keep waiting)"
    if elapsed_s is not None and elapsed_s > est.p90:
        return (
            f"SLOW: {elapsed_s}s exceeds p90 {int(round(est.p90))}s but progress is recent; "
            "wait one more short window, then check"
        )
    return "ON TRACK: wait for the suggested ETA, then check once"


def cancel_job(job_id: str) -> str:
    """Kill a running background job by its job_id."""
    with _lock:
        j = _jobs.get(job_id)
        if j is None:
            return f"Unknown job: {job_id}"
        if j["status"] != "running":
            return f"Job {job_id} is not running (status: {j['status']})"
        proc = j.get("_proc")

    if proc is None:
        return f"Job {job_id} has no killable process (started before cancel support was added)"
    try:
        proc.kill()
        proc.wait(timeout=5)
    except Exception as exc:  # noqa: BLE001
        return f"Kill failed: {exc}"
    with _lock:
        j = _jobs.get(job_id)
        if j:
            j["status"] = "cancelled"
            j["completed_at"] = _now()
    return f"Job {job_id} killed."


def _now() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(s: str) -> datetime | None:
    """Parse an ISO-8601 timestamp string like '2026-06-11T10:41:23Z'."""
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError):
        return None


def _evict_old() -> None:
    """Drop oldest completed jobs when over the cap. Caller must hold _lock."""
    completed = [k for k, v in _jobs.items() if v["status"] != "running"]
    excess = len(_jobs) - _MAX_JOBS
    if excess > 0:
        completed_sorted = sorted(completed, key=lambda k: _jobs[k]["started_at"])
        for k in completed_sorted[:excess]:
            del _jobs[k]
