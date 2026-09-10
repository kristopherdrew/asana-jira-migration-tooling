"""Business logic for finding recurring tasks (undocumented `recurrence` field).

Prefers the local SQLite snapshot (`tasks.recurrence`/`recurrence_checked_at`, populated by the
normal task sync) and falls back to live Asana queries only for gaps: projects never synced, or
task rows synced before the `recurrence` column existed. Live-queried values are written back to
SQLite so a later run against the same projects needs no further API calls.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Generator, Iterable, List, Optional, Set, Tuple

from src.lib.database import Database
from src.lib.http_client import AsanaHttpClient, AsanaHttpError
from src.tasks.tasks import fetch_project_tasks, fetch_subtasks, fetch_task_detail


logger = logging.getLogger(__name__)


RECURRING_TASK_FIELDS = [
    "gid",
    "name",
    "resource_subtype",
    "completed",
    "due_on",
    "permalink_url",
    "parent.gid",
    "parent.name",
    "recurrence",
]


@dataclass(frozen=True)
class RecurringTaskInstance:
    project_gid: str
    project_name: str
    task_gid: str
    task_name: str
    task_type: str
    parent_gid: str
    parent_name: str
    recurrence_type: str
    recurrence_frequency: str
    due_on: str
    completed: bool
    permalink_url: str


@dataclass(frozen=True)
class RecurringTaskLookupError:
    project_gid: str
    project_name: str
    task_gid: str
    task_name: str
    status: str
    message: str


@dataclass(frozen=True)
class RecurringTaskSeries:
    project_gid: str
    project_name: str
    task_name: str
    recurrence_type: str
    recurrence_frequency: str
    occurrence_count: int
    first_due_on: str
    last_due_on: str


def fetch_project_name(client: AsanaHttpClient, project_gid: str) -> str:
    """Return a project's live display name, or the gid if the lookup fails."""
    payload = client.get(f"/projects/{project_gid}", params={"opt_fields": "name"})
    return payload.get("data", payload).get("name") or project_gid


def walk_project_tasks(
    client: AsanaHttpClient,
    project_gid: str,
    opt_fields: Optional[Iterable[str]] = None,
    page_size: int = 100,
) -> Generator[Tuple[Dict, str], None, None]:
    """Yield ``(task, task_type)`` for every task and nested subtask in a project.

    ``task_type`` is ``"task"`` for top-level tasks and ``"subtask"`` for any task
    reached via the subtasks endpoint, however deeply nested. A ``seen`` set guards
    against duplicate/cyclic traversal.
    """
    seen: Set[str] = set()

    def walk(tasks: Iterable[Dict], task_type: str) -> Generator[Tuple[Dict, str], None, None]:
        for task in tasks:
            task_gid = task.get("gid")
            if not task_gid or task_gid in seen:
                continue
            seen.add(task_gid)
            yield task, task_type
            try:
                subtasks = list(fetch_subtasks(client, task_gid, opt_fields, page_size=page_size))
            except AsanaHttpError as exc:
                logger.warning("Skipping subtasks of %s: %s", task_gid, exc)
                continue
            yield from walk(subtasks, "subtask")

    yield from walk(fetch_project_tasks(client, project_gid, opt_fields, page_size=page_size), "task")


NON_RECURRING_TYPE = "never"


def is_recurring(recurrence: Optional[Dict]) -> bool:
    """Return True when a task's `recurrence` object represents an actual recurrence.

    Asana returns a non-null `recurrence` object on every task once it is requested via
    ``opt_fields``, even for non-recurring tasks — its `type` is the literal string
    ``"never"`` in that case, not `null` or an absent key, so a plain truthiness check on
    `type` is not enough.
    """
    if not recurrence:
        return False
    recurrence_type = recurrence.get("type")
    return bool(recurrence_type) and recurrence_type != NON_RECURRING_TYPE


def extract_recurrence_type_and_frequency(recurrence: Optional[Dict]) -> Tuple[str, str]:
    """Return ``(type, frequency)`` strings from a task's `recurrence` object, blank if absent."""
    if not recurrence:
        return "", ""
    recurrence_type = recurrence.get("type") or ""
    data = recurrence.get("data") or {}
    frequency = data.get("frequency")
    return str(recurrence_type), "" if frequency is None else str(frequency)


def collect_recurring_task_instances(
    client: AsanaHttpClient,
    project_gid: str,
    project_name: str,
    page_size: int = 100,
) -> List[RecurringTaskInstance]:
    """Walk a project's tasks and subtasks, returning one instance per recurring task found."""
    instances: List[RecurringTaskInstance] = []
    for task, task_type in walk_project_tasks(client, project_gid, RECURRING_TASK_FIELDS, page_size=page_size):
        recurrence = task.get("recurrence")
        if not is_recurring(recurrence):
            continue
        recurrence_type, recurrence_frequency = extract_recurrence_type_and_frequency(recurrence)
        parent = task.get("parent") or {}
        instances.append(
            RecurringTaskInstance(
                project_gid=project_gid,
                project_name=project_name,
                task_gid=task.get("gid") or "",
                task_name=task.get("name") or "",
                task_type=task_type,
                parent_gid=parent.get("gid") or "",
                parent_name=parent.get("name") or "",
                recurrence_type=recurrence_type,
                recurrence_frequency=recurrence_frequency,
                due_on=task.get("due_on") or "",
                completed=bool(task.get("completed")),
                permalink_url=task.get("permalink_url") or "",
            )
        )
    return instances


def group_into_series(instances: Iterable[RecurringTaskInstance]) -> List[RecurringTaskSeries]:
    """Group recurring task instances into best-effort series by name + recurrence pattern.

    Asana's undocumented `recurrence` field carries no series identifier, so grouping by
    ``(project_gid, task_name, recurrence_type, recurrence_frequency)`` is a heuristic:
    two unrelated tasks that happen to share a name and schedule will merge into one row.
    """
    groups: Dict[Tuple[str, str, str, str], List[RecurringTaskInstance]] = defaultdict(list)
    for instance in instances:
        key = (instance.project_gid, instance.task_name, instance.recurrence_type, instance.recurrence_frequency)
        groups[key].append(instance)

    series: List[RecurringTaskSeries] = []
    for (project_gid, task_name, recurrence_type, recurrence_frequency), group in groups.items():
        due_ons = sorted(instance.due_on for instance in group if instance.due_on)
        series.append(
            RecurringTaskSeries(
                project_gid=project_gid,
                project_name=group[0].project_name,
                task_name=task_name,
                recurrence_type=recurrence_type,
                recurrence_frequency=recurrence_frequency,
                occurrence_count=len(group),
                first_due_on=due_ons[0] if due_ons else "",
                last_due_on=due_ons[-1] if due_ons else "",
            )
        )
    return series


def fetch_project_name_from_db(connection: sqlite3.Connection, project_gid: str) -> Optional[str]:
    row = connection.execute("SELECT name FROM projects WHERE gid = ?", (project_gid,)).fetchone()
    return row["name"] if row and row["name"] else None


def fetch_project_task_rows(connection: sqlite3.Connection, project_gid: str) -> List[sqlite3.Row]:
    """Return every task (including subtasks) synced for a project, via `task_projects`.

    Subtask rows are copied onto `task_projects` from their parent by `Database._copy_parent_projects`,
    so this single join already covers the full task tree — no manual recursion needed.
    """
    return list(
        connection.execute(
            """
            SELECT DISTINCT t.gid, t.name, t.due_on, t.completed, t.permalink_url,
                   t.parent_gid, parent.name AS parent_name, t.is_subtask,
                   t.recurrence, t.recurrence_checked_at
            FROM task_projects tp
            JOIN tasks t ON t.gid = tp.task_gid
            LEFT JOIN tasks parent ON parent.gid = t.parent_gid
            WHERE tp.project_gid = ?
            """,
            (project_gid,),
        ).fetchall()
    )


def _build_instance(
    project_gid: str,
    project_name: str,
    task_gid: str,
    task_name: str,
    task_type: str,
    parent_gid: str,
    parent_name: str,
    due_on: str,
    completed: bool,
    permalink_url: str,
    recurrence: Optional[Dict],
) -> RecurringTaskInstance:
    recurrence_type, recurrence_frequency = extract_recurrence_type_and_frequency(recurrence)
    return RecurringTaskInstance(
        project_gid=project_gid,
        project_name=project_name,
        task_gid=task_gid,
        task_name=task_name,
        task_type=task_type,
        parent_gid=parent_gid,
        parent_name=parent_name,
        recurrence_type=recurrence_type,
        recurrence_frequency=recurrence_frequency,
        due_on=due_on,
        completed=completed,
        permalink_url=permalink_url,
    )


def _collect_via_full_live_walk(
    client: AsanaHttpClient,
    database: Database,
    connection: sqlite3.Connection,
    project_gid: str,
    project_name: str,
    page_size: int,
) -> Tuple[List[RecurringTaskInstance], List[RecurringTaskLookupError]]:
    """Discover a never-synced project's task tree live, writing recurrence back for every task seen."""
    instances: List[RecurringTaskInstance] = []
    checked_at = datetime.now(timezone.utc)
    for task, task_type in walk_project_tasks(client, project_gid, RECURRING_TASK_FIELDS, page_size=page_size):
        task_gid = task.get("gid")
        recurrence = task.get("recurrence")
        if task_gid:
            database.update_task_recurrence(connection, task_gid, task.get("name") or "", recurrence, checked_at)
        if not is_recurring(recurrence):
            continue
        parent = task.get("parent") or {}
        instances.append(
            _build_instance(
                project_gid=project_gid,
                project_name=project_name,
                task_gid=task_gid or "",
                task_name=task.get("name") or "",
                task_type=task_type,
                parent_gid=parent.get("gid") or "",
                parent_name=parent.get("name") or "",
                due_on=task.get("due_on") or "",
                completed=bool(task.get("completed")),
                permalink_url=task.get("permalink_url") or "",
                recurrence=recurrence,
            )
        )
    connection.commit()
    # Errors during this path are per-subtask-listing failures already logged (and skipped)
    # inside walk_project_tasks; there is no per-task recurrence lookup here to fail
    # separately, so this path never itself produces a RecurringTaskLookupError.
    return instances, []


def collect_recurring_task_instances_hybrid(
    client: AsanaHttpClient,
    database: Database,
    connection: sqlite3.Connection,
    project_gid: str,
    page_size: int = 100,
) -> Tuple[List[RecurringTaskInstance], List[RecurringTaskLookupError]]:
    """DB-first recurring-task lookup for one project, falling back to live Asana for gaps.

    - A project with no synced tasks falls back entirely to a live API walk (which also
      populates the DB for next time).
    - A synced project uses each task's stored `recurrence` when `recurrence_checked_at` is
      set; otherwise it does a single-task live lookup just for that task's recurrence and
      writes the result back.

    Returns ``(instances, errors)`` — ``errors`` records every task whose live recurrence
    lookup failed (e.g. 403/404), so callers can report exactly what failed and why instead
    of only seeing it in the log.
    """
    rows = fetch_project_task_rows(connection, project_gid)
    if not rows:
        project_name = fetch_project_name(client, project_gid)
        return _collect_via_full_live_walk(client, database, connection, project_gid, project_name, page_size)

    project_name = fetch_project_name_from_db(connection, project_gid) or fetch_project_name(client, project_gid)
    checked_at = datetime.now(timezone.utc)
    instances: List[RecurringTaskInstance] = []
    errors: List[RecurringTaskLookupError] = []
    for row in rows:
        task_gid = row["gid"]
        if row["recurrence_checked_at"] is not None:
            recurrence = json.loads(row["recurrence"]) if row["recurrence"] else None
        else:
            try:
                detail = fetch_task_detail(client, task_gid, RECURRING_TASK_FIELDS)
            except AsanaHttpError as exc:
                logger.warning("Skipping recurrence lookup for task %s: %s", task_gid, exc)
                errors.append(
                    RecurringTaskLookupError(
                        project_gid=project_gid,
                        project_name=project_name,
                        task_gid=task_gid,
                        task_name=row["name"] or "",
                        status=str(exc.status),
                        message=str(exc),
                    )
                )
                continue
            recurrence = detail.get("recurrence")
            database.update_task_recurrence(connection, task_gid, row["name"] or "", recurrence, checked_at)
        if not is_recurring(recurrence):
            continue
        instances.append(
            _build_instance(
                project_gid=project_gid,
                project_name=project_name,
                task_gid=task_gid,
                task_name=row["name"] or "",
                task_type="subtask" if row["is_subtask"] else "task",
                parent_gid=row["parent_gid"] or "",
                parent_name=row["parent_name"] or "",
                due_on=row["due_on"] or "",
                completed=bool(row["completed"]),
                permalink_url=row["permalink_url"] or "",
                recurrence=recurrence,
            )
        )
    connection.commit()
    return instances, errors
