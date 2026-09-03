"""Shared pagination primitives for MCP tool listings.

THE single definition of how every Python MCP server in this workspace windows
a list response and reports that it did.

Why this lives in the framework rather than in each server
---------------------------------------------------------
A 2026-09-03 inventory found the same defect in four unrelated servers: a
partial result rendered in the same shape as a complete one. A truncated list
and a short list were indistinguishable, so a caller acting on "that's all of
them" was silently wrong. Each server that had grown a fix had also grown its
own field names -- ``has_more`` here, ``truncated`` there, ``result_truncated``
somewhere else -- so an agent calling several servers met several dialects of
one idea.

The contract, which is the part that actually matters:

    A partial result MUST NOT share a wire shape with a complete one.

``page_slice`` computes the window; ``paginate`` applies it to a concrete list
and returns the envelope fields to merge into a response. Both are pure.

What this does NOT decide
-------------------------
Ordering is the caller's job, and it is not optional: window a list with no
total order and consecutive pages can overlap or skip entries with nothing to
detect it. Sort before paginating, and break ties on a unique key.

This is OFFSET paging, which is correct only when the underlying sequence is
stable for the duration of the walk (a directory listing, an in-memory list, a
completed result string). For a sequence being written while it is read -- a
graph under ingest, a live queue -- offset paging silently drops rows as items
shift beneath the cursor; use a keyset cursor instead.
"""

from __future__ import annotations

from typing import Any, Sequence, TypeVar

__all__ = [
    "DEFAULT_PAGE_LIMIT",
    "MAX_PAGE_LIMIT",
    "PageWindow",
    "page_slice",
    "paginate",
    "PaginationMixin",
]

#: Page size used when a caller does not ask for one.
DEFAULT_PAGE_LIMIT = 100

#: Ceiling on any caller-supplied limit. A caller asking for more gets this.
MAX_PAGE_LIMIT = 500

T = TypeVar("T")


class PageWindow(dict):
    """Slice bounds plus the truthful description of what was left out.

    A dict subclass so it merges straight into a tool response and stays
    JSON-serializable, while still being addressable by attribute in tests.
    """

    @property
    def start(self) -> int:
        return int(self["start"])

    @property
    def end(self) -> int:
        return int(self["end"])

    @property
    def truncated(self) -> bool:
        return bool(self["truncated"])


def page_slice(
    total: int,
    limit: int | None = None,
    offset: int | None = None,
    *,
    default_limit: int = DEFAULT_PAGE_LIMIT,
    max_limit: int = MAX_PAGE_LIMIT,
) -> PageWindow:
    """Resolve a page window over ``total`` items.

    Out-of-range and negative inputs clamp rather than raise: a bad offset
    should yield an honest empty page, not an error a caller might treat as a
    listing failure.

    Args:
        total: Number of items available.
        limit: Requested page size. None/0 uses ``default_limit``; anything
            above ``max_limit`` is clamped down to it.
        offset: Items to skip. Negative clamps to 0; past the end yields an
            empty window.

    Returns:
        PageWindow with start, end, limit, total, truncated and next_offset.
        ``truncated`` is True whenever the window omits anything at either
        end -- including a final page that starts late but runs to the end,
        because that page is still not the whole listing.
    """
    safe_total = max(0, int(total))
    try:
        requested = int(limit) if limit else default_limit
    except (TypeError, ValueError):
        requested = default_limit
    safe_limit = max(1, min(requested, max_limit))

    try:
        requested_offset = int(offset) if offset else 0
    except (TypeError, ValueError):
        requested_offset = 0
    start = max(0, min(requested_offset, safe_total))
    end = min(safe_total, start + safe_limit)

    return PageWindow(
        start=start,
        end=end,
        limit=safe_limit,
        total=safe_total,
        truncated=(start > 0 or end < safe_total),
        next_offset=(end if end < safe_total else None),
    )


def paginate(
    items: Sequence[T],
    limit: int | None = None,
    offset: int | None = None,
    *,
    default_limit: int = DEFAULT_PAGE_LIMIT,
    max_limit: int = MAX_PAGE_LIMIT,
) -> tuple[list[T], dict[str, Any]]:
    """Window a list and produce the envelope fields describing the window.

    Sort ``items`` into a total order BEFORE calling this. See module docstring.

    Returns:
        (page, envelope). Merge ``envelope`` into the tool response. It always
        carries ``total`` and ``returned``; when the page is partial it also
        carries ``truncated: True``, ``offset`` and ``next_offset``. A complete
        listing carries ``truncated: False`` so the flag is always present and
        a caller never has to infer completeness from a row count.
    """
    window = page_slice(
        len(items), limit, offset, default_limit=default_limit, max_limit=max_limit
    )
    page = list(items[window.start:window.end])

    envelope: dict[str, Any] = {
        "total": window["total"],
        "returned": len(page),
        "truncated": window["truncated"],
    }
    if window.truncated:
        envelope["offset"] = window.start
        envelope["next_offset"] = window["next_offset"]
    return page, envelope


class PaginationMixin:
    """Mixin exposing the pagination primitives as methods.

    For gateways that already compose framework mixins and prefer
    ``self.paginate(...)`` alongside ``self.resolve_compact(...)``.
    """

    #: Override per gateway if its listings warrant a different default.
    default_page_limit: int = DEFAULT_PAGE_LIMIT
    max_page_limit: int = MAX_PAGE_LIMIT

    def page_slice(
        self, total: int, limit: int | None = None, offset: int | None = None
    ) -> PageWindow:
        return page_slice(
            total,
            limit,
            offset,
            default_limit=self.default_page_limit,
            max_limit=self.max_page_limit,
        )

    def paginate(
        self, items: Sequence[T], limit: int | None = None, offset: int | None = None
    ) -> tuple[list[T], dict[str, Any]]:
        return paginate(
            items,
            limit,
            offset,
            default_limit=self.default_page_limit,
            max_limit=self.max_page_limit,
        )
