from typing import Annotated

from fastapi import APIRouter, Query, status

from vibepod_board.api.models import DocumentCreate, DocumentUpdate
from vibepod_board.api.responses import Item, Items
from vibepod_board.auth import AccessDep, AdminDep
from vibepod_board.db import SessionDep
from vibepod_board.schemas import ActivityEvent, PlanDocument
from vibepod_board.services import activity, documents

router = APIRouter(prefix="/api", tags=["documents"])


@router.get("/documents")
def list_documents(
    session: SessionDep,
    access: AccessDep,
    project_id: Annotated[str | None, Query(alias="projectId")] = None,
) -> Items[PlanDocument]:
    project_id = project_id.strip() or None if project_id else None
    return Items(items=documents.list_documents(session, access, project_id))


@router.post("/documents", status_code=status.HTTP_201_CREATED)
def create_document(
    body: DocumentCreate, session: SessionDep, access: AccessDep
) -> Item[PlanDocument]:
    return Item(item=documents.create_document(session, access, **body.model_dump(by_alias=False)))


@router.patch("/documents/{document_id}")
def update_document(
    document_id: str, body: DocumentUpdate, session: SessionDep, access: AccessDep
) -> Item[PlanDocument]:
    changes = body.model_dump(exclude_unset=True, by_alias=False)
    return Item(item=documents.update_document(session, access, document_id, **changes))


@router.get("/activity")
def list_activity(session: SessionDep, admin: AdminDep) -> Items[ActivityEvent]:
    return Items(items=activity.list_activity(session, admin))
