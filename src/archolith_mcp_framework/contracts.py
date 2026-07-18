"""Small, policy-free registration contracts for MCP tools and JSON resources."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from functools import wraps
from typing import Any, Awaitable


def _json_default(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def render_json(payload: dict[str, Any]) -> str:
    """Render a stable compact JSON response for an MCP resource or tool."""

    return json.dumps(payload, separators=(",", ":"), sort_keys=True, default=_json_default)


class JsonResource(ABC):
    """Registration and error-shape contract for JSON MCP resources.

    Consumers retain ownership of authorization, backends, and telemetry by overriding
    ``execute`` when they need policy around ``build_payload``.
    """

    uri: str
    name: str
    description: str
    mime_type = "application/json"

    @property
    def kind(self) -> str:
        return "resource_template" if "{" in self.uri else "resource"

    @property
    def operation(self) -> str:
        return self.uri

    def error_mapper(self, error_text: str) -> str:
        return render_json({"ok": False, "resource": self.operation, "error": {"message": error_text}})

    async def execute(self, *args: Any, **kwargs: Any) -> str:
        payload = await self.build_payload(*args, **kwargs)
        return render_json(payload)

    def call_payload(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        if kwargs:
            return dict(kwargs)
        if not args:
            return {}
        return {"args": list(args)}

    @abstractmethod
    async def build_payload(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Return the JSON payload to render for this resource."""

    @abstractmethod
    def endpoint(self, *args: Any, **kwargs: Any) -> Awaitable[dict[str, Any]]:
        """Typed signature anchor used for MCP registration."""

    def register(self, mcp: Any) -> None:
        target = self.endpoint

        @wraps(target)
        async def handler(*args: Any, **kwargs: Any) -> str:
            return await self.execute(*args, **kwargs)

        mcp.resource(
            self.uri,
            name=self.name,
            description=self.description,
            mime_type=self.mime_type,
        )(handler)


class McpTool:
    """Registration and metadata contract for MCP tools without product policy."""

    name: str
    description: str
    response_kind = "text"

    @property
    def operation(self) -> str:
        return self.name

    def error_mapper(self, error_text: str) -> str | None:
        return None

    def call_payload(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        if kwargs:
            return dict(kwargs)
        if not args:
            return {}
        return {"args": list(args)}

    def timeout_for(self, *args: Any, **kwargs: Any) -> int:
        return 120

    def endpoint(self, *args: Any, **kwargs: Any) -> Awaitable[str]:
        """Typed MCP signature plus tool implementation."""

        raise NotImplementedError(f"{type(self).__name__} must implement endpoint()")

    async def execute(self, *args: Any, **kwargs: Any) -> str:
        return await self.endpoint(*args, **kwargs)

    def register(self, mcp: Any) -> None:
        target = self.endpoint

        @wraps(target)
        async def handler(*args: Any, **kwargs: Any) -> str:
            return await self.execute(*args, **kwargs)

        handler.__name__ = self.name
        handler.__qualname__ = self.name
        mcp.tool()(handler)