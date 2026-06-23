"""Tests for cth_mcp_framework.duration_stats."""

from __future__ import annotations

from pathlib import Path

import pytest

from cth_mcp_framework import duration_stats as ds
from cth_mcp_framework.duration_stats import (
    DurationEstimate,
    estimate_duration,
    record_duration,
)


@pytest.fixture(autouse=True)
def _isolate_cache():
    """Each test starts with a clean in-memory cache."""
    ds.reset_cache()
    yield
    ds.reset_cache()


@pytest.fixture
def stats_path(tmp_path: Path) -> Path:
    return tmp_path / "logs" / "mcp-duration-stats.json"


# ---------------------------------------------------------------------------
# Percentile math
# ---------------------------------------------------------------------------

class TestPercentile:
    def test_empty(self):
        assert ds._percentile([], 50) == 0.0

    def test_single(self):
        assert ds._percentile([42.0], 50) == 42.0
        assert ds._percentile([42.0], 90) == 42.0

    def test_median_odd(self):
        assert ds._percentile([1.0, 2.0, 3.0], 50) == 2.0

    def test_median_even_interpolates(self):
        assert ds._percentile([1.0, 2.0, 3.0, 4.0], 50) == 2.5

    def test_p90(self):
        vals = [float(i) for i in range(1, 11)]  # 1..10
        # rank = 0.9 * 9 = 8.1 -> between index 8 (=9.0) and 9 (=10.0)
        assert ds._percentile(vals, 90) == pytest.approx(9.1)


# ---------------------------------------------------------------------------
# Cold start
# ---------------------------------------------------------------------------

class TestColdStart:
    def test_no_samples_returns_default(self, stats_path: Path):
        est = estimate_duration("gradle_build", "build", default=240, path=stats_path)
        assert est.source == "default"
        assert est.samples == 0
        assert est.p50 == 240
        assert est.p90 == 240

    def test_below_min_samples_uses_default(self, stats_path: Path):
        record_duration("gradle_build", "build", 100, path=stats_path)
        record_duration("gradle_build", "build", 110, path=stats_path)  # only 2 < MIN_SAMPLES
        est = estimate_duration("gradle_build", "build", default=240, path=stats_path)
        assert est.source == "default"
        assert est.samples == 2
        assert est.p50 == 240

    def test_suggested_first_check_floored(self, stats_path: Path):
        est = estimate_duration("gradle_compile", "compileJava", default=5, path=stats_path)
        # default below floor -> still floored to MIN_FIRST_CHECK_S
        assert est.suggested_first_check_s == ds.MIN_FIRST_CHECK_S


# ---------------------------------------------------------------------------
# Stats-backed estimate
# ---------------------------------------------------------------------------

class TestStatsBacked:
    def test_estimate_from_recorded(self, stats_path: Path):
        for v in (100, 200, 300, 400, 500):
            record_duration("vps_deploy", "rip", v, path=stats_path)
        est = estimate_duration("vps_deploy", "rip", default=120, path=stats_path)
        assert est.source == "stats"
        assert est.samples == 5
        assert est.p50 == 300
        assert est.p90 == pytest.approx(460.0)

    def test_buckets_isolated(self, stats_path: Path):
        for v in (10, 20, 30):
            record_duration("vps_deploy", "rip", v, path=stats_path)
        for v in (1000, 2000, 3000):
            record_duration("vps_deploy", "market", v, path=stats_path)
        rip = estimate_duration("vps_deploy", "rip", default=120, path=stats_path)
        market = estimate_duration("vps_deploy", "market", default=120, path=stats_path)
        assert rip.p50 == 20
        assert market.p50 == 2000

    def test_window_caps_at_WINDOW(self, stats_path: Path):
        for v in range(100):
            record_duration("gradle_test", "test", float(v), path=stats_path)
        est = estimate_duration("gradle_test", "test", default=180, path=stats_path)
        assert est.samples == ds.WINDOW
        # Only the last WINDOW (80..99) are retained.
        assert est.p50 == pytest.approx(89.5)

    def test_negative_duration_ignored(self, stats_path: Path):
        record_duration("vps_deploy", "rip", -5, path=stats_path)
        est = estimate_duration("vps_deploy", "rip", default=120, path=stats_path)
        assert est.samples == 0


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

class TestPersistence:
    def test_round_trips_through_disk(self, stats_path: Path):
        for v in (100, 200, 300):
            record_duration("vps_deploy", "rip", v, path=stats_path)
        assert stats_path.exists()
        ds.reset_cache()  # force reload from disk
        est = estimate_duration("vps_deploy", "rip", default=120, path=stats_path)
        assert est.samples == 3
        assert est.p50 == 200

    def test_corrupt_file_starts_empty(self, stats_path: Path):
        stats_path.parent.mkdir(parents=True, exist_ok=True)
        stats_path.write_text("{ not valid json", encoding="utf-8")
        ds.reset_cache()
        est = estimate_duration("vps_deploy", "rip", default=120, path=stats_path)
        assert est.source == "default"
        assert est.samples == 0

    def test_missing_file_starts_empty(self, stats_path: Path):
        est = estimate_duration("vps_deploy", "rip", default=120, path=stats_path)
        assert est.samples == 0
        assert not stats_path.exists()


# ---------------------------------------------------------------------------
# Guidance / DurationEstimate
# ---------------------------------------------------------------------------

class TestGuidance:
    def test_default_guidance_mentions_no_history(self):
        est = DurationEstimate(p50=240, p90=240, samples=0, source="default")
        assert "No history" in est.guidance()
        assert est.suggested_first_check_s == 240

    def test_stats_guidance_mentions_runs(self):
        est = DurationEstimate(p50=210, p90=300, samples=18, source="stats")
        g = est.guidance()
        assert "18 runs" in g
        assert "210s" in g
