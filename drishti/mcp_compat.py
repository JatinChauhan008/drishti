import inspect
import json
from collections.abc import Callable
from typing import Any, Union, get_args, get_origin

import anyio
from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from drishti.logbus import log_error


try:
    from mcp.server import MCPServer
except ImportError:
    MCPServer = None

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    FastMCP = None


def create_mcp(name: str):
    if MCPServer is not None:
        return MCPServer(name)
    if FastMCP is not None:
        return FastMCP(name)
    return LocalMCP(name)


class LocalMCP:
    """FastMCP-compatible fallback for MCP packages without FastMCP."""

    def __init__(self, name: str):
        self.name = name
        self._tools: dict[str, Callable[..., Any]] = {}

    def tool(self) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def register(fn: Callable[..., Any]) -> Callable[..., Any]:
            self._tools[fn.__name__] = fn
            return fn

        return register

    async def _list_tools(self, _ctx, _params) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name=name,
                    description=inspect.getdoc(fn) or "",
                    inputSchema=_schema_for_function(fn),
                )
                for name, fn in self._tools.items()
            ]
        )

    async def _call_tool(self, _ctx, params: types.CallToolRequestParams) -> types.CallToolResult:
        fn = self._tools.get(params.name)
        if fn is None:
            return _tool_result({"error": True, "status": "unknown_tool", "detail": f"Unknown tool: {params.name}"}, True)

        try:
            result = fn(**(params.arguments or {}))
        except Exception as exc:
            log_error("tool_dispatch_error", exc, tool=params.name, arguments=params.arguments)
            result = {"error": True, "status": "internal_error", "detail": f"{params.name} failed: {exc}"}

        return _tool_result(result, isinstance(result, dict) and bool(result.get("error")))

    async def _run_stdio(self) -> None:
        server = Server(self.name, on_list_tools=self._list_tools, on_call_tool=self._call_tool)
        async with stdio_server() as streams:
            await server.run(*streams, server.create_initialization_options())

    def run(self) -> None:
        anyio.run(self._run_stdio)


def _schema_for_function(fn: Callable[..., Any]) -> dict[str, Any]:
    signature = inspect.signature(fn)
    properties = {}
    required = []

    for name, parameter in signature.parameters.items():
        if parameter.kind not in {parameter.POSITIONAL_OR_KEYWORD, parameter.KEYWORD_ONLY}:
            continue
        properties[name] = _schema_for_type(parameter.annotation)
        if parameter.default is inspect.Parameter.empty:
            required.append(name)

    return {"type": "object", "properties": properties, "required": required}


def _schema_for_type(annotation: Any) -> dict[str, Any]:
    if annotation is inspect.Signature.empty:
        return {}

    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is list:
        item_type = args[0] if args else Any
        return {"type": "array", "items": _schema_for_type(item_type)}
    if origin is dict:
        return {"type": "object"}
    if origin is Union:
        non_none = [arg for arg in args if arg is not type(None)]
        return _schema_for_type(non_none[0]) if non_none else {}

    type_map = {str: "string", int: "integer", float: "number", bool: "boolean", dict: "object", list: "array"}
    schema_type = type_map.get(annotation)
    return {"type": schema_type} if schema_type else {}


def _tool_result(result: Any, is_error: bool = False) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False, default=str))],
        structuredContent=result if isinstance(result, dict) else None,
        isError=is_error,
    )
