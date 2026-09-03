"""WorkspaceSearchTransform — BM25SearchTransform with workspace defaults.

Differences from stock BM25SearchTransform:
- max_results=10 (most MCP servers have 3-25 actions)
- Custom search_result_serializer that renders compact tool schemas,
matching the output format of the old _build_action_schema() helpers
- Optional schema abbreviation: after the first list_tools call, pinned
  (always_visible) tool schemas are collapsed to name + 1-line description
  + required param names only, saving ~60% per-turn schema tokens.
"""

from __future__ import annotations

import json
import re
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


_FIRST_SENTENCE_RE = re.compile(r"^(.*?(?:[.!?](?:\s|$)))", re.DOTALL)


def _abbreviate_tool(tool: Tool) -> Tool:
    """Return a copy of *tool* with collapsed schema for list_tools display.

    Keeps: name, first sentence of description, required params (with their
    original property schema, so the result stays valid JSON Schema).
    Drops: optional parameters entirely, defaults, full description.

    A `required` entry with no matching `properties` key is legal JSON Schema
    but self-inconsistent (nothing constrains the value or even documents its
    type) - earlier versions of this function did exactly that, which some
    MCP clients handle poorly for the *output* side of the same tool once its
    input schema has been served this way. Keeping required properties'
    schema intact avoids emitting that shape at all.
    """
    mcp_tool = tool.to_mcp_tool()
    schema = mcp_tool.inputSchema or {}
    properties: dict[str, Any] = schema.get("properties", {})
    required_names: list[str] = schema.get("required", [])

    kept_required = [n for n in required_names if n in properties]
    abbreviated_params: dict[str, Any] = {
        "type": "object",
        "properties": {n: properties[n] for n in kept_required},
    }
    if kept_required:
        abbreviated_params["required"] = kept_required

    desc = tool.description or ""
    first_sentence = _FIRST_SENTENCE_RE.match(desc)
    short_desc = first_sentence.group(1).strip() if first_sentence else desc.split("\n")[0].strip()

    return tool.model_copy(update={
        "description": short_desc,
        "parameters": abbreviated_params,
    })


class WorkspaceSearchTransform(BM25SearchTransform):
    """BM25SearchTransform with workspace-standard defaults.

    - max_results=10 (most servers have 3-25 actions)
    - search_result_serializer renders compact schemas
    - abbreviate_visible: after warmup_calls list_tools invocations,
      pinned (always_visible) tool schemas are collapsed to name +
      1-line description + required param names, saving ~60% per-turn
      schema tokens.
    """

    def __init__(
        self,
        *,
        max_results: int = 10,
        always_visible: list[str] | None = None,
        search_tool_name: str = "search_tools",
        call_tool_name: str = "call_tool",
        abbreviate_visible: bool = False,
        warmup_calls: int = 1,
    ) -> None:
        super().__init__(
            max_results=max_results,
            always_visible=always_visible,
            search_tool_name=search_tool_name,
            call_tool_name=call_tool_name,
            search_result_serializer=compact_search_serializer,
        )
        self._abbreviate_visible = abbreviate_visible
        self._warmup_calls = warmup_calls
        self._call_count = 0

    async def transform_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        """Replace the catalog with pinned + synthetic search/call tools.

        After warmup_calls, pinned tool schemas are abbreviated if
        abbreviate_visible is enabled.
        """
        self._call_count += 1
        pinned = [t for t in tools if t.name in self._always_visible]

        if (
            self._abbreviate_visible
            and self._call_count > self._warmup_calls
        ):
            pinned = [_abbreviate_tool(t) for t in pinned]

        return [*pinned, self._make_search_tool(), self._make_call_tool()]
