"""Failure classification and an async circuit breaker for external services."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from time import monotonic
from typing import Any, TypeVar

import httpx

T = TypeVar("T")
logger = logging.getLogger(__name__)


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    """Raised when a call is rejected because its circuit is open."""

    def __init__(self, message: str, *, snapshot: dict[str, Any]) -> None:
        super().__init__(message)
        self.snapshot = snapshot


@dataclass(frozen=True)
class CircuitTransition:
    """A circuit state transition reported to an optional consumer hook."""

    name: str
    previous: CircuitState
    current: CircuitState
    failures: int
    reason: str | None = None


CircuitTransitionHook = Callable[[CircuitTransition], None]


def should_trip_circuit(exc: Exception) -> bool:
    """Return whether an exception indicates an upstream service-health failure."""

    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return True
    if isinstance(
        exc,
        (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout, httpx.WriteTimeout, httpx.PoolTimeout),
    ):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return code == 429 or code >= 500
    if isinstance(exc, (ConnectionError, OSError)):
        return True

    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int) and (status_code == 429 or status_code >= 500):
        return True

    return type(exc).__name__ in {
        "APIConnectionError",
        "APITimeoutError",
        "RateLimitError",
        "InternalServerError",
    }


@dataclass
class CircuitBreaker:
    """Async CLOSED/OPEN/HALF_OPEN circuit breaker for external calls."""

    name: str
    failure_threshold: int = 3
    cooldown_seconds: float = 30.0
    on_transition: CircuitTransitionHook | None = None
    _state: CircuitState = field(default=CircuitState.CLOSED, init=False)
    _failures: int = field(default=0, init=False)
    _opened_at: float | None = field(default=None, init=False)
    _probe_in_flight: bool = field(default=False, init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    def state_snapshot(self) -> dict[str, Any]:
        """Return a serializable snapshot of the current state."""

        remaining = 0.0
        if self._opened_at is not None and self._state in (CircuitState.OPEN, CircuitState.HALF_OPEN):
            remaining = max(0.0, self.cooldown_seconds - (monotonic() - self._opened_at))
        return {
            "name": self.name,
            "state": self._state.value,
            "failures": self._failures,
            "cooldown_remaining_s": remaining,
        }

    def _transition(self, current: CircuitState, *, reason: str | None = None) -> None:
        previous = self._state
        self._state = current
        if self.on_transition is None:
            return
        try:
            self.on_transition(
                CircuitTransition(
                    name=self.name,
                    previous=previous,
                    current=current,
                    failures=self._failures,
                    reason=reason,
                )
            )
        except Exception:
            logger.exception("Circuit breaker %s transition hook failed", self.name)

    async def call(self, fn: Callable[[], Awaitable[T]]) -> T:
        """Execute ``fn`` and apply state transitions around upstream failures."""

        async with self._lock:
            if self._state == CircuitState.OPEN:
                now = monotonic()
                elapsed = now - (self._opened_at or now)
                if elapsed >= self.cooldown_seconds:
                    self._transition(CircuitState.HALF_OPEN)
                    self._probe_in_flight = True
                else:
                    raise CircuitOpenError(
                        f"Circuit breaker open for {self.name}; "
                        f"state={self._state.value} failures={self._failures} "
                        f"cooldown_remaining_s={self.cooldown_seconds - elapsed:.0f}",
                        snapshot=self.state_snapshot(),
                    )
            elif self._state == CircuitState.HALF_OPEN:
                if self._probe_in_flight:
                    raise CircuitOpenError(
                        f"Circuit breaker open for {self.name}; "
                        f"state={self._state.value} probe in flight",
                        snapshot=self.state_snapshot(),
                    )
                self._probe_in_flight = True

            is_probe = self._state == CircuitState.HALF_OPEN

        try:
            result = await fn()
        except Exception as exc:
            async with self._lock:
                if should_trip_circuit(exc):
                    self._failures += 1
                    if is_probe:
                        self._transition(CircuitState.OPEN)
                        self._opened_at = monotonic()
                        self._probe_in_flight = False
                    elif self._state == CircuitState.CLOSED and self._failures >= self.failure_threshold:
                        self._transition(CircuitState.OPEN)
                        self._opened_at = monotonic()
                else:
                    if is_probe:
                        self._transition(CircuitState.CLOSED, reason="non_trip_error_probe_closed")
                        self._failures = 0
                        self._opened_at = None
                        self._probe_in_flight = False
                    logger.info(
                        "Circuit breaker %s: non-trip failure %s (state unchanged)",
                        self.name,
                        type(exc).__name__,
                    )
            raise

        async with self._lock:
            if is_probe:
                self._transition(CircuitState.CLOSED)
                self._failures = 0
                self._opened_at = None
                self._probe_in_flight = False
            elif self._state == CircuitState.CLOSED:
                self._failures = 0
        return result