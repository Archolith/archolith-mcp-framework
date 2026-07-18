# Changelog

## 0.3.0 - 2026-07-18

- Added reusable async MCP call execution with timeout, error mapping, and consumer-provided telemetry recording.
- Added policy-free JSON resource and MCP tool registration contracts.
- Added an async circuit breaker with upstream failure classification and transition hooks.
- Added ASGI request-id context middleware.
## 0.2.0 - 2026-07-18

- Renamed the public package to `archolith-mcp-framework`.
- Added `archolith_mcp_framework` as the primary import namespace.
- Retained `cth_mcp_framework` as a forwarding compatibility package.
- Added public README and MIT license.
