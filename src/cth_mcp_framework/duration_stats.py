"""Duration statistics for async MCP jobs.

Long-running tools (deploy, gradle build) run as async start + poll. A poll is a
full model turn, so a multi-minute job that is polled every few seconds burns
dozens of turns. The fix is to make the job *self-describing*: record how long
each kind of job actually takes, then tell the client how long to wait before its
first (ideally only) status check.

This module records terminal-state durations per ``tool + bucket`` and returns a
:class:`DurationEstimate` with ``p50`` (suggested first-check delay) and ``p90``
(the "now it's worth worrying" threshold). Buckets are deliberately
low-cardinality discriminators of runtime (gradle task name, deploy target) so
the percentiles mean something.

Storage is a small JSON file under ``<WORKSPACE_ROOT>/logs``; missing or corrupt
files start empty (cold-start defaults cover the gap). All access is guarded by a
module-level lock — each MCP server is a single stdio process.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

# Rolling window: keep at most this many recent durations per bucket.
WINDOW = 20

# Below this many samples, ignore recorded stats and use the cold-start default.
MIN_SAMPLES = 3

# Floor for the suggested first-check delay (seconds). Never advise an absurdly
# short wait even if a bucket's p50 is tiny — a status check still costs a turn.
MIN_FIRST_CHECK_S = 30

_lock = threading.RLock()

# In-memory cache of {"<tool>:<bucket>": [durations...]}. Loaded lazily.
_cache: dict[str, list[float]] | None = None


# ---------------------------------------------------------------------------
# Storage path resolution
# ---------------------------------------------------------------------------

def _default_stats_path() -> Path:
    """Resolve the stats file path from WORKSPACE_ROOT (or a sane fallback)."""
    root = os.getenv("WORKSPACE_ROOT")
    if root:
        base = Path(root)
    else:
        # framework lives at projects/ctharvey/cth.mcp.framework/src/... — walk up
        # to the workspace root. Fall back to cwd if the layout is unexpected.
        here = Path(__file__).resolve()
        base = here.parents[5] if len(here.parents) >= 6 else Path.cwd()
    return base / "logs" / "mcp-duration-stats.json"


# Resolved once at import; tests override via the ``path`` argument on helpers.
STATS_PATH = _default_stats_path()


# ---------------------------------------------------------------------------
# Estimate dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DurationEstimate:
    """Estimated runtime for a job bucket.

    Attributes:
        p50: Median runtime in seconds. Use as the suggested first-check delay.
        p90: 90th-percentile runtime in seconds. Past this, a running job is
            worth worrying about even though it may still be a slow run.
        samples: Number of recorded durations backing the estimate.
        source: ``"stats"`` when derived from recorded samples, ``"default"``
            when ``samples < MIN_SAMPLES`` and the cold-start default was used.
    """

    p50: float
    p90: float
    samples: int
    source: str

    @property
    def suggested_first_check_s(self) -> int:
        """Integer seconds to wait before the first status check (floored)."""
        return max(MIN_FIRST_CHECK_S, int(round(self.p50)))

    def guidance(self) -> str:
        """One-line, LLM-facing instruction on how to wait."""
        first = self.suggested_first_check_s
        worry = int(round(self.p90))
        if self.source == "default":
            return (
                f"No history yet; expect ~{first}s. Schedule one wakeup in ~{first}s, "
                f"then check status once. Treat as stuck only well past ~{worry}s."
            )
        return (
            f"Typically ~{first}s (p50 over {self.samples} runs). Schedule one wakeup "
            f"in ~{first}s, then check status once. Past ~{worry}s (p90) it's worth a "
            f"closer look; do not poll in between."
        )


# ---------------------------------------------------------------------------
# Percentile math (pure)
# ---------------------------------------------------------------------------

def _percentile(sorted_values: list[float], pct: float) -> float:
    """Linear-interpolation percentile over an already-sorted list.

    ``pct`` is in [0, 100]. Returns 0.0 for an empty list.
    """
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    rank = (pct / 100.0) * (len(sorted_values) - 1)
    low = int(rank)
    high = min(low + 1, len(sorted_values) - 1)
    frac = rank - low
    return float(sorted_values[low] + (sorted_values[high] - sorted_values[low]) * frac)


def _estimate_from_window(window: list[float], *, default: float) -> DurationEstimate:
    """Build an estimate from a duration window, falling back to ``default``."""
    samples = len(window)
    if samples < MIN_SAMPLES:
        return DurationEstimate(p50=float(default), p90=float(default), samples=samples, source="default")
    ordered = sorted(window)
    return DurationEstimate(
        p50=_percentile(ordered, 50),
        p90=_percentile(ordered, 90),
        samples=samples,
        source="stats",
    )


# ---------------------------------------------------------------------------
# Disk I/O (isolated, path-injectable for tests)
# ---------------------------------------------------------------------------

def _load(path: Path) -> dict[str, list[float]]:
    """Load the stats document. Missing or corrupt file -> empty dict."""
    try:
        raw = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    result: dict[str, list[float]] = {}
    for key, vals in data.items():
        if isinstance(key, str) and isinstance(vals, list):
            cleaned = [float(v) for v in vals if isinstance(v, (int, float))]
            if cleaned:
                result[key] = cleaned[-WINDOW:]
    return result


def _save(path: Path, data: dict[str, list[float]]) -> None:
    """Atomically write the stats document."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".dur-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _key(tool: str, bucket: str) -> str:
    return f"{tool}:{bucket}"


def _get_cache(path: Path) -> dict[str, list[float]]:
    global _cache
    if _cache is None:
        _cache = _load(path)
    return _cache


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def record_duration(tool: str, bucket: str, seconds: float, *, path: Path | None = None) -> None:
    """Record a completed job's duration into the rolling window for its bucket.

    Only successful runs should be recorded — failures and timeouts skew the
    median toward unrepresentative values. Callers enforce that policy.

    Args:
        tool: Logical tool name (e.g. ``"vps_deploy"``, ``"gradle_build"``).
        bucket: Low-cardinality runtime discriminator (deploy target, task name).
        seconds: Wall-clock duration of the run.
        path: Override the stats file (tests). Defaults to :data:`STATS_PATH`.
    """
    if seconds < 0:
        return
    target = path or STATS_PATH
    with _lock:
        data = dict(_get_cache(target))
        key = _key(tool, bucket)
        window = list(data.get(key, []))
        window.append(float(seconds))
        data[key] = window[-WINDOW:]
        _save(target, data)
        global _cache
        _cache = data


def estimate_duration(
    tool: str,
    bucket: str,
    *,
    default: float,
    path: Path | None = None,
) -> DurationEstimate:
    """Estimate runtime for a job bucket.

    Returns a stats-backed estimate when at least :data:`MIN_SAMPLES` durations
    are recorded, otherwise a default-backed estimate (``source="default"``).

    Args:
        tool: Logical tool name.
        bucket: Low-cardinality runtime discriminator.
        default: Cold-start expected duration in seconds for this tool.
        path: Override the stats file (tests). Defaults to :data:`STATS_PATH`.
    """
    target = path or STATS_PATH
    with _lock:
        data = _get_cache(target)
        window = list(data.get(_key(tool, bucket), []))
    return _estimate_from_window(window, default=default)


def reset_cache() -> None:
    """Drop the in-memory cache (tests). Next access reloads from disk."""
    global _cache
    with _lock:
        _cache = None
