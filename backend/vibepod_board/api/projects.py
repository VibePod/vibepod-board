import json

from fastapi import APIRouter, Response, status

from vibepod_board.api.models import PauseRequest, ProjectCreate, ProjectUpdate
from vibepod_board.api.responses import Item, Items
from vibepod_board.auth import AccessDep, AdminDep
from vibepod_board.bundle import ImportProjectRequest
from vibepod_board.db import SessionDep
from vibepod_board.schemas import AutomationState, ImportProjectResult, Project
from vibepod_board.services import automation, projects, transfer

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.get("")
def list_projects(session: SessionDep, access: AccessDep) -> Items[Project]:
    return Items(items=projects.list_projects(session, access))


@router.post("/import", responses={200: {"model": ImportProjectResult}})
def import_project(
    body: ImportProjectRequest, session: SessionDep, _admin: AdminDep, response: Response
) -> ImportProjectResult:
    result = transfer.import_project(session, body.bundle, body.replace_existing)
    response.status_code = status.HTTP_200_OK if result.replaced else status.HTTP_201_CREATED
    return result


@router.post("", status_code=status.HTTP_201_CREATED)
def create_project(body: ProjectCreate, session: SessionDep, _admin: AdminDep) -> Item[Project]:
    return Item(item=projects.create_project(session, body.key, body.title, body.summary))


@router.patch("/{project_id}")
def update_project(
    project_id: str, body: ProjectUpdate, session: SessionDep, _admin: AdminDep
) -> Item[Project]:
    changes = body.model_dump(exclude_unset=True, by_alias=False)
    return Item(item=projects.update_project(session, project_id, **changes))


@router.get("/{project_id}/export")
def export_project(project_id: str, session: SessionDep, _admin: AdminDep) -> Response:
    bundle = transfer.export_project(session, project_id)
    return Response(
        content=json.dumps(bundle.model_dump(mode="json"), indent=2),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{bundle.project.key}-project.json"'
        },
    )


@router.get("/{project_id}/automation")
def get_automation(project_id: str, session: SessionDep, access: AccessDep) -> AutomationState:
    return automation.get_automation(session, access, project_id)


@router.post("/{project_id}/automation/pause")
def pause_automation(
    project_id: str, session: SessionDep, access: AccessDep, body: PauseRequest | None = None
) -> AutomationState:
    """Refuses new claims in the project and tells its workers to pause; runs in progress
    finish."""
    return automation.pause_automation(session, access, project_id, body.reason if body else None)


@router.post("/{project_id}/automation/resume")
def resume_automation(project_id: str, session: SessionDep, access: AccessDep) -> AutomationState:
    return automation.resume_automation(session, access, project_id)
