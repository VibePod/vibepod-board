from typing import Annotated

from fastapi import APIRouter, Query

from vibepod_board.api.deps import SettingsDep
from vibepod_board.api.models import (
    AnswerRequest,
    ArchiveDoneRequest,
    BoardCardBatch,
    BoardCardUpdate,
    CancelReviewRequest,
    CancelRunRequest,
    ClaimRequest,
    HandoverRequest,
    ReadinessRequest,
    ReleaseRequest,
    RenewClaimRequest,
    RenewReviewRequest,
    ReviewRequest,
    ReworkRequest,
)
from vibepod_board.api.queries import (
    Assignee,
    ProjectFilter,
    Unassigned,
    UpdatedSince,
    ViewParam,
    enum_list,
    project_filter,
    split_list,
)
from vibepod_board.api.responses import Columns, Item, Items
from vibepod_board.auth import AccessDep
from vibepod_board.db import SessionDep
from vibepod_board.enums import BoardColumn
from vibepod_board.schemas import (
    BoardCard,
    ClaimResult,
    CompactBoardCard,
    EntityRef,
    KeyedBoardCard,
    ReviewState,
    TaskReview,
)
from vibepod_board.services import board, claims, conversation, readiness, reviews
from vibepod_board.services.views import View, project_cards

router = APIRouter(prefix="/api/board", tags=["board"])

ProjectedCard = KeyedBoardCard | CompactBoardCard | EntityRef


@router.get("")
def get_board(
    session: SessionDep,
    access: AccessDep,
    project_id: ProjectFilter = None,
    column: Annotated[list[str] | None, Query()] = None,
    updated_since: UpdatedSince = None,
    assignee: Assignee = None,
    unassigned: Unassigned = False,
) -> Columns:
    filters = board.CardFilter(
        project=project_filter(project_id),
        column=enum_list(BoardColumn, column, "column"),
        updated_since=updated_since,
        assignee=split_list(assignee),
        unassigned=unassigned,
    )
    return Columns(columns=board.board_columns(session, access, filters=filters))


@router.get("/archived")
def list_archived(
    session: SessionDep, access: AccessDep, project_id: ProjectFilter = None
) -> Items[BoardCard]:
    return Items(items=board.list_archived_cards(session, access, project_filter(project_id)))


@router.post("/archive-done")
def archive_done(
    body: ArchiveDoneRequest, session: SessionDep, access: AccessDep
) -> Items[BoardCard]:
    return Items(items=board.archive_done_cards(session, access, body.project_id))


@router.post("/claim")
def claim_task(
    body: ClaimRequest, session: SessionDep, access: AccessDep, settings: SettingsDep
) -> ClaimResult:
    """Claims the next planned task of a project in the work order, or the named task: its
    card moves to In progress and `assignee` becomes its holder. `claimed` is false when
    nothing can be claimed. In `review` mode it takes a task in Review for a review instead:
    the card stays in Review, and `review` carries the review with the `headSha` to judge."""
    return claims.claim_task(
        session,
        access,
        body.project_id,
        body.assignee,
        task=body.task,
        labels=body.labels,
        min_readiness=body.min_readiness,
        exclude=body.exclude,
        lease_seconds=body.lease_seconds,
        default_lease_seconds=settings.claim_lease_seconds,
        max_attempts=settings.claim_max_attempts,
        mode=body.mode,
    )


@router.post("/batch")
def update_cards(
    body: BoardCardBatch, session: SessionDep, access: AccessDep, view: ViewParam = View.FULL
) -> Items[ProjectedCard]:
    items = [item.model_dump(exclude_unset=True, by_alias=False) for item in body.items]
    return Items(items=project_cards(session, board.update_cards(session, access, items), view))


@router.get("/{card_id}")
def get_card(
    card_id: str, session: SessionDep, access: AccessDep, view: ViewParam = View.FULL
) -> Item[ProjectedCard]:
    """A card by its id or by any reference to its task, such as `VP-236`."""
    card = board.get_card(session, access, card_id)
    return Item(item=project_cards(session, [card], view)[0])


@router.post("/{card_id}/archive")
def archive_card(card_id: str, session: SessionDep, access: AccessDep) -> Item[BoardCard]:
    return Item(item=board.archive_card(session, access, card_id))


@router.post("/{card_id}/unarchive")
def unarchive_card(card_id: str, session: SessionDep, access: AccessDep) -> Item[BoardCard]:
    return Item(item=board.unarchive_card(session, access, card_id))


@router.patch("/{card_id}")
def update_card(
    card_id: str, body: BoardCardUpdate, session: SessionDep, access: AccessDep
) -> Item[BoardCard]:
    changes = body.model_dump(exclude_unset=True, by_alias=False)
    return Item(item=board.update_card(session, access, card_id, **changes))


@router.post("/{card_id}/renew")
def renew_claim(
    card_id: str,
    body: RenewClaimRequest,
    session: SessionDep,
    access: AccessDep,
    settings: SettingsDep,
) -> Item[BoardCard]:
    return Item(
        item=claims.renew_claim(
            session,
            access,
            card_id,
            body.assignee,
            body.lease_seconds,
            settings.claim_lease_seconds,
        )
    )


@router.post("/{card_id}/handover")
def hand_over_task(
    card_id: str, body: HandoverRequest, session: SessionDep, access: AccessDep
) -> Item[BoardCard]:
    return Item(
        item=claims.hand_over_task(
            session,
            access,
            card_id,
            body.assignee,
            branch_name=body.branch_name,
            note=body.note,
            expected_updated_at=body.expected_updated_at,
            head_sha=body.head_sha,
        )
    )


@router.post("/{card_id}/release")
def release_task(
    card_id: str,
    body: ReleaseRequest,
    session: SessionDep,
    access: AccessDep,
    settings: SettingsDep,
) -> Item[BoardCard]:
    return Item(
        item=claims.release_task(
            session,
            access,
            card_id,
            body.assignee,
            outcome=body.outcome,
            note=body.note,
            max_attempts=body.max_attempts,
            default_max_attempts=settings.claim_max_attempts,
        )
    )


@router.post("/{card_id}/cancel")
def cancel_run(
    card_id: str,
    session: SessionDep,
    access: AccessDep,
    body: CancelRunRequest | None = None,
) -> Item[BoardCard]:
    """Stops the run of a claimed task: it returns to Planned without counting an attempt,
    and the worker is told to cancel with its next heartbeat reply."""
    return Item(
        item=claims.cancel_run(
            session,
            access,
            card_id,
            body.reason if body else None,
            body.expected_updated_at if body else None,
        )
    )


@router.post("/{card_id}/answer")
def answer_question(
    card_id: str, body: AnswerRequest, session: SessionDep, access: AccessDep
) -> Item[BoardCard]:
    """Answers the question the task waits on and puts it back in Planned."""
    return Item(
        item=conversation.answer_question(
            session, access, card_id, body.answer, body.expected_updated_at
        )
    )


@router.post("/{card_id}/rework")
def request_rework(
    card_id: str, body: ReworkRequest, session: SessionDep, access: AccessDep
) -> Item[BoardCard]:
    """Sends a task from Review or PR ready back to Planned with feedback; it keeps its branch."""
    return Item(
        item=conversation.request_rework(
            session, access, card_id, body.feedback, body.expected_updated_at
        )
    )


@router.post("/{card_id}/readiness")
def set_card_readiness(
    card_id: str, body: ReadinessRequest, session: SessionDep, access: AccessDep
) -> Item[BoardCard]:
    return Item(
        item=readiness.set_card_readiness(session, access, card_id, body.score, body.reason)
    )


@router.get("/{card_id}/reviews")
def review_state(card_id: str, session: SessionDep, access: AccessDep) -> ReviewState:
    """The reviews of a task, newest first, and its approvals for the current head commit."""
    return reviews.review_state(session, access, card_id)


@router.post("/{card_id}/review")
def submit_review(
    card_id: str, body: ReviewRequest, session: SessionDep, access: AccessDep
) -> ReviewState:
    """Ends the review `assignee` holds with its verdict: approve, rework (with the feedback
    as `note`), needs_input (with the question), failed or released."""
    return reviews.submit_review(
        session, access, card_id, body.assignee, body.verdict, body.head_sha, body.note
    )


@router.post("/{card_id}/review/renew")
def renew_review(
    card_id: str,
    body: RenewReviewRequest,
    session: SessionDep,
    access: AccessDep,
    settings: SettingsDep,
) -> Item[TaskReview]:
    return Item(
        item=reviews.renew_review(
            session,
            access,
            card_id,
            body.assignee,
            body.lease_seconds,
            settings.claim_lease_seconds,
        )
    )


@router.post("/{card_id}/reviews/{review_id}/cancel")
def cancel_review(
    card_id: str,
    review_id: str,
    session: SessionDep,
    access: AccessDep,
    body: CancelReviewRequest | None = None,
) -> ReviewState:
    """Ends one open review without a verdict; its worker is told to cancel."""
    return reviews.cancel_review(session, access, card_id, review_id, body.reason if body else None)
