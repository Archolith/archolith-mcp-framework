# Architecture: cth.mcp.framework

## Overview

`cth.mcp.framework` is a small shared library for workspace MCP servers built on FastMCP. It standardizes the
server bootstrap path, synthetic tool-discovery surface, middleware defaults, response helpers, and a set of
mixins that other MCP repos can reuse instead of re-implementing the same plumbing.

## Primary Responsibilities

- create FastMCP servers with the workspace-standard search transform
- provide default middleware for error handling and timing
- expose a base class for OOP-style gateway servers
- provide reusable mixins for path validation, chunked I/O, git helpers, auditing, and compact-mode behavior
- keep the public response/error shape consistent across servers that adopt the framework

## Module Layout

| Module | Role |
|--------|------|
| `src/cth_mcp_framework/server.py` | `create_gateway_server()` factory that wires FastMCP, transforms, and middleware |
| `src/cth_mcp_framework/transforms.py` | `WorkspaceSearchTransform` and related search/call serialization behavior |
| `src/cth_mcp_framework/middleware.py` | Shared middleware for error handling, timing, and structured logging |
| `src/cth_mcp_framework/base.py` | `BaseGatewayServer` convenience class on top of the factory |
| `src/cth_mcp_framework/response.py` | `ToolResponse` plus shared error-code constants |
| `src/cth_mcp_framework/runner.py` | Stdio server bootstrap helper |
| `src/cth_mcp_framework/mixins/` | Reusable behavior slices: paths, chunked I/O, git, audit, compact mode |

## Request Path

1. A consumer repo creates a server with `create_gateway_server()` or subclasses `BaseGatewayServer`.
2. The framework installs `WorkspaceSearchTransform`, which collapses the full tool catalog into a discovery
   surface centered on `search_tools` and `call_tool`.
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

## Test Surface

| Test file | Coverage focus |
|-----------|----------------|
| `tests/test_framework.py` | Transform wiring, middleware defaults, custom middleware behavior, lifespan passthrough, git mixin behavior |
| `tests/test_compact_mixin.py` | Compact-mode behavior and mixin-level edge cases |
