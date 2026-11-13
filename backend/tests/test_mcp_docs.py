"""The MCP reference: the public tool catalog, the page that shows it, and bearer auth in
the OpenAPI spec."""

from dataclasses import replace
from pathlib import Path

import httpx
from fastapi.testclient import TestClient
from fastmcp import Client
from sqlmodel import Session

from vibepod_board.api.system import MCP_INFO_TOOLS
from vibepod_board.config import Settings
from vibepod_board.main import create_app
from vibepod_board.services import projects, tokens


def test_catalog_describes_every_tool_without_authentication(client: TestClient) -> None:
    response = client.get("/api/mcp-tools")
    assert response.status_code == 200
    catalog = response.json()

    assert catalog["server"] == {"name": "vibepod-board", "version": "0.1.0"}
    assert catalog["endpoint"] == "/mcp"
    assert catalog["transport"] == "streamable-http"
    assert catalog["auth"] == "bearer"

    tools = {tool["name"]: tool for tool in catalog["tools"]}
    assert len(tools) == len(catalog["tools"])
    assert sorted(tools) == sorted(MCP_INFO_TOOLS)
    for tool in tools.values():
        assert tool["title"] and tool["description"], tool["name"]
        assert tool["inputSchema"]["type"] == "object", tool["name"]
        assert "_meta" not in tool, tool["name"]
    assert tools["list_projects"]["annotations"] == {"readOnlyHint": True}
    assert tools["delete_project"]["annotations"] == {"destructiveHint": True}
    assert "annotations" not in tools["archive_project"]

    assert catalog["resources"] == [
        {
            "name": "board-state",
            "title": "VibePod Board State",
            "uri": "vibepod-board://state",
            "description": "Full scoped vibepod-board state as JSON.",
            "mimeType": "application/json",
        }
    ]


async def test_catalog_matches_what_an_mcp_client_lists(server_url: str, session: Session) -> None:
    project = projects.create_project(session, "APP", "App")
    token = tokens.create_token(session, "Agent", [project.id]).token

    async with Client(server_url, auth=token) as client:
        listed = [
            tool.model_dump(mode="json", by_alias=True, exclude_none=True)
            for tool in await client.list_tools()
        ]
    async with httpx.AsyncClient() as http:
        catalog = (await http.get(server_url.removesuffix("/mcp") + "/api/mcp-tools")).json()

    assert catalog["tools"] == [
        {key: value for key, value in tool.items() if key != "_meta"} for tool in listed
    ]


def test_page_is_served_with_and_without_a_built_client(
    client: TestClient, settings: Settings, tmp_path: Path
) -> None:
    page = client.get("/mcp-docs")
    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert "/api/mcp-tools" in page.text
    assert "/mcp-docs" not in client.get("/openapi.json").json()["paths"]

    # With a built client the SPA fallback answers every other path; the page still wins.
    (tmp_path / "index.html").write_text("<title>board client</title>")
    built = replace(settings, public_dir=tmp_path)
    with TestClient(create_app(built, run_migrations=False)) as with_client:
        assert with_client.get("/projects").text == "<title>board client</title>"
        assert with_client.get("/mcp-docs").text == page.text
