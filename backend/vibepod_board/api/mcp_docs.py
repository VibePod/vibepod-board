"""A browsable reference for the MCP endpoint: the live tool catalog as JSON."""

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter(tags=["system"])


def _wire(model: BaseModel) -> dict[str, Any]:
    """A tool or resource as MCP clients receive it, without unset fields and `_meta`."""
    data = model.model_dump(mode="json", by_alias=True, exclude_none=True)
    data.pop("_meta", None)
    return data


@router.get("/api/mcp-tools")
async def mcp_tools(request: Request) -> dict[str, Any]:
    """Every tool and resource the MCP server lists, read from the server on each request."""
    server = request.app.state.mcp_server
    return {
        "server": {"name": server.name, "version": server.version},
        "endpoint": "/mcp",
        "transport": "streamable-http",
        "auth": "bearer",
        "tools": [_wire(tool.to_mcp_tool()) for tool in await server.list_tools()],
        "resources": [
            _wire(resource.to_mcp_resource()) for resource in await server.list_resources()
        ],
    }
