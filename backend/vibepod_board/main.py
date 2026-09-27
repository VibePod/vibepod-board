"""Application factory: REST routers, the FastMCP server at /mcp, and the built client."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.routing import Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from vibepod_board.api import board, documents, github, ideas, projects, system, tokens
from vibepod_board.auth import AdminSessionManager
from vibepod_board.config import Settings, get_settings
from vibepod_board.db import create_db_engine, get_engine, migrate, set_engine
from vibepod_board.errors import BoardError, NotFound
from vibepod_board.mcp_server import create_mcp_server

MiB = 1024 * 1024
BODY_LIMIT = 2 * MiB
IMPORT_BODY_LIMIT = 10 * MiB
IMPORT_PATH = "/api/projects/import"


class BodyTooLarge(Exception):
    def __init__(self, path: str) -> None:
        self.path = path


def _limit_message(path: str) -> str:
    # The import route accepts a larger body than the rest of the API, so the limit named
    # in the message has to match the route that was called.
    if path.startswith(IMPORT_PATH):
        return "Project import file must be 10 MiB or smaller"
    return "Request body must be 2 MiB or smaller"


class BodyLimitMiddleware:
    """Rejects oversized request bodies with 413, before or while they are read."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            await self.app(scope, receive, send)
            return

        path = scope["path"]
        limit = IMPORT_BODY_LIMIT if path.startswith(IMPORT_PATH) else BODY_LIMIT
        headers = dict(scope["headers"])
        declared = headers.get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            response = JSONResponse({"error": _limit_message(path)}, status_code=413)
            await response(scope, receive, send)
            return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise BodyTooLarge(path)
            return message

        await self.app(scope, limited_receive, send)


def _validation_details(error: RequestValidationError) -> dict[str, object]:
    """Roughly zod's `flatten()`: body-level messages plus messages per field."""
    form_errors: list[str] = []
    field_errors: dict[str, list[str]] = {}
    for issue in error.errors():
        location = [str(part) for part in issue["loc"][1:]]
        if location:
            field_errors.setdefault(".".join(location), []).append(issue["msg"])
        else:
            form_errors.append(issue["msg"])
    return {"formErrors": form_errors, "fieldErrors": field_errors}


def _install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(BoardError)
    async def board_error(_request: Request, exc: BoardError) -> JSONResponse:
        return JSONResponse({"error": exc.message}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            {"error": "Validation failed", "details": _validation_details(exc)}, status_code=400
        )

    @app.exception_handler(BodyTooLarge)
    async def body_too_large(_request: Request, exc: BodyTooLarge) -> JSONResponse:
        return JSONResponse({"error": _limit_message(exc.path)}, status_code=413)


def _mount_client(app: FastAPI, public_dir: Path) -> None:
    """Serves the Vite build; any other non-API path falls back to the SPA entry point."""
    index = public_dir / "index.html"
    assets = public_dir / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str) -> FileResponse:
        if path.startswith(("api/", "mcp")):
            raise NotFound(f"Not found: /{path}")
        candidate = (public_dir / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(public_dir.resolve()):
            return FileResponse(candidate)
        return FileResponse(index)


def create_app(
    settings: Settings | None = None,
    sessions: AdminSessionManager | None = None,
    run_migrations: bool | None = None,
    github_transport: httpx.BaseTransport | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    mcp_app = create_mcp_server(settings, github_transport).http_app(
        path="/mcp", stateless_http=True, json_response=True
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            get_engine()
        except RuntimeError:
            set_engine(create_db_engine(settings.sqlalchemy_url, settings.pool_size))
        if settings.auto_migrate if run_migrations is None else run_migrations:
            migrate(get_engine())
        async with mcp_app.lifespan(app):
            yield

    app = FastAPI(
        title="vibepod-board",
        summary="Planning board for refining ideas, syncing ready work, and exposing plans "
        "through UI, API, and MCP.",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = settings
    # Tests swap in an httpx.MockTransport; None talks to api.github.com.
    app.state.github_transport = github_transport
    app.state.sessions = sessions or AdminSessionManager(
        settings.admin_username, settings.admin_password
    )
    app.add_middleware(BodyLimitMiddleware)
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
    )
    _install_error_handlers(app)

    for module in (system, projects, ideas, github, board, documents, tokens):
        app.include_router(module.router)
    # FastMCP ships a Starlette app whose middleware verifies the bearer token. Routing
    # /mcp to the whole app keeps that middleware, and unlike a mount it does not turn the
    # endpoint into /mcp/.
    app.router.routes.append(Route("/mcp", endpoint=mcp_app, methods=["GET", "POST", "DELETE"]))

    if settings.public_dir:
        _mount_client(app, settings.public_dir)
    return app
