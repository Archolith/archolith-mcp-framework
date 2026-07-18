# Archolith MCP Framework

`archolith-mcp-framework` is shared FastMCP infrastructure for MCP servers. It provides a server factory,
tool-discovery transform, common middleware, response helpers, reusable mixins, and asynchronous job support. It also provides policy-free MCP call execution, resource/tool registration, circuit breaking, and request correlation.

## Install

```toml
[project]
dependencies = [
    "archolith-mcp-framework @ git+https://github.com/Archolith/archolith-mcp-framework.git@v0.3.0",
]
```

## Use

```python
from archolith_mcp_framework import create_gateway_server, run_server

server = create_gateway_server("example")
run_server(server)
```

## Compatibility

The old `cth_mcp_framework` import remains available during migration and forwards to the same public API.
New projects should import `archolith_mcp_framework`.

## License

MIT. See [LICENSE](LICENSE).
