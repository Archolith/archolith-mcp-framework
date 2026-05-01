"""WorkspaceSearchTransform — BM25SearchTransform with cth.* workspace defaults.

Differences from stock BM25SearchTransform:
- max_results=10 (most cth.* servers have 3-25 actions)
- Custom search_result_serializer that renders compact tool schemas,
  matching the output format of the old _build_action_schema() helpers
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from fastmcp.server.transforms.search import BM25SearchTransform
from fastmcp.tools.base import Tool


def _compact_tool_schema(tool: Tool) -> dict[str, Any]:
    """Render a tool schema in a compact format similar to the old gateway _build_action_schema().

    Returns a dict with: name, description, required (list of param names),
    optional (dict of param_name -> default_value).
    """
    mcp_tool = tool.to_mcp_tool()
    schema = mcp_tool.inputSchema or {}
    properties: dict[str, Any] = schema.get("properties", {})
    required_names: list[str] = schema.get("required", [])

    required: list[str] = []
    optional: dict[str, Any] = {}

    for param_name, param_schema in properties.items():
        if param_name in required_names:
            required.append(param_name)
        else:
            # Extract default if available, otherwise None
            default = param_schema.get("default", None)
            optional[param_name] = default

    result: dict[str, Any] = {"name": tool.name}
    if mcp_tool.description:
        result["description"] = mcp_tool.description
    if required:
        result["required"] = required
    if optional:
        result["optional"] = optional

    return result


def compact_search_serializer(tools: Sequence[Tool]) -> list[dict[str, Any]]:
    """Serialize search results as compact schema dicts.

    This replaces the default serializer (which dumps full JSON Schema)
    with a lighter format that matches what the old gateway help system produced.
    """
    return [_compact_tool_schema(t) for t in tools]


class WorkspaceSearchTransform(BM25SearchTransform):
    """BM25SearchTransform with workspace-standard defaults.

    - max_results=10 (most servers have 3-25 actions)
    - search_result_serializer renders compact schemas
    """

    def __init__(
        self,
        *,
        max_results: int = 10,
        always_visible: list[str] | None = None,
        search_tool_name: str = "search_tools",
        call_tool_name: str = "call_tool",
    ) -> None:
        super().__init__(
            max_results=max_results,
            always_visible=always_visible,
            search_tool_name=search_tool_name,
            call_tool_name=call_tool_name,
            search_result_serializer=compact_search_serializer,
        )
