import json
from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from vibepod_board.api.deps import SettingsDep, worker_timing
from vibepod_board.api.models import PauseRequest, ProjectCreate, ProjectSettings, ProjectUpdate
from vibepod_board.api.responses import Item, Items
from vibepod_board.auth import AccessDep, AdminDep
from vibepod_board.bundle import ImportProjectRequest
from vibepod_board.db import SessionDep
from vibepod_board.schemas import (
    AutomationState,
    DeletedProject,
    ImportProjectResult,
    Project,
    ProjectListItem,
)
from vibepod_board.services import automation, project_archive, projects, transfer

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.get("")
def list_projects(
    session: SessionDep,
    access: AccessDep,
    archived: bool = False,
    include_archived: Annotated[bool, Query(alias="includeArchived")] = False,
) -> Items[ProjectListItem]:
    """Active projects by default; `archived=true` lists only archived projects and
    `includeArchived=true` both."""
    selected = None if include_archived else archived
    return Items(items=projects.list_projects(session, access, selected))


@router.post("/import", responses={200: {"model": ImportProjectResult}})
def import_project(
    body: ImportProjectRequest, session: SessionDep, _admin: AdminDep, response: Response
) -> ImportProjectResult:
    result = transfer.import_project(session, body.bundle, body.replace_existing)
    response.status_code = status.HTTP_200_OK if result.replaced else status.HTTP_201_CREATED
    return result


@router.post("", status_code=status.HTTP_201_CREATED)
def create_project(body: ProjectCreate, session: SessionDep, _admin: AdminDep) -> Item[Project]:
    return Item(
        item=projects.create_project(
            session,
            body.key,
            body.title,
            body.summary,
            body.required_approvals,
            body.max_review_rounds,
        )
    )


@router.patch("/{project_id}")
def update_project(
    project_id: str, body: ProjectUpdate, session: SessionDep, _admin: AdminDep
) -> Item[Project]:
    changes = body.model_dump(exclude_unset=True, by_alias=False)
    return Item(item=projects.update_project(session, project_id, **changes))


@router.patch("/{project_id}/settings")
def update_project_settings(
    project_id: str, body: ProjectSettings, session: SessionDep, access: AccessDep
) -> Item[Project]:
    """The review settings, for any caller with access to the project: `requiredApprovals`
    (1 to 5) and `maxReviewRounds` (1 to 10)."""
    changes = body.model_dump(exclude_unset=True, by_alias=False)
    return Item(item=projects.update_project_settings(session, access, project_id, **changes))


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


@router.post("/{project_id}/archive")
def archive_project(
    project_id: str, session: SessionDep, admin: AdminDep, settings: SettingsDep
) -> Item[Project]:
    """Archives the project and pauses its automation. Refused (409) while workers of the
    project are connected or its tasks are claimed or under review."""
    return Item(
        item=project_archive.archive_project(session, admin, project_id, worker_timing(settings))
    )


@router.post("/{project_id}/restore")
def restore_project(project_id: str, session: SessionDep, admin: AdminDep) -> Item[Project]:
    """Brings an archived project back; its automation stays paused until resumed."""
    return Item(item=project_archive.restore_project(session, admin, project_id))


@router.delete("/{project_id}")
def delete_project(
    project_id: str,
    session: SessionDep,
    admin: AdminDep,
    confirm_key: Annotated[str | None, Query(alias="confirmKey")] = None,
) -> DeletedProject:
    """Deletes an archived project for good, with all its tasks, cards, notes, workers and
    token links. Active projects are refused (409). `confirmKey`, when given, must be the
    project key."""
    return project_archive.delete_project(session, admin, project_id, confirm_key)


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
