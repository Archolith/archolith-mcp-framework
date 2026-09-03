"""Tests for ASGI request-id correlation on HTTP-exposed MCP surfaces."""

from __future__ import annotations

from typing import Any

import pytest

from archolith_mcp_framework import (
    RequestContextMiddleware,
    get_request_id,
    get_request_id_from_scope,
)


async def _receive() -> dict[str, Any]:  # pragma: no cover - never awaited in these tests
    return {"type": "http.request"}


def _response_headers(sent: list[dict[str, Any]]) -> list[list[bytes]]:
    start = next(m for m in sent if m["type"] == "http.response.start")
    return start["headers"]


def _request_id_values(headers: list[list[bytes]]) -> list[bytes]:
    return [value for name, value in headers if bytes(name).lower() == b"x-request-id"]


class TestRequestContextMiddleware:
    async def test_reuses_incoming_id_and_clears_context_after_response(self) -> None:
        sent: list[dict[str, Any]] = []
        seen: dict[str, Any] = {}

        async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
            seen["contextvar"] = get_request_id()
            seen["scope"] = get_request_id_from_scope(scope)
            await send({"type": "http.response.start", "status": 200, "headers": []})

        async def send(message: dict[str, Any]) -> None:
            sent.append(message)

        await RequestContextMiddleware(app)(
            {"type": "http", "headers": [(b"x-request-id", b"trace-123")]}, _receive, send
        )

        assert seen == {"contextvar": "trace-123", "scope": "trace-123"}
        assert get_request_id() is None
        assert _request_id_values(_response_headers(sent)) == [b"trace-123"]

    async def test_mints_an_id_when_the_header_is_absent(self) -> None:
        sent: list[dict[str, Any]] = []
        seen: dict[str, Any] = {}

        async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
            seen["id"] = get_request_id()
            await send({"type": "http.response.start", "status": 200, "headers": []})

        async def send(message: dict[str, Any]) -> None:
            sent.append(message)

        await RequestContextMiddleware(app)({"type": "http", "headers": []}, _receive, send)

        minted = seen["id"]
        assert isinstance(minted, str) and len(minted) == 32
        assert _request_id_values(_response_headers(sent)) == [minted.encode()]

    async def test_each_request_gets_a_distinct_minted_id(self) -> None:
        seen: list[str | None] = []

        async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
            seen.append(get_request_id())
            await send({"type": "http.response.start", "status": 200, "headers": []})

        async def send(message: dict[str, Any]) -> None:
            return None

        middleware = RequestContextMiddleware(app)
        for _ in range(2):
            await middleware({"type": "http", "headers": []}, _receive, send)

        assert len(set(seen)) == 2

    @pytest.mark.parametrize("header", [b"", b"   "])
    async def test_blank_incoming_header_is_replaced_with_a_minted_id(self, header: bytes) -> None:
        seen: dict[str, Any] = {}

        async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
            seen["id"] = get_request_id()
            await send({"type": "http.response.start", "status": 200, "headers": []})

        async def send(message: dict[str, Any]) -> None:
            return None

        await RequestContextMiddleware(app)(
            {"type": "http", "headers": [(b"x-request-id", header)]}, _receive, send
        )

        assert seen["id"] and seen["id"].strip() == seen["id"]
        assert len(seen["id"]) == 32

    async def test_existing_response_header_is_not_duplicated(self) -> None:
        sent: list[dict[str, Any]] = []

        async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [[b"x-request-id", b"app-owned"]],
                }
            )

        async def send(message: dict[str, Any]) -> None:
            sent.append(message)

        await RequestContextMiddleware(app)(
            {"type": "http", "headers": [(b"x-request-id", b"trace-123")]}, _receive, send
        )

        assert _request_id_values(_response_headers(sent)) == [b"app-owned"]

    async def test_other_response_headers_are_preserved(self) -> None:
        sent: list[dict[str, Any]] = []

        async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [[b"content-type", b"application/json"]],
                }
            )

        async def send(message: dict[str, Any]) -> None:
            sent.append(message)

        await RequestContextMiddleware(app)({"type": "http", "headers": []}, _receive, send)

        headers = _response_headers(sent)
        assert [b"content-type", b"application/json"] in headers
        assert len(_request_id_values(headers)) == 1

    async def test_body_messages_pass_through_untouched(self) -> None:
        sent: list[dict[str, Any]] = []

        async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"payload"})

        async def send(message: dict[str, Any]) -> None:
            sent.append(message)

        await RequestContextMiddleware(app)({"type": "http", "headers": []}, _receive, send)

        assert sent[1] == {"type": "http.response.body", "body": b"payload"}

    async def test_non_http_scopes_bypass_the_middleware(self) -> None:
        seen: dict[str, Any] = {}

        async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
            seen["id"] = get_request_id()
            seen["state"] = scope.get("state")

        await RequestContextMiddleware(app)({"type": "websocket"}, _receive, lambda m: None)

        assert seen == {"id": None, "state": None}

    async def test_context_is_reset_when_the_app_raises(self) -> None:
        async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
            assert get_request_id() == "trace-123"
            raise RuntimeError("handler exploded")

        with pytest.raises(RuntimeError):
            await RequestContextMiddleware(app)(
                {"type": "http", "headers": [(b"x-request-id", b"trace-123")]},
                _receive,
                lambda m: None,
            )

        assert get_request_id() is None

    async def test_scope_state_is_populated_for_downstream_readers(self) -> None:
        scope: dict[str, Any] = {"type": "http", "headers": [(b"x-request-id", b"trace-123")]}

        async def app(inner: dict[str, Any], receive: Any, send: Any) -> None:
            await send({"type": "http.response.start", "status": 200, "headers": []})

        async def send(message: dict[str, Any]) -> None:
            return None

        await RequestContextMiddleware(app)(scope, _receive, send)

        assert scope["state"]["request_id"] == "trace-123"


class TestGetRequestIdFromScope:
    def test_returns_none_without_state(self) -> None:
        assert get_request_id_from_scope({"type": "http"}) is None

    def test_returns_none_when_state_is_not_a_mapping(self) -> None:
        assert get_request_id_from_scope({"type": "http", "state": "nope"}) is None

    def test_returns_none_for_empty_or_non_string_ids(self) -> None:
        assert get_request_id_from_scope({"state": {"request_id": ""}}) is None
        assert get_request_id_from_scope({"state": {"request_id": 123}}) is None

    def test_returns_the_bound_id(self) -> None:
        assert get_request_id_from_scope({"state": {"request_id": "trace-123"}}) == "trace-123"
