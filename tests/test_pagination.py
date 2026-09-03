"""Tests for the shared pagination primitives.

The contract under test is not "slicing works" -- it is that a partial result
never shares a wire shape with a complete one. Every assertion about
`truncated` is guarding that.
"""

from __future__ import annotations

import pytest

from archolith_mcp_framework import (
    DEFAULT_PAGE_LIMIT,
    MAX_PAGE_LIMIT,
    page_slice,
    paginate,
    PaginationMixin,
)


# ---------------------------------------------------------------------------
# The invariant
# ---------------------------------------------------------------------------


def test_complete_and_truncated_pages_are_distinguishable():
    """The whole point. Two 40-item pages, one complete and one not."""
    complete, env_complete = paginate(list(range(40)), limit=40)
    partial, env_partial = paginate(list(range(100)), limit=40)

    assert len(complete) == len(partial) == 40  # identical row counts...
    assert env_complete["truncated"] is False   # ...but never identical shape
    assert env_partial["truncated"] is True
    assert env_complete["total"] == 40
    assert env_partial["total"] == 100


def test_truncated_flag_is_always_present():
    """A caller must never have to infer completeness from a row count."""
    for total, limit in [(0, 10), (5, 10), (10, 10), (11, 10), (1000, 10)]:
        _, env = paginate(list(range(total)), limit=limit)
        assert "truncated" in env, (total, limit)
        assert isinstance(env["truncated"], bool)


def test_final_page_that_starts_late_is_still_truncated():
    """It runs to the end, but it is not the whole listing."""
    page, env = paginate(list(range(100)), limit=50, offset=50)
    assert page == list(range(50, 100))
    assert env["truncated"] is True
    assert env["next_offset"] is None  # nothing after it, but still partial


# ---------------------------------------------------------------------------
# Walking a listing to exhaustion
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("total,limit", [(100, 7), (100, 100), (1, 10), (0, 10), (997, 33)])
def test_walk_visits_every_item_exactly_once(total, limit):
    items = list(range(total))
    seen: list[int] = []
    offset = 0
    guard = 0
    while True:
        guard += 1
        assert guard < 1000, "walk did not terminate"
        page, env = paginate(items, limit=limit, offset=offset)
        seen.extend(page)
        nxt = env.get("next_offset")
        if nxt is None:
            break
        offset = nxt

    assert seen == items
    assert len(seen) == len(set(seen))


# ---------------------------------------------------------------------------
# Clamping: bad input yields an honest page, never an exception
# ---------------------------------------------------------------------------


def test_offset_past_end_yields_empty_page_not_error():
    page, env = paginate(list(range(10)), limit=5, offset=999)
    assert page == []
    assert env["total"] == 10
    assert env["returned"] == 0
    assert env["truncated"] is True
    assert env["next_offset"] is None


def test_negative_inputs_clamp():
    page, env = paginate(list(range(10)), limit=-5, offset=-5)
    assert env["total"] == 10
    assert page[0] == 0  # offset clamped to 0
    assert env["returned"] >= 1  # limit clamped to >= 1


def test_limit_is_capped_at_max():
    w = page_slice(10_000, limit=999_999)
    assert w["limit"] == MAX_PAGE_LIMIT


def test_limit_defaults_when_absent_or_zero():
    assert page_slice(1000)["limit"] == DEFAULT_PAGE_LIMIT
    assert page_slice(1000, limit=0)["limit"] == DEFAULT_PAGE_LIMIT
    assert page_slice(1000, limit=None)["limit"] == DEFAULT_PAGE_LIMIT


def test_non_numeric_inputs_fall_back_rather_than_raise():
    w = page_slice(100, limit="nonsense", offset="nonsense")  # type: ignore[arg-type]
    assert w["limit"] == DEFAULT_PAGE_LIMIT
    assert w.start == 0


def test_empty_listing():
    page, env = paginate([], limit=10)
    assert page == []
    assert env["total"] == 0
    assert env["returned"] == 0
    assert env["truncated"] is False
    assert "next_offset" not in env


def test_exact_fit_is_not_truncated():
    _, env = paginate(list(range(50)), limit=50)
    assert env["truncated"] is False
    assert "next_offset" not in env


# ---------------------------------------------------------------------------
# Mixin
# ---------------------------------------------------------------------------


def test_mixin_respects_overridden_defaults():
    class Gateway(PaginationMixin):
        default_page_limit = 5
        max_page_limit = 10

    g = Gateway()
    page, env = g.paginate(list(range(100)))
    assert len(page) == 5
    assert env["truncated"] is True
    assert g.page_slice(100, limit=999)["limit"] == 10


def test_mixin_and_functions_agree():
    class Gateway(PaginationMixin):
        pass

    items = list(range(37))
    assert Gateway().paginate(items, limit=10, offset=10) == paginate(
        items, limit=10, offset=10
    )
