"""Tests for generic MCP execution, contract, resilience, and HTTP primitives."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest

from archolith_mcp_framework import (
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
    JsonResource,
    McpCallEvent,
    McpTool,
    json_preview,
    json_size,
    RequestContextMiddleware,
    get_request_id,
    get_request_id_from_scope,
    render_json,
    run_mcp_call,
)


@dataclass
class _Recorder:
    events: list[McpCallEvent] = field(default_factory=list)

    def record(self, event: McpCallEvent) -> None:
        self.events.append(event)


class _FailingRecorder:
    def record(self, event: McpCallEvent) -> None:
        raise RuntimeError("telemetry down")


class TestRunMcpCall:
    async def test_success_records_result_metadata(self) -> None:
        recorder = _Recorder()

        async def runner() -> dict[str, bool]:
            return {"ok": True}

        assert await run_mcp_call(
            kind="tool",
            operation="status",
            payload={"target": "backend"},
            runner=runner,
            recorder=recorder,
        ) == {"ok": True}
        assert len(recorder.events) == 1
        event = recorder.events[0]
        assert event.success is True
        assert event.error is None
        assert event.input_size is not None
        assert event.result_size is not None
        assert json_size({"a": 1}) == len('{"a": 1}')
        assert json_preview({"b": 1, "a": 2}, limit=12) == '{"a": 2, ...'

    async def test_exception_uses_error_mapper_and_records_failure(self) -> None:
        recorder = _Recorder()

        async def runner() -> str:
            raise ValueError("bad input")

        result = await run_mcp_call(
            kind="tool",
            operation="status",
            payload={},
            runner=runner,
            recorder=recorder,
            error_mapper=lambda message: {"error": message},
        )
        assert result == {"error": "ValueError: bad input"}
        assert recorder.events[0].success is False
        assert recorder.events[0].error == "ValueError: bad input"

    async def test_timeout_returns_tool_safe_error_and_records_failure(self) -> None:
        recorder = _Recorder()

        async def runner() -> str:
            await asyncio.sleep(1)
            return "late"

        result = await run_mcp_call(
            kind="resource",
            operation="resource://slow",
            payload={},
            runner=runner,
            recorder=recorder,
            timeout=0.01,
        )
        assert result == "Error: TIMEOUT: resource://slow exceeded 0.01s limit"
        assert recorder.events[0].success is False
        assert recorder.events[0].error == "TIMEOUT: resource://slow exceeded 0.01s limit"

    async def test_recorder_failure_never_breaks_the_call(self) -> None:
        async def runner() -> str:
            return "ok"

        assert await run_mcp_call(
            kind="tool",
            operation="status",
            payload={},
            runner=runner,
            recorder=_FailingRecorder(),
        ) == "ok"


class TestCircuitBreaker:
    async def test_trips_and_rejects_calls_after_threshold(self) -> None:
        transitions = []
        breaker = CircuitBreaker("backend", failure_threshold=2, on_transition=transitions.append)

        async def fail() -> None:
            raise ConnectionError("unavailable")

        for _ in range(2):
            with pytest.raises(ConnectionError):
                await breaker.call(fail)

        assert breaker.state_snapshot()["state"] == CircuitState.OPEN
        assert [(event.previous, event.current) for event in transitions] == [
            (CircuitState.CLOSED, CircuitState.OPEN),
        ]
        with pytest.raises(CircuitOpenError):
            await breaker.call(fail)

    async def test_successful_probe_closes_the_circuit(self) -> None:
        transitions = []
        breaker = CircuitBreaker("backend", failure_threshold=1, cooldown_seconds=0, on_transition=transitions.append)

        async def fail() -> None:
            raise ConnectionError("unavailable")

        async def succeed() -> str:
            return "ok"

        with pytest.raises(ConnectionError):
            await breaker.call(fail)
        assert await breaker.call(succeed) == "ok"
        assert breaker.state_snapshot()["state"] == CircuitState.CLOSED
        assert [(event.previous, event.current) for event in transitions] == [
            (CircuitState.CLOSED, CircuitState.OPEN),
            (CircuitState.OPEN, CircuitState.HALF_OPEN),
            (CircuitState.HALF_OPEN, CircuitState.CLOSED),
        ]

    async def test_non_trip_exception_does_not_open_the_circuit(self) -> None:
        breaker = CircuitBreaker("backend", failure_threshold=1)

        async def fail() -> None:
            raise ValueError("invalid response")

        with pytest.raises(ValueError):
            await breaker.call(fail)
        assert breaker.state_snapshot()["state"] == CircuitState.CLOSED
        assert breaker.state_snapshot()["failures"] == 0


class _Resource(JsonResource):
    uri = "resource://status"
    name = "status"
    description = "Get status."

    async def build_payload(self, name: str) -> dict[str, Any]:
        return {"name": name}

    async def endpoint(self, name: str) -> dict[str, Any]:
        return {"name": name}


class _Tool(McpTool):
    name = "status"
    description = "Get status."

    async def endpoint(self, name: str) -> str:
        return name


class _FakeMcp:
    def __init__(self) -> None:
        self.resource_handler: Any = None
        self.tool_handler: Any = None
        self.resource_options: dict[str, Any] = {}

    def resource(self, uri: str, **kwargs: Any):
        self.resource_options = {"uri": uri, **kwargs}

        def decorator(handler: Any) -> Any:
            self.resource_handler = handler
            return handler

        return decorator

    def tool(self):
        def decorator(handler: Any) -> Any:
            self.tool_handler = handler
            return handler

        return decorator


class TestContracts:
    async def test_json_resource_renders_compact_stable_json(self) -> None:
        resource = _Resource()
        assert await resource.execute("Ada") == '{"name":"Ada"}'
        assert resource.kind == "resource"
        assert resource.call_payload("Ada") == {"args": ["Ada"]}
        assert render_json({"b": 1, "a": 2}) == '{"a":2,"b":1}'

    async def test_registration_uses_public_metadata_and_handler(self) -> None:
        mcp = _FakeMcp()
        resource = _Resource()
        tool = _Tool()
        resource.register(mcp)
        assert mcp.resource_options == {
            "uri": "resource://status",
            "name": "status",
            "description": "Get status.",
            "mime_type": "application/json",
        }
        assert await mcp.resource_handler("Ada") == '{"name":"Ada"}'

        tool.register(mcp)
        assert mcp.tool_handler.__name__ == "status"
        assert await mcp.tool_handler("Ada") == "Ada"


class TestRequestContextMiddleware:
    async def test_reuses_incoming_id_and_clears_context_after_response(self) -> None:
        sent: list[dict[str, Any]] = []

        async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
            assert get_request_id() == "trace-123"
            assert get_request_id_from_scope(scope) == "trace-123"
            await send({"type": "http.response.start", "status": 200, "headers": []})

        async def send(message: dict[str, Any]) -> None:
            sent.append(message)

        middleware = RequestContextMiddleware(app)
        await middleware(
            {"type": "http", "headers": [(b"x-request-id", b"trace-123")]},
            lambda: None,
            send,
        )
        assert get_request_id() is None
        assert sent[0]["headers"] == [[b"x-request-id", b"trace-123"]]