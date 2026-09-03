# Changelog

## Unreleased

- Added an audited, manually dispatchable trusted-publishing workflow for the existing `v0.2.0`
  tag, including tests, artifact inspection, clean-wheel import smoke tests, checksums, PyPI upload,
  and an immutable GitHub release record.

## 0.3.0 - 2026-09-03

- Added `resilience.py`: `should_trip_circuit()` classifies upstream-health failures (timeouts,
  httpx transport errors, 429/5xx, duck-typed `status_code`, name-matched SDK error classes) and
  deliberately excludes 4xx client errors, so a caller's own bad request cannot open a circuit.
- Added `CircuitBreaker`: async CLOSED/OPEN/HALF_OPEN breaker whose half-open state admits exactly
  one probe, so a recovering upstream is not hit by every queued caller at once. A failed probe
  re-opens the circuit and restarts the cooldown. Transition-hook exceptions are contained and
  never propagate into the call path.
- Added `http.py`: `RequestContextMiddleware` binds an `x-request-id` per ASGI request (reusing an
  incoming header or minting one), exposes it through `get_request_id()`, and echoes it on the
  response without overwriting an id the application already set. Non-HTTP scopes pass through
  untouched and the context var is reset even when the application raises.
- Declared `httpx>=0.28.1,<1` explicitly; `resilience.py` imports it directly rather than relying on
  it arriving transitively through fastmcp.

Both modules are opt-in: `create_gateway_server()` does not wire them, so existing servers are
unchanged until they adopt them.

## 0.2.0 - 2026-07-18

- Renamed the public package to `archolith-mcp-framework`.
- Added `archolith_mcp_framework` as the primary import namespace.
- Retained `cth_mcp_framework` as a forwarding compatibility package.
- Added public README and MIT license.
