"""Shared helpers for syncing personal (project-less) tasks."""

from __future__ import annotations

import logging
from concurrent.futures import ALL_COMPLETED, FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import Dict, Optional, Set

from src.lib.database import Database
from src.lib.http_client import AsanaHttpClient
from src.lib.user_backfill import UserBackfill
from src.tasks.task_processing import collect_custom_field_gids, ensure_custom_fields, merge_task_payload
from src.tasks.tasks import (
    TASK_DETAIL_FIELDS,
    TASK_LIST_FIELDS,
    collect_user_gids_from_task,
    fetch_task_detail,
    fetch_workspace_tasks,
)

logger = logging.getLogger(__name__)
PROGRESS_INTERVAL = 25
COMMIT_INTERVAL = 25


@dataclass
class PersonalTaskSyncResult:
    processed: int
    skipped: int
    max_modified_at: Optional[str]


def sync_personal_tasks(
    *,
    connection,
    database: Database,
    client: AsanaHttpClient,
    user_backfill: UserBackfill,
    known_custom_fields: Set[str],
    workspace_gid: str,
    assignee: str,
    page_size: int,
    workers: int,
    sync_time,
    modified_since: Optional[str] = None,
    completed_since: Optional[str] = None,
) -> PersonalTaskSyncResult:
    """Fetch and persist personal tasks for the provided workspace+assignee."""

    processed = 0
    skipped = 0
    committed_since = 0
    max_modified_at: Optional[str] = None
    found_task_gids: Set[str] = set()
    personal_project_gid: Optional[str] = None
    detail_executor = ThreadPoolExecutor(max_workers=max(1, workers))
    pending: Dict[Future, Dict] = {}

    def schedule_task(summary: Dict) -> None:
        nonlocal skipped
        if not _is_personal_summary(summary):
            skipped += 1
            return
        task_gid = summary.get("gid")
        if not task_gid:
            skipped += 1
            return
        future = detail_executor.submit(fetch_task_detail, client, task_gid, TASK_DETAIL_FIELDS)
        pending[future] = summary

    def drain_pending(mode: str) -> None:
        if not pending:
            return
        futures = list(pending.keys())
        if mode == "ready":
            ready = {future for future in futures if future.done()}
            if not ready:
                return
            to_process = ready
        elif mode == "any":
            done, _ = wait(futures, return_when=FIRST_COMPLETED)
            to_process = done
        else:
            done, _ = wait(futures, return_when=ALL_COMPLETED)
            to_process = done

        for future in list(to_process):
            summary = pending.pop(future, None)
            if summary is None:
                continue
            process_task(summary, future)

    def process_task(summary: Dict, future: Future) -> None:
        nonlocal processed, committed_since, max_modified_at, skipped, personal_project_gid
        try:
            detail = future.result()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed fetching detail for personal task %s: %s", summary.get("gid"), exc)
            skipped += 1
            return

        task_payload = merge_task_payload(summary, detail)
        if _has_project_membership(task_payload):
            skipped += 1
            return

        assignee_info = task_payload.get("assignee") or {}
        assignee_gid = assignee_info.get("gid")
        assignee_name = assignee_info.get("name")
        if not assignee_gid:
            skipped += 1
            return

        ensure_custom_fields(
            connection=connection,
            database=database,
            client=client,
            known_custom_fields=known_custom_fields,
            field_gids=collect_custom_field_gids(task_payload),
            sync_time=sync_time,
        )

        user_backfill.ensure_users(connection, collect_user_gids_from_task(task_payload), sync_time)

        personal_proj_gid, personal_project_name = _ensure_personal_project(
            connection=connection,
            database=database,
            assignee_gid=assignee_gid,
            assignee_name=assignee_name,
            sync_time=sync_time,
        )
        if personal_project_gid is None:
            personal_project_gid = personal_proj_gid

        task_payload["is_personal"] = True
        task_payload["memberships"] = [
            {
                "project": {"gid": personal_project_gid, "name": personal_project_name},
                "is_primary": True,
            }
        ]

        database.upsert_task(connection, task_payload, sync_time)
        task_gid = task_payload.get("gid")
        if task_gid:
            found_task_gids.add(task_gid)

        processed += 1
        committed_since += 1
        if committed_since >= COMMIT_INTERVAL:
            connection.commit()
            committed_since = 0

        modified_at = task_payload.get("modified_at")
        if modified_at and (max_modified_at is None or modified_at > max_modified_at):
            max_modified_at = modified_at

        if processed == 1 or processed % PROGRESS_INTERVAL == 0:
            logger.info("Processed %s personal task(s) so far...", processed)

    try:
        max_pending = max(1, workers) * 4

        for summary in fetch_workspace_tasks(
            client,
            workspace_gid=workspace_gid,
            assignee=assignee,
            opt_fields=TASK_LIST_FIELDS,
            page_size=page_size,
            modified_since=modified_since,
            completed_since=completed_since,
        ):
            schedule_task(summary)
            drain_pending(mode="ready")
            if len(pending) >= max_pending:
                drain_pending(mode="any")

        drain_pending(mode="all")

        if committed_since:
            connection.commit()

        if personal_project_gid and found_task_gids is not None:
            deleted_count = database.mark_tasks_as_deleted(
                connection,
                found_task_gids,
                sync_time,
                scope_project_gids={personal_project_gid},
            )
            if deleted_count > 0:
                connection.commit()
                logger.info("Marked %s personal task(s) as deleted for assignee %s.", deleted_count, assignee)
    finally:
        detail_executor.shutdown(wait=True)

    logger.info(
        "Completed personal task sync for assignee %s. Processed=%s skipped=%s.",
        assignee,
        processed,
        skipped,
    )
    return PersonalTaskSyncResult(processed=processed, skipped=skipped, max_modified_at=max_modified_at)


def _is_personal_summary(task: Dict) -> bool:
    """Return True when the task summary has no project memberships."""
    return not _has_project_membership(task)


def _has_project_membership(task: Dict) -> bool:
    for membership in task.get("memberships") or []:
        if not isinstance(membership, dict):
            continue
        project = membership.get("project") or {}
        if project.get("gid"):
            return True
    return False


def _ensure_personal_project(
    *,
    connection,
    database: Database,
    assignee_gid: str,
    assignee_name: Optional[str],
    sync_time,
) -> tuple[str, str]:
    project_gid = f"personal_{assignee_gid}"
    project_name = assignee_name or assignee_gid
    project_payload = {
        "gid": project_gid,
        "name": project_name,
        "resource_subtype": "personal_project_placeholder",
        "archived": False,
        "team": {"gid": None, "name": project_name},
        "owner": {"gid": assignee_gid, "name": assignee_name},
        "notes": f"Personal tasks for {project_name}.",
        "html_notes": None,
        "privacy_setting": "private",
        "is_personal_project": True,
    }
    database.upsert_project(connection, project_payload, sync_time)
    return project_gid, project_name
