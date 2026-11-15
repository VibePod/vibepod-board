"""A browsable reference for the MCP endpoint: the live tool catalog as JSON, and the page
that renders it."""

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from vibepod_board import permissions

router = APIRouter(tags=["system"])

PAGE = Path(__file__).resolve().parents[1] / "static" / "mcp-docs.html"


def _wire(model: BaseModel) -> dict[str, Any]:
    """A tool or resource as MCP clients receive it, without unset fields and `_meta`."""
    data = model.model_dump(mode="json", by_alias=True, exclude_none=True)
    data.pop("_meta", None)
    return data


def _needs(tool_name: str) -> dict[str, str] | None:
    """The token permission a tool needs, or nothing for a tool every token may call."""
    needs = permissions.TOOL_PERMISSIONS.get(tool_name)
    if not isinstance(needs, permissions.Requirement):
        return None
    return {
        "area": needs.area.value,
        "label": permissions.POLICY[needs.area].label,
        "level": needs.level.value,
    }


@router.get("/api/mcp-tools")
async def mcp_tools(request: Request) -> dict[str, Any]:
    """Every tool and resource the MCP server lists, read from the server on each request,
    and the token permission each tool needs."""
    server = request.app.state.mcp_server
    tools = await server.list_tools()
    return {
        "server": {"name": server.name, "version": server.version},
        "endpoint": "/mcp",
        "transport": "streamable-http",
        "auth": "bearer",
        "tools": [_wire(tool.to_mcp_tool()) for tool in tools],
        "permissions": {tool.name: needs for tool in tools if (needs := _needs(tool.name))},
        "resources": [
            _wire(resource.to_mcp_resource()) for resource in await server.list_resources()
        ],
    }


@router.get("/mcp-docs", include_in_schema=False)
def mcp_docs() -> FileResponse:
    return FileResponse(PAGE, media_type="text/html")
