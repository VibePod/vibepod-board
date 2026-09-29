"""Pausing automation of a project from the board. While paused, claims are refused and every
worker of the project is told to pause with its heartbeat reply; resuming lifts both. A run
already in progress finishes; cancel it to stop it."""

from sqlmodel import Session, select

from vibepod_board.access import AccessContext, assert_can_access_project
from vibepod_board.schemas import AutomationState, now
from vibepod_board.services.common import (
    add_activity,
    normalize_optional_text,
    require_project,
    transactional,
)
from vibepod_board.services.history import actor_for
from vibepod_board.services.references import resolve_project_id
from vibepod_board.tables import ProjectRow

PAUSED_FROM_THE_BOARD = "Automation is paused from the board"


def automation_state(row: ProjectRow) -> AutomationState:
    return AutomationState(
        project_id=row.id,
        paused=row.automation_paused_at is not None,
        paused_at=row.automation_paused_at,
        reason=row.automation_paused_reason,
    )


def pause_reason(row: ProjectRow) -> str:
    return row.automation_paused_reason or PAUSED_FROM_THE_BOARD


def _project(session: Session, access: AccessContext, project: str) -> ProjectRow:
    project_id = resolve_project_id(session, project)
    assert_can_access_project(access, project_id)
    return require_project(session, project_id)


def get_automation(session: Session, access: AccessContext, project: str) -> AutomationState:
    return automation_state(_project(session, access, project))


def _locked(session: Session, access: AccessContext, project: str) -> ProjectRow:
    row = _project(session, access, project)
    return session.exec(
        select(ProjectRow)
        .where(ProjectRow.id == row.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()


@transactional
def pause_automation(
    session: Session, access: AccessContext, project: str, reason: str | None = None
) -> AutomationState:
    row = _locked(session, access, project)
    timestamp = now()
    if row.automation_paused_at is None:
        row.automation_paused_at = timestamp
    row.automation_paused_reason = normalize_optional_text(reason)
    add_activity(
        session,
        "automation.paused",
        f"{actor_for(session, access)} paused automation of {row.key}",
        timestamp,
    )
    return automation_state(row)


@transactional
def resume_automation(session: Session, access: AccessContext, project: str) -> AutomationState:
    row = _locked(session, access, project)
    if row.automation_paused_at is not None:
        row.automation_paused_at = None
        row.automation_paused_reason = None
        add_activity(
            session,
            "automation.resumed",
            f"{actor_for(session, access)} resumed automation of {row.key}",
            now(),
        )
    return automation_state(row)
