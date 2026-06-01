# cth.mcp.framework — Agent Docs

Read this directory before changing `cth.mcp.framework`.

## Files

| File | Purpose |
|------|---------|
| `architecture.md` | Library purpose, module layout, extension points, and tests |

## Maintenance Rules

- Update `architecture.md` when adding or removing exported helpers, middleware, mixins, or server-factory behavior.
- Document any public contract change to `create_gateway_server()`, `BaseGatewayServer`, `ToolResponse`, or the
  synthetic tool-search surface.
- Run the focused framework tests when behavior changes touch the library surface:
  `tests/test_framework.py` and `tests/test_compact_mixin.py`.
