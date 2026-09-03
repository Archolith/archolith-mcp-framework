"""Tests for the upstream failure classifier and the async circuit breaker."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from archolith_mcp_framework import (
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
    CircuitTransition,
    should_trip_circuit,
)


def _http_status_error(code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://upstream.invalid/health")
    response = httpx.Response(code, request=request)
    return httpx.HTTPStatusError("boom", request=request, response=response)


class _DuckTypedStatus(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"status {status_code}")
        self.status_code = status_code


class APIConnectionError(Exception):
    """Name-matched stand-in for an SDK error class the framework cannot import."""


class TestShouldTripCircuit:
    @pytest.mark.parametrize(
        "exc",
        [
            asyncio.TimeoutError(),
            TimeoutError(),
            httpx.ConnectError("refused"),
            httpx.ReadTimeout("slow"),
            httpx.ConnectTimeout("slow"),
            httpx.WriteTimeout("slow"),
            httpx.PoolTimeout("saturated"),
            ConnectionError("reset"),
            OSError("socket gone"),
        ],
    )
    def test_transport_and_timeout_failures_trip(self, exc: Exception) -> None:
        assert should_trip_circuit(exc) is True

    @pytest.mark.parametrize("code", [429, 500, 502, 503, 504])
    def test_rate_limit_and_server_errors_trip(self, code: int) -> None:
        assert should_trip_circuit(_http_status_error(code)) is True

    @pytest.mark.parametrize("code", [400, 401, 403, 404, 409, 422])
    def test_client_errors_do_not_trip(self, code: int) -> None:
        assert should_trip_circuit(_http_status_error(code)) is False

    @pytest.mark.parametrize(
        ("code", "expected"),
        [(429, True), (500, True), (503, True), (400, False), (404, False)],
    )
    def test_duck_typed_status_code_is_honoured(self, code: int, expected: bool) -> None:
        assert should_trip_circuit(_DuckTypedStatus(code)) is expected

    def test_sdk_error_classes_match_by_name(self) -> None:
        assert should_trip_circuit(APIConnectionError("no route")) is True

    def test_unrelated_errors_do_not_trip(self) -> None:
        assert should_trip_circuit(ValueError("bad argument")) is False
        assert should_trip_circuit(KeyError("missing")) is False

    def test_non_integer_status_code_is_ignored(self) -> None:
        exc = Exception("weird")
        exc.status_code = "500"  # type: ignore[attr-defined]
        assert should_trip_circuit(exc) is False


async def _fail() -> None:
    raise ConnectionError("unavailable")


async def _succeed() -> str:
    return "ok"


class TestCircuitBreaker:
    async def test_trips_and_rejects_calls_after_threshold(self) -> None:
        transitions: list[CircuitTransition] = []
        breaker = CircuitBreaker("backend", failure_threshold=2, on_transition=transitions.append)

        for _ in range(2):
            with pytest.raises(ConnectionError):
                await breaker.call(_fail)

        assert breaker.state_snapshot()["state"] == CircuitState.OPEN
        assert [(t.previous, t.current) for t in transitions] == [
            (CircuitState.CLOSED, CircuitState.OPEN),
        ]
        with pytest.raises(CircuitOpenError):
            await breaker.call(_fail)

    async def test_rejection_below_threshold_does_not_open(self) -> None:
        breaker = CircuitBreaker("backend", failure_threshold=3)

        for _ in range(2):
            with pytest.raises(ConnectionError):
                await breaker.call(_fail)

        snapshot = breaker.state_snapshot()
        assert snapshot["state"] == CircuitState.CLOSED
        assert snapshot["failures"] == 2

    async def test_open_circuit_error_carries_a_state_snapshot(self) -> None:
        breaker = CircuitBreaker("backend", failure_threshold=1, cooldown_seconds=30)

        with pytest.raises(ConnectionError):
            await breaker.call(_fail)

        with pytest.raises(CircuitOpenError) as excinfo:
            await breaker.call(_succeed)

        snapshot = excinfo.value.snapshot
        assert snapshot["name"] == "backend"
        assert snapshot["state"] == CircuitState.OPEN
        assert snapshot["failures"] == 1
        assert 0 < snapshot["cooldown_remaining_s"] <= 30

    async def test_rejected_call_never_reaches_the_wrapped_function(self) -> None:
        breaker = CircuitBreaker("backend", failure_threshold=1, cooldown_seconds=30)
        calls = 0

        async def counted() -> str:
            nonlocal calls
            calls += 1
            return "ok"

        with pytest.raises(ConnectionError):
            await breaker.call(_fail)
        with pytest.raises(CircuitOpenError):
            await breaker.call(counted)

        assert calls == 0

    async def test_successful_probe_closes_the_circuit(self) -> None:
        transitions: list[CircuitTransition] = []
        breaker = CircuitBreaker(
            "backend", failure_threshold=1, cooldown_seconds=0, on_transition=transitions.append
        )

        with pytest.raises(ConnectionError):
            await breaker.call(_fail)
        assert await breaker.call(_succeed) == "ok"

        snapshot = breaker.state_snapshot()
        assert snapshot["state"] == CircuitState.CLOSED
        assert snapshot["failures"] == 0
        assert [(t.previous, t.current) for t in transitions] == [
            (CircuitState.CLOSED, CircuitState.OPEN),
            (CircuitState.OPEN, CircuitState.HALF_OPEN),
            (CircuitState.HALF_OPEN, CircuitState.CLOSED),
        ]

    async def test_failed_probe_reopens_the_circuit_and_restarts_cooldown(self) -> None:
        transitions: list[CircuitTransition] = []
        breaker = CircuitBreaker(
            "backend", failure_threshold=1, cooldown_seconds=0, on_transition=transitions.append
        )

        with pytest.raises(ConnectionError):
            await breaker.call(_fail)
        # Cooldown has elapsed, so this call is the half-open probe -- and it fails.
        with pytest.raises(ConnectionError):
            await breaker.call(_fail)

        assert breaker.state_snapshot()["state"] == CircuitState.OPEN
        assert [(t.previous, t.current) for t in transitions] == [
            (CircuitState.CLOSED, CircuitState.OPEN),
            (CircuitState.OPEN, CircuitState.HALF_OPEN),
            (CircuitState.HALF_OPEN, CircuitState.OPEN),
        ]

    async def test_only_one_probe_runs_while_half_open(self) -> None:
        """A recovering upstream must see a single probe, not every queued caller."""

        breaker = CircuitBreaker("backend", failure_threshold=1, cooldown_seconds=0)
        with pytest.raises(ConnectionError):
            await breaker.call(_fail)

        started = asyncio.Event()
        release = asyncio.Event()
        concurrent = 0

        async def slow_probe() -> str:
            nonlocal concurrent
            concurrent += 1
            started.set()
            await release.wait()
            return "ok"

        probe = asyncio.create_task(breaker.call(slow_probe))
        try:
            await asyncio.wait_for(started.wait(), timeout=5)

            # The circuit is HALF_OPEN with a probe in flight; every other caller is
            # rejected immediately. The bounded wait turns a lost gate into a fast
            # failure instead of a hang -- without it, a queued caller would block on
            # `release` forever.
            for _ in range(2):
                with pytest.raises(CircuitOpenError):
                    await asyncio.wait_for(breaker.call(slow_probe), timeout=5)
        finally:
            release.set()

        assert await asyncio.wait_for(probe, timeout=5) == "ok"
        assert concurrent == 1
        assert breaker.state_snapshot()["state"] == CircuitState.CLOSED

    async def test_non_trip_exception_does_not_open_the_circuit(self) -> None:
        breaker = CircuitBreaker("backend", failure_threshold=1)

        async def bad_argument() -> None:
            raise ValueError("invalid response")

        with pytest.raises(ValueError):
            await breaker.call(bad_argument)

        snapshot = breaker.state_snapshot()
        assert snapshot["state"] == CircuitState.CLOSED
        assert snapshot["failures"] == 0

    async def test_success_resets_accumulated_failures(self) -> None:
        breaker = CircuitBreaker("backend", failure_threshold=3)

        for _ in range(2):
            with pytest.raises(ConnectionError):
                await breaker.call(_fail)
        assert breaker.state_snapshot()["failures"] == 2

        assert await breaker.call(_succeed) == "ok"
        assert breaker.state_snapshot()["failures"] == 0

    async def test_transition_hook_failure_never_breaks_the_call(self) -> None:
        def exploding_hook(transition: CircuitTransition) -> None:
            raise RuntimeError("metrics sink down")

        breaker = CircuitBreaker("backend", failure_threshold=1, on_transition=exploding_hook)

        with pytest.raises(ConnectionError):
            await breaker.call(_fail)

        assert breaker.state_snapshot()["state"] == CircuitState.OPEN

    async def test_transition_hook_receives_the_breaker_name_and_failure_count(self) -> None:
        transitions: list[CircuitTransition] = []
        breaker = CircuitBreaker("upstream-api", failure_threshold=2, on_transition=transitions.append)

        for _ in range(2):
            with pytest.raises(ConnectionError):
                await breaker.call(_fail)

        assert len(transitions) == 1
        assert transitions[0].name == "upstream-api"
        assert transitions[0].failures == 2

    async def test_closed_circuit_snapshot_reports_no_cooldown(self) -> None:
        breaker = CircuitBreaker("backend", cooldown_seconds=30)
        assert breaker.state_snapshot() == {
            "name": "backend",
            "state": CircuitState.CLOSED,
            "failures": 0,
            "cooldown_remaining_s": 0.0,
        }

    async def test_breakers_are_isolated_from_one_another(self) -> None:
        first = CircuitBreaker("first", failure_threshold=1)
        second = CircuitBreaker("second", failure_threshold=1)

        with pytest.raises(ConnectionError):
            await first.call(_fail)

        assert first.state_snapshot()["state"] == CircuitState.OPEN
        assert second.state_snapshot()["state"] == CircuitState.CLOSED
        assert await second.call(_succeed) == "ok"
