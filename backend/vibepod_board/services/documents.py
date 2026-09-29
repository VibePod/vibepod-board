from collections.abc import Sequence
from dataclasses import dataclass

from sqlmodel import Session, col, select

from vibepod_board.access import (
    AccessContext,
    assert_can_access_project,
    default_project_id_for_create,
)
from vibepod_board.enums import DocumentKind
from vibepod_board.schemas import PlanDocument, now
from vibepod_board.services.common import (
    add_activity,
    assert_title,
    document_from_row,
    new_id,
    normalize_list,
    require_document,
    transactional,
)
from vibepod_board.services.listing import ListFilter, list_scope, page_query, scoped
from vibepod_board.services.projects import resolve_project_id_for_create
from vibepod_board.services.references import resolve_project_id
from vibepod_board.tables import DocumentRow


@dataclass(frozen=True)
class DocumentFilter(ListFilter):
    kind: Sequence[DocumentKind] = ()


def list_documents(
    session: Session,
    access: AccessContext,
    project_id: str | None = None,
    filters: DocumentFilter | None = None,
) -> list[PlanDocument]:
    filters = filters or DocumentFilter(project=project_id)
    scope = list_scope(session, access, filters.project)
    if scope == []:
        return []
    query = scoped(select(DocumentRow), DocumentRow.project_id, scope)
    if filters.kind:
        query = query.where(col(DocumentRow.kind).in_(list(filters.kind)))
    rows = session.exec(page_query(query, DocumentRow, filters)).all()
    return [document_from_row(row) for row in rows]


@transactional
def create_document(
    session: Session,
    access: AccessContext,
    title: str,
    project_id: str | None = None,
    kind: DocumentKind | None = None,
    content: str | None = None,
    linked_idea_ids: Sequence[str] | None = None,
    linked_card_ids: Sequence[str] | None = None,
) -> PlanDocument:
    assert_title(title, "Document")
    requested = resolve_project_id(session, project_id) if project_id else None
    resolved_project_id = resolve_project_id_for_create(
        session, default_project_id_for_create(access, requested)
    )
    assert_can_access_project(access, resolved_project_id)
    timestamp = now()
    document = DocumentRow(
        id=new_id(),
        project_id=resolved_project_id,
        title=title.strip(),
        kind=kind or DocumentKind.EXECUTION_PLAN,
        content=(content or "").strip(),
        linked_idea_ids=normalize_list(linked_idea_ids),
        linked_card_ids=normalize_list(linked_card_ids),
        created_at=timestamp,
        updated_at=timestamp,
    )
    session.add(document)
    session.flush()
    add_activity(session, "document.created", f"Created document: {document.title}", timestamp)
    return document_from_row(document)


@transactional
def update_document(
    session: Session,
    access: AccessContext,
    document_id: str,
    title: str | None = None,
    kind: DocumentKind | None = None,
    content: str | None = None,
    linked_idea_ids: Sequence[str] | None = None,
    linked_card_ids: Sequence[str] | None = None,
) -> PlanDocument:
    document = require_document(session, document_id)
    assert_can_access_project(access, document.project_id)
    if title is not None:
        assert_title(title, "Document")
        document.title = title.strip()
    if kind is not None:
        document.kind = kind
    if content is not None:
        # Content is kept verbatim on update (the TS store did not trim it either).
        document.content = content
    if linked_idea_ids is not None:
        document.linked_idea_ids = normalize_list(linked_idea_ids)
    if linked_card_ids is not None:
        document.linked_card_ids = normalize_list(linked_card_ids)
    timestamp = now()
    document.updated_at = timestamp
    session.flush()
    add_activity(session, "document.updated", f"Updated document: {document.title}", timestamp)
    return document_from_row(document)
