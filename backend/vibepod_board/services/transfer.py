"""Project export/import. An import either creates the project or, with confirmation,
replaces an existing project with the same key; either way it is one transaction."""

from sqlalchemy import delete
from sqlmodel import Session, col, select

from vibepod_board.access import token_access
from vibepod_board.bundle import ProjectBundle, parse_project_bundle
from vibepod_board.enums import BoardColumn
from vibepod_board.errors import Conflict
from vibepod_board.github import normalize_repository
from vibepod_board.schemas import ImportProjectResult, format_timestamp, now
from vibepod_board.services.board import list_cards
from vibepod_board.services.common import (
    add_activity,
    project_from_row,
    readiness_from_row,
    require_project,
    transactional,
)
from vibepod_board.services.documents import list_documents
from vibepod_board.services.ideas import list_ideas
from vibepod_board.tables import (
    BoardCardRow,
    DocumentRow,
    IdeaReadinessEventRow,
    IdeaRow,
    ProjectRow,
    TaskDependencyRow,
)


def readiness_events_for(session: Session, idea_ids: list[str]):
    if not idea_ids:
        return []
    rows = session.exec(
        select(IdeaReadinessEventRow)
        .where(col(IdeaReadinessEventRow.idea_id).in_(idea_ids))
        .order_by(col(IdeaReadinessEventRow.created_at).desc())
    ).all()
    return [readiness_from_row(row) for row in rows]


# Automation state belongs to the board it runs on: claims, attempts and blocks are not part
# of a bundle, and an imported card starts unclaimed and unblocked.
AUTOMATION_CARD_FIELDS = {
    "claimed_at",
    "claim_expires_at",
    "attempts",
    "blocked_at",
    "blocked_reason",
    "question",
    "head_sha",
    "handed_over_at",
    "review_rounds",
}
# Review settings are how a board automates the project, not part of the project's content.
AUTOMATION_PROJECT_FIELDS = {"required_approvals", "max_review_rounds"}


def export_project(session: Session, project_id: str) -> ProjectBundle:
    project = project_from_row(require_project(session, project_id))
    access = token_access("project-export", [project.id])
    ideas = list_ideas(session, access, project.id)
    cards = list_cards(session, access, project.id)
    # A claimed task leaves as planned work nobody holds: its runner's claim does not come
    # along, so the imported task must not stay in progress under a holder that is gone.
    claimed = {card.idea_id: card.assignee for card in cards if card.claimed_at and card.idea_id}
    for index, card in enumerate(cards):
        if card.claimed_at:
            cards[index] = card.model_copy(update={"column": BoardColumn.PLANNED, "assignee": None})
    for index, idea in enumerate(ideas):
        if idea.id in claimed and idea.assignee == claimed[idea.id]:
            ideas[index] = idea.model_copy(update={"assignee": None})
    return parse_project_bundle(
        {
            "bundleVersion": 4,
            "exportedAt": format_timestamp(now()),
            "project": project.model_dump(mode="json", exclude=AUTOMATION_PROJECT_FIELDS),
            "ideas": [idea.model_dump(mode="json") for idea in ideas],
            "boardCards": [
                card.model_dump(mode="json", exclude=AUTOMATION_CARD_FIELDS) for card in cards
            ],
            "readinessEvents": [
                event.model_dump(mode="json")
                for event in readiness_events_for(session, [idea.id for idea in ideas])
            ],
            "documents": [
                document.model_dump(mode="json")
                for document in list_documents(session, access, project.id)
            ],
        }
    )


def _assert_ids_available(session: Session, bundle: ProjectBundle, destination: str) -> None:
    for entity, table, ids in (
        ("Idea", IdeaRow, [idea.id for idea in bundle.ideas]),
        ("Board card", BoardCardRow, [card.id for card in bundle.board_cards]),
        ("Document", DocumentRow, [document.id for document in bundle.documents]),
    ):
        if not ids:
            continue
        collision = session.exec(
            select(table.id).where(col(table.id).in_(ids), table.project_id != destination).limit(1)
        ).first()
        if collision:
            raise Conflict(f"{entity} ID is already used by another project: {collision}")

    event_ids = [event.id for event in bundle.readiness_events]
    if event_ids:
        collision = session.exec(
            select(IdeaReadinessEventRow.id)
            .join(IdeaRow, col(IdeaRow.id) == IdeaReadinessEventRow.idea_id)
            .where(col(IdeaReadinessEventRow.id).in_(event_ids), IdeaRow.project_id != destination)
            .limit(1)
        ).first()
        if collision:
            raise Conflict(f"Readiness event ID is already used by another project: {collision}")


def _insert_children(session: Session, bundle: ProjectBundle, destination: str) -> None:
    for idea in bundle.ideas:
        session.add(
            IdeaRow(
                id=idea.id,
                project_id=destination,
                task_number=idea.task_number,
                title=idea.title,
                summary=idea.summary,
                details=idea.details,
                status=idea.status,
                labels=list(idea.labels),
                acceptance_criteria=list(idea.acceptance_criteria),
                github_issue_url=idea.github_issue_url,
                github_issue_number=idea.github_issue_number,
                github_repository=(
                    normalize_repository(idea.github_repository) if idea.github_repository else None
                ),
                github_issue_state=idea.github_issue_state,
                github_issue_updated_at=idea.github_issue_updated_at,
                github_synced_at=idea.github_synced_at,
                repository_local_path=idea.repository_local_path,
                repository_remote_url=idea.repository_remote_url,
                assignee=idea.assignee,
                readiness_score=idea.readiness_score,
                readiness_reason=idea.readiness_reason,
                readiness_evaluated_at=idea.readiness_evaluated_at,
                created_at=idea.created_at,
                updated_at=idea.updated_at,
            )
        )
    session.flush()
    for idea in bundle.ideas:
        for depends_on_id in dict.fromkeys(idea.depends_on):
            session.add(
                TaskDependencyRow(
                    idea_id=idea.id, depends_on_idea_id=depends_on_id, created_at=idea.updated_at
                )
            )
    for card in bundle.board_cards:
        session.add(
            BoardCardRow(
                id=card.id,
                project_id=destination,
                idea_id=card.idea_id,
                title=card.title,
                details=card.details,
                column_name=card.column,
                branch_name=card.branch_name,
                github_issue_url=card.github_issue_url,
                github_issue_number=card.github_issue_number,
                github_pr_url=card.github_pr_url,
                github_pr_number=card.github_pr_number,
                github_pr_repository=(
                    normalize_repository(card.github_pr_repository)
                    if card.github_pr_repository
                    else None
                ),
                github_pr_state=card.github_pr_state,
                github_pr_draft=card.github_pr_draft,
                github_pr_base=card.github_pr_base,
                github_pr_synced_at=card.github_pr_synced_at,
                repository_local_path=card.repository_local_path,
                repository_remote_url=card.repository_remote_url,
                assignee=card.assignee,
                labels=list(card.labels),
                readiness_score=card.readiness_score,
                readiness_reason=card.readiness_reason,
                readiness_evaluated_at=card.readiness_evaluated_at,
                archived_at=card.archived_at,
                created_at=card.created_at,
                updated_at=card.updated_at,
            )
        )
    for event in bundle.readiness_events:
        session.add(
            IdeaReadinessEventRow(
                id=event.id,
                idea_id=event.idea_id,
                score=event.score,
                reason=event.reason,
                created_at=event.created_at,
            )
        )
    for document in bundle.documents:
        session.add(
            DocumentRow(
                id=document.id,
                project_id=destination,
                title=document.title,
                kind=document.kind,
                content=document.content,
                linked_idea_ids=list(document.linked_idea_ids),
                linked_card_ids=list(document.linked_card_ids),
                created_at=document.created_at,
                updated_at=document.updated_at,
            )
        )
    session.flush()


@transactional
def import_project(
    session: Session, bundle: ProjectBundle, replace_existing: bool = False
) -> ImportProjectResult:
    # Lock the key's row, so two concurrent imports of the same key wait for each other
    # instead of both reading the pre-import state and racing into a constraint error.
    existing = session.exec(
        select(ProjectRow).where(ProjectRow.key == bundle.project.key).with_for_update()
    ).first()
    if existing and not replace_existing:
        raise Conflict(
            f"Project key {bundle.project.key} already exists; replacement confirmation is required"
        )

    destination = existing.id if existing else bundle.project.id
    if not existing and session.get(ProjectRow, bundle.project.id):
        raise Conflict(f"Project ID is already used: {bundle.project.id}")
    _assert_ids_available(session, bundle, destination)

    if existing:
        for table in (BoardCardRow, DocumentRow, IdeaRow):
            session.execute(delete(table).where(col(table.project_id) == destination))
        existing.title = bundle.project.title
        existing.summary = bundle.project.summary
        existing.created_at = bundle.project.created_at
        existing.updated_at = bundle.project.updated_at
        session.flush()
    else:
        session.add(
            ProjectRow(
                id=destination,
                key=bundle.project.key,
                title=bundle.project.title,
                summary=bundle.project.summary,
                created_at=bundle.project.created_at,
                updated_at=bundle.project.updated_at,
            )
        )
        session.flush()
    _insert_children(session, bundle, destination)
    imported = require_project(session, destination)
    highest = max((idea.task_number for idea in bundle.ideas), default=0)
    imported.last_task_number = max(imported.last_task_number, highest)
    add_activity(session, "project.imported", f"Imported project: {bundle.project.title}")
    # A replaced project keeps its review settings.
    return ImportProjectResult(item=project_from_row(imported), replaced=existing is not None)
