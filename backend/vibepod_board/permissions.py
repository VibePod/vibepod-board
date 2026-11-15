"""What an API token may do: one level per area, the same in every project it is mapped to.

`POLICY` is the whole rule book. Every MCP tool and every REST route a token can reach is
listed under the area it belongs to, as something that reads or something that writes.
A tool or route that is listed nowhere is refused, so a new one cannot slip through
unprotected; tests keep the lists in step with what the server registers.

The admin is not subject to any of this, and neither are the admin-only routes.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from vibepod_board.errors import BadRequest, Forbidden
from vibepod_board.schemas import PermissionArea


class Area(StrEnum):
    TASKS = "tasks"
    BOARD = "board"
    DOCUMENTS = "documents"
    GITHUB = "github"
    AUTOMATION = "automation"
    PROJECT = "project"


class Level(StrEnum):
    NONE = "none"
    READ = "read"
    WRITE = "write"


_RANK = {Level.NONE: 0, Level.READ: 1, Level.WRITE: 2}


@dataclass(frozen=True)
class Requirement:
    area: Area
    level: Level


@dataclass(frozen=True)
class Open:
    """Needs no permission: every token may use it."""


OPEN = Open()
Needs = Requirement | Open


@dataclass(frozen=True)
class AreaPolicy:
    label: str
    description: str
    read_tools: tuple[str, ...] = ()
    write_tools: tuple[str, ...] = ()
    read_routes: tuple[str, ...] = ()
    write_routes: tuple[str, ...] = ()


POLICY: dict[Area, AreaPolicy] = {
    Area.TASKS: AreaPolicy(
        label="Tasks",
        description="Tasks with their dependencies, readiness scores, history and work order.",
        read_tools=(
            "list_ideas",
            "get_idea",
            "list_work_order",
            "list_task_history",
            "list_idea_readiness",
            "list_readiness",
        ),
        write_tools=(
            "create_idea",
            "update_idea",
            "update_ideas",
            "delete_idea",
            "add_idea_dependency",
            "remove_idea_dependency",
            "set_idea_dependencies",
            "mark_idea_ready",
            "set_idea_readiness",
        ),
        read_routes=(
            "GET /api/ideas",
            "GET /api/ideas/{idea_id}",
            "GET /api/work-order",
            "GET /api/ideas/{idea_id}/readiness",
            "GET /api/ideas/{idea_id}/history",
            "GET /api/readiness",
        ),
        write_routes=(
            "POST /api/ideas",
            "POST /api/ideas/batch",
            "PATCH /api/ideas/{idea_id}",
            "DELETE /api/ideas/{idea_id}",
            "POST /api/ideas/{idea_id}/ready",
            "PUT /api/ideas/{idea_id}/dependencies",
            "POST /api/ideas/{idea_id}/dependencies",
            "DELETE /api/ideas/{idea_id}/dependencies/{depends_on_id}",
            "POST /api/ideas/{idea_id}/readiness",
        ),
    ),
    Area.BOARD: AreaPolicy(
        label="Board",
        description="Board cards: moving, editing and archiving them, answering a worker's "
        "question, requesting rework and approving a review.",
        read_tools=("list_board", "get_board_card", "list_archived_cards"),
        write_tools=(
            "move_board_card",
            "update_board_card",
            "update_board_cards",
            "archive_board_card",
            "unarchive_board_card",
            "answer_task_question",
            "request_task_rework",
            "set_card_readiness",
        ),
        read_routes=(
            "GET /api/board",
            "GET /api/board/archived",
            "GET /api/board/{card_id}",
        ),
        write_routes=(
            "POST /api/board/archive-done",
            "POST /api/board/batch",
            "PATCH /api/board/{card_id}",
            "POST /api/board/{card_id}/archive",
            "POST /api/board/{card_id}/unarchive",
            "POST /api/board/{card_id}/answer",
            "POST /api/board/{card_id}/rework",
            "POST /api/board/{card_id}/readiness",
            "POST /api/board/{card_id}/approve",
        ),
    ),
    Area.DOCUMENTS: AreaPolicy(
        label="Documents",
        description="Plans and notes.",
        read_tools=("list_documents",),
        write_tools=("create_document", "update_document"),
        read_routes=("GET /api/documents",),
        write_routes=("POST /api/documents", "PATCH /api/documents/{document_id}"),
    ),
    Area.GITHUB: AreaPolicy(
        label="GitHub",
        description="Syncing tasks with GitHub issues, and opening or linking pull requests.",
        write_tools=(
            "push_github_issue",
            "pull_github_issue",
            "upsert_github_issue",
            "open_pull_request",
            "link_pull_request",
        ),
        read_routes=("GET /api/github", "GET /api/board/{card_id}/pull-request"),
        write_routes=(
            "POST /api/ideas/{idea_id}/github/push",
            "POST /api/ideas/{idea_id}/github/pull",
            "POST /api/board/{card_id}/pull-request",
            "POST /api/board/{card_id}/pull-request/link",
        ),
    ),
    Area.AUTOMATION: AreaPolicy(
        label="Automation",
        description="What worker agents do: registering as a worker, claiming and handing "
        "over tasks, run reports and reviews.",
        read_tools=("list_workers", "list_run_reports", "list_task_reviews"),
        write_tools=(
            "claim_next_task",
            "renew_task_claim",
            "hand_over_task",
            "release_task",
            "cancel_task_run",
            "add_run_report",
            "submit_review",
            "renew_task_review",
            "cancel_task_review",
            "register_worker",
            "worker_heartbeat",
            "sign_off_worker",
            "stop_worker",
            "remove_worker",
            "remove_offline_workers",
        ),
        read_routes=(
            "GET /api/workers",
            "GET /api/workers/{worker_id}",
            "GET /api/ideas/{idea_id}/runs",
            "GET /api/board/{card_id}/reviews",
            "GET /api/projects/{project_id}/automation",
        ),
        write_routes=(
            "POST /api/board/claim",
            "POST /api/board/{card_id}/renew",
            "POST /api/board/{card_id}/handover",
            "POST /api/board/{card_id}/release",
            "POST /api/board/{card_id}/cancel",
            "POST /api/board/{card_id}/review",
            "POST /api/board/{card_id}/review/renew",
            "POST /api/board/{card_id}/reviews/{review_id}/cancel",
            "POST /api/ideas/{idea_id}/runs",
            "POST /api/workers",
            "POST /api/workers/remove-offline",
            "POST /api/workers/{worker_id}/heartbeat",
            "DELETE /api/workers/{worker_id}",
            "POST /api/workers/{worker_id}/sign-off",
            "POST /api/workers/{worker_id}/stop",
        ),
    ),
    Area.PROJECT: AreaPolicy(
        label="Project",
        description="Project settings, pausing and resuming automation, and archiving, "
        "restoring or deleting the project.",
        write_tools=(
            "update_project_settings",
            "pause_automation",
            "resume_automation",
            "archive_project",
            "restore_project",
            "delete_project",
        ),
        write_routes=(
            "PATCH /api/projects/{project_id}/settings",
            "POST /api/projects/{project_id}/automation/pause",
            "POST /api/projects/{project_id}/automation/resume",
        ),
    ),
}

# Listing the projects a token is mapped to needs no permission. `create_project` is
# listed here because the tool itself refuses everyone but the admin.
OPEN_TOOLS = ("list_projects", "create_project")
OPEN_ROUTES = ("GET /api/projects",)


def _flatten(kind: str, open_names: tuple[str, ...]) -> dict[str, Needs]:
    table: dict[str, Needs] = dict.fromkeys(open_names, OPEN)
    for area, policy in POLICY.items():
        for level in (Level.READ, Level.WRITE):
            for name in getattr(policy, f"{level.value}_{kind}"):
                if name in table:
                    raise RuntimeError(f"{name} is listed twice in the permission policy")
                table[name] = Requirement(area, level)
    return table


TOOL_PERMISSIONS: dict[str, Needs] = _flatten("tools", OPEN_TOOLS)
ROUTE_PERMISSIONS: dict[str, Needs] = _flatten("routes", OPEN_ROUTES)

# An MCP resource needs everything it lists: the board state shows tasks, cards and documents.
RESOURCE_PERMISSIONS: dict[str, tuple[Requirement, ...]] = {
    "vibepod-board://state": (
        Requirement(Area.TASKS, Level.READ),
        Requirement(Area.BOARD, Level.READ),
        Requirement(Area.DOCUMENTS, Level.READ),
    ),
}


def levels(area: Area) -> list[Level]:
    """The levels an area offers. Read is left out where nothing can only be read."""
    policy = POLICY[area]
    readable = bool(policy.read_tools or policy.read_routes)
    return [Level.NONE, *([Level.READ] if readable else []), Level.WRITE]


FULL_ACCESS: dict[str, str] = {area.value: Level.WRITE.value for area in Area}


def normalize(raw: Mapping[str, str] | None) -> dict[str, str]:
    """A complete permission set. Without one a token gets full access, which is what
    every token had before permissions existed; in a set that is given, an area that is
    not named gets no access."""
    if raw is None:
        return dict(FULL_ACCESS)
    known = {area.value: area for area in Area}
    chosen = {area.value: Level.NONE.value for area in Area}
    for key, value in raw.items():
        area = known.get(str(key))
        if area is None:
            raise BadRequest(f"Unknown permission area: {key}")
        if str(value) not in {level.value for level in levels(area)}:
            raise BadRequest(f"Unknown permission level for {area.value}: {value}")
        chosen[area.value] = str(value)
    return chosen


def stored(raw: Mapping[str, str]) -> dict[str, str]:
    """A permission set as it was saved, with every area present. An area added after the
    set was saved has no access."""
    return {area.value: str(raw.get(area.value, Level.NONE.value)) for area in Area}


def allows(granted: Mapping[str, str], needs: Requirement) -> bool:
    try:
        level = Level(granted.get(needs.area.value, Level.NONE.value))
    except ValueError:
        return False
    return _RANK[level] >= _RANK[needs.level]


def require(granted: Mapping[str, str], needs: Needs | None) -> None:
    """Refuses unless `granted` covers `needs`. `None` stands for a tool or route the
    policy does not list."""
    if needs is None:
        raise Forbidden("This token cannot use this operation")
    if isinstance(needs, Open) or allows(granted, needs):
        return
    label = POLICY[needs.area].label
    raise Forbidden(f"This token has no {needs.level.value} access to {label}")


def permits(granted: Mapping[str, str], needs: Needs | None) -> bool:
    """`require` as a yes or no, for leaving out of a listing what would be refused."""
    try:
        require(granted, needs)
    except Forbidden:
        return False
    return True


def require_resource(granted: Mapping[str, str], uri: str) -> None:
    needs = RESOURCE_PERMISSIONS.get(uri)
    if needs is None:
        raise Forbidden("This token cannot use this operation")
    for requirement in needs:
        require(granted, requirement)


def area_catalog() -> list[PermissionArea]:
    """The areas with the levels they offer and the MCP tools each level unlocks, for the
    forms that let an admin choose a token's permissions."""
    return [
        PermissionArea(
            key=area.value,
            label=policy.label,
            description=policy.description,
            levels=[level.value for level in levels(area)],
            read_tools=list(policy.read_tools),
            write_tools=list(policy.write_tools),
        )
        for area, policy in POLICY.items()
    ]
