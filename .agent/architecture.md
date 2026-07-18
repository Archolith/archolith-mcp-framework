# Architecture: archolith-mcp-framework

## Overview

`archolith-mcp-framework` is a small public library for MCP servers built on FastMCP. It standardizes the
server bootstrap path, synthetic tool-discovery surface, middleware defaults, response helpers, and a set of
mixins that other MCP repos can reuse instead of re-implementing the same plumbing.

## Primary Responsibilities

- create FastMCP servers with the workspace-standard search transform
- provide default middleware for error handling and timing
- expose a base class for OOP-style gateway servers
- provide reusable mixins for path validation, chunked I/O, git helpers, auditing, and compact-mode behavior
- keep the public response/error shape consistent across servers that adopt the framework
- provide a shared async-job registry and a duration-stats ETA engine so long-running tools (deploy, build)
  can advise a single wait-then-check instead of tight, token-expensive polling
- provide policy-free execution, registration, resilience, and request-correlation primitives for consumers

## Module Layout

| Module | Role |
|--------|------|
| `src/archolith_mcp_framework/server.py` | `create_gateway_server()` factory that wires FastMCP, transforms, and middleware |
| `src/archolith_mcp_framework/transforms.py` | `WorkspaceSearchTransform` and related search/call serialization behavior |
| `src/archolith_mcp_framework/middleware.py` | Shared middleware for error handling, timing, and structured logging |
| `src/archolith_mcp_framework/base.py` | `BaseGatewayServer` convenience class on top of the factory |
| `src/archolith_mcp_framework/response.py` | `ToolResponse` plus shared error-code constants |
| `src/archolith_mcp_framework/runner.py` | Stdio server bootstrap helper |
| `src/archolith_mcp_framework/duration_stats.py` | Records per `tool+bucket` job durations (rolling window, JSON-persisted) and returns p50/p90/samples estimates with cold-start defaults |
| `src/archolith_mcp_framework/jobs.py` | Shared background-job registry (`start_job`/`job_status`/`job_eta`/`cancel_job`) with ETA hints, a `last_progress_ts` heartbeat (stuck vs slow), and an optional timeout-kill watchdog |
| `src/archolith_mcp_framework/call_execution.py` | Async call timeout/error boundary plus a consumer-supplied telemetry recorder protocol |
| `src/archolith_mcp_framework/contracts.py` | Policy-free JSON resource and MCP tool registration contracts |
| `src/archolith_mcp_framework/resilience.py` | Failure classifier and async circuit breaker with optional transition hook |
| src/archolith_mcp_framework/http.py | ASGI request-id context middleware and lookup helpers |
| src/archolith_mcp_framework/mixins/ | Reusable behavior slices: paths, chunked I/O, git, audit, compact mode, **job control** |
| `src/archolith_mcp_framework/mixins/job_control.py` | `JobControlMixin` — opt-in polling support for OOP servers: auto-registers `<prefix>job_status`/`<prefix>job_cancel`, plus `start_job`/`started_message` helpers that apply per-server ETA defaults |

## Async Jobs + ETA

Long-running tools start a background job via `start_job(label, fn, *args, streaming=..., eta_tool=...,
eta_bucket=..., eta_default=..., timeout_s=...)` and return a job ID immediately, then poll `job_status`.

- **Bucketing**: durations are keyed by `tool + bucket`, where bucket is a low-cardinality runtime
  discriminator (deploy target, gradle task) — not the raw command — so percentiles are meaningful.
- **Recording**: only successful runs are recorded (failures/timeouts skew the median).
- **Self-describing**: `job_status` for a running ETA-tracked job reports `ETA: p50/p90` and a stuck-vs-slow
  line, so the client waits the p50 once and checks once instead of polling.
- Consumers: `yawn.vps` (deploy/canary) and the agentsmith gradle server. `yawn.vps/vps/jobs.py` is a thin
  re-export of `jobs.py` for backward compatibility.

### Polling vs non-polling servers (OOP)

Class-based servers opt into polling by composing `JobControlMixin` before `BaseGatewayServer`:

```python
class MyServer(JobControlMixin, BaseGatewayServer):
    name = "myserver"
    job_tool_prefix = "my_"
    eta_defaults = {"build": 240}
```

This auto-registers `my_job_status` / `my_job_cancel` and gives `self.start_job(..., eta_bucket="build")`
+ `self.started_message(job_id)` (the wait-once-then-check message with ETA). A server that does not run
long tasks simply omits the mixin and carries no job tools or registry. The mixin delegates to the
module-level `jobs.py` registry (not an instance-scoped one): each MCP server is its own stdio process, so
the process-global registry is effectively per-server. Existing functional servers (`yawn.vps`, gradle) keep
using `create_gateway_server` directly; the mixin is the path for new OOP servers.

## Request Path

1. A consumer repo creates a server with `create_gateway_server()` or subclasses `BaseGatewayServer`.
2. The framework installs `WorkspaceSearchTransform`, which collapses the full tool catalog into a discovery
   surface centered on `search_tools` and `call_tool`.
   - `schema_abbreviated=True` abbreviates pinned `always_visible` tool schemas after the initial warmup
     `list_tools` call, keeping the full schema visible once while reducing repeated prompt tokens.
3. Middleware runs around every tool invocation.
   - `ErrorHandlingMiddleware` converts uncaught exceptions into structured tool-safe text responses.
   - `TimingMiddleware` records call duration.
   - `StructuredLoggingMiddleware` is available when a server wants richer request/result logging.
4. Consumer repos register project-specific tools on the configured FastMCP server.

## Design Boundaries

- This repo is infrastructure, not a product server. It should stay narrow and reusable.
- Avoid embedding project-specific business logic here.
- Public exports in `__init__.py` are the compatibility surface other repos depend on; update docs when that
  surface changes.
- `archolith_mcp_framework` is the primary import. `cth_mcp_framework` remains a forwarding compatibility package.

## Test Surface

| Test file | Coverage focus |
|-----------|----------------|
| `tests/test_framework.py` | Transform wiring, middleware defaults, custom middleware behavior, lifespan passthrough, git mixin behavior |
| `tests/test_compact_mixin.py` | Compact-mode behavior and mixin-level edge cases |
| `tests/test_duration_stats.py` | Percentile math, cold-start defaults, rolling window, JSON persistence, corrupt-file handling |
| `tests/test_jobs.py` | Job lifecycle, ETA recording (success-only), heartbeat/ETA in status, timeout watchdog, legacy-dict compatibility |
