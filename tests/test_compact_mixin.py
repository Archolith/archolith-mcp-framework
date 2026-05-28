"""Tests for CompactMixin — shared compact-mode support for cth.* MCP servers."""

from __future__ import annotations

import os

import pytest

from cth_mcp_framework import CompactMixin


class _TestGateway(CompactMixin):
    """Concrete subclass for testing with a known env prefix."""
    _compact_env_prefix = "TEST_SERVER"


class _NoPrefixGateway(CompactMixin):
    """Subclass with no env prefix set (session-only mode)."""
    _compact_env_prefix = ""


class TestResolveCompactExplicit:
    """Explicit per-call _compact always wins."""

    def test_explicit_true(self):
        gw = _TestGateway()
        gw._response_mode = "compact"
        os.environ["TEST_SERVER_RESPONSE_MODE"] = "compact"
        try:
            # Explicit True should return True regardless of session/env state
            assert gw.resolve_compact(True) is True
        finally:
            os.environ.pop("TEST_SERVER_RESPONSE_MODE", None)

    def test_explicit_false_overrides_session_compact(self):
        gw = _TestGateway()
        gw._response_mode = "compact"
        # Explicit False should return False even when session is compact
        assert gw.resolve_compact(False) is False

    def test_explicit_false_overrides_env_compact(self):
        gw = _TestGateway()
        os.environ["TEST_SERVER_RESPONSE_MODE"] = "compact"
        try:
            assert gw.resolve_compact(False) is False
        finally:
            os.environ.pop("TEST_SERVER_RESPONSE_MODE", None)


class TestResolveCompactSessionMode:
    """Session mode kicks in when _compact is None."""

    def test_session_compact(self):
        gw = _TestGateway()
        gw._response_mode = "compact"
        assert gw.resolve_compact(None) is True

    def test_session_verbose(self):
        gw = _TestGateway()
        gw._response_mode = "verbose"
        assert gw.resolve_compact(None) is False

    def test_session_compact_beats_env_verbose(self):
        """Session mode takes priority over env var."""
        gw = _TestGateway()
        gw._response_mode = "compact"
        os.environ["TEST_SERVER_RESPONSE_MODE"] = "verbose"
        try:
            assert gw.resolve_compact(None) is True
        finally:
            os.environ.pop("TEST_SERVER_RESPONSE_MODE", None)

    def test_session_verbose_beats_env_compact(self):
        """Session mode (set via set_response_mode) takes priority over env var."""
        gw = _TestGateway()
        gw.set_response_mode("verbose")  # explicitly set, not just default
        os.environ["TEST_SERVER_RESPONSE_MODE"] = "compact"
        try:
            assert gw.resolve_compact(None) is False
        finally:
            os.environ.pop("TEST_SERVER_RESPONSE_MODE", None)


class TestResolveCompactEnvVar:
    """Env var kicks in when _compact is None and session mode is verbose."""

    def test_env_compact(self):
        gw = _TestGateway()
        gw._response_mode = "verbose"
        os.environ["TEST_SERVER_RESPONSE_MODE"] = "compact"
        try:
            assert gw.resolve_compact(None) is True
        finally:
            os.environ.pop("TEST_SERVER_RESPONSE_MODE", None)

    def test_env_verbose(self):
        gw = _TestGateway()
        gw._response_mode = "verbose"
        os.environ["TEST_SERVER_RESPONSE_MODE"] = "verbose"
        try:
            assert gw.resolve_compact(None) is False
        finally:
            os.environ.pop("TEST_SERVER_RESPONSE_MODE", None)

    def test_env_not_set(self):
        gw = _TestGateway()
        gw._response_mode = "verbose"
        os.environ.pop("TEST_SERVER_RESPONSE_MODE", None)
        assert gw.resolve_compact(None) is False

    def test_env_unrecognized_value(self):
        """Unrecognized env var value is treated as verbose."""
        gw = _TestGateway()
        gw._response_mode = "verbose"
        os.environ["TEST_SERVER_RESPONSE_MODE"] = "terse"
        try:
            assert gw.resolve_compact(None) is False
        finally:
            os.environ.pop("TEST_SERVER_RESPONSE_MODE", None)


class TestResolveCompactNoPrefix:
    """When no env prefix is set, env var resolution is skipped."""

    def test_no_prefix_skips_env(self):
        gw = _NoPrefixGateway()
        gw._response_mode = "verbose"
        # Even though env var is set, it shouldn't be checked
        os.environ["_RESPONSE_MODE"] = "compact"
        try:
            assert gw.resolve_compact(None) is False
        finally:
            os.environ.pop("_RESPONSE_MODE", None)

    def test_no_prefix_session_compact_still_works(self):
        gw = _NoPrefixGateway()
        gw._response_mode = "compact"
        assert gw.resolve_compact(None) is True

    def test_no_prefix_explicit_still_works(self):
        gw = _NoPrefixGateway()
        assert gw.resolve_compact(True) is True
        assert gw.resolve_compact(False) is False


class TestSetResponseMode:
    """Tests for set_response_mode()."""

    def test_set_compact(self):
        gw = _TestGateway()
        gw._response_mode = "verbose"
        result = gw.set_response_mode("compact")
        assert result["success"] is True
        assert result["mode"] == "compact"
        assert result["previous_mode"] == "verbose"
        assert gw._response_mode == "compact"

    def test_set_verbose(self):
        gw = _TestGateway()
        gw._response_mode = "compact"
        result = gw.set_response_mode("verbose")
        assert result["success"] is True
        assert result["mode"] == "verbose"
        assert result["previous_mode"] == "compact"
        assert gw._response_mode == "verbose"

    def test_set_invalid_mode(self):
        gw = _TestGateway()
        result = gw.set_response_mode("terse")
        assert result["success"] is False
        assert "verbose" in result["error"]
        assert "compact" in result["error"]
        # Mode should not have changed
        assert gw._response_mode == "verbose"

    def test_set_idempotent(self):
        gw = _TestGateway()
        gw._response_mode = "compact"
        result = gw.set_response_mode("compact")
        assert result["success"] is True
        assert result["previous_mode"] == "compact"


class TestStripCompact:
    """Tests for strip_compact()."""

    def test_strips_compact(self):
        assert CompactMixin.strip_compact({"_compact": True, "filename": "x.md"}) == {"filename": "x.md"}

    def test_strips_fields(self):
        assert CompactMixin.strip_compact({"fields": ["Status"], "filename": "x.md"}) == {"filename": "x.md"}

    def test_strips_both(self):
        assert CompactMixin.strip_compact({"_compact": True, "fields": ["Status"], "filename": "x.md"}) == {"filename": "x.md"}

    def test_no_compact_keys(self):
        assert CompactMixin.strip_compact({"filename": "x.md", "size": 100}) == {"filename": "x.md", "size": 100}

    def test_empty_dict(self):
        assert CompactMixin.strip_compact({}) == {}

    def test_does_not_mutate_original(self):
        original = {"_compact": True, "filename": "x.md"}
        result = CompactMixin.strip_compact(original)
        assert original == {"_compact": True, "filename": "x.md"}
        assert result == {"filename": "x.md"}
