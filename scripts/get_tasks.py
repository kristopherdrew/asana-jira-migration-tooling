#!/usr/bin/env python3
"""CLI script to fetch Asana tasks (with subtasks) and persist them to SQLite."""

from __future__ import annotations

import argparse
import logging
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from concurrent.futures import ALL_COMPLETED, FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from typing import Dict, Generator, Iterable, List, Optional, Set

from src.lib.config import load_credentials
from src.lib.database import Database, synced_at_timestamp
from src.lib.http_client import AsanaHttpClient
from src.lib.logging_utils import configure_logging, resolve_log_level
from src.lib.project_scope import ARCHIVED_SCOPE_CHOICES, determine_projects, validate_scope_args
from src.lib.team_picker import choose_team
from src.lib.user_backfill import UserBackfill
from src.tasks.tasks import (
    SUBTASK_FIELDS,
    TASK_DETAIL_FIELDS,
    TASK_LIST_FIELDS,
    collect_user_gids_from_task,
    fetch_project_tasks,
    fetch_subtasks,
    fetch_task_detail,
)
from src.tasks.task_processing import collect_custom_field_gids, ensure_custom_fields, merge_task_payload


DEFAULT_CONFIG_ROOT = "."
DEFAULT_DB_PATH = "data/asana.sqlite"
DEFAULT_PAGE_SIZE = 100
DEFAULT_WORKERS = 5
DEFAULT_STATE_PATH = "exports/all_team_sync_state.json"
DEFAULT_MISSING_PROJECT_STATE_PATH = "exports/missing_service_user_projects.json"
PROGRESS_INTERVAL = 25
COMMIT_INTERVAL = 25
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MissingProjectMembershipCandidate:
    project_gid: str
    project_name: str
    team_gid: Optional[str]
    team_name: Optional[str]
    expected_task_count: Optional[int]
    actual_task_count: int


@dataclass(frozen=True)
class TaskSyncResult:
    processed_projects: int
    missing_membership_candidates: List[MissingProjectMembershipCandidate]
    missing_membership_state_path: Optional[str]


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workspace-gid",
        help="Override the workspace GID from the resolved credentials.",
    )
    parser.add_argument(
        "--project-gid",
        help="Sync a single project by GID instead of using the default.",
    )
    parser.add_argument(
        "--project-gids",
        help="Comma-separated list of project GIDs to sync sequentially.",
    )
    parser.add_argument(
        "--team",
        action="store_true",
        help="Interactively select a team to sync. Overrides the default project list.",
    )
    parser.add_argument(
        "--team-gids",
        help="Comma-separated list of team GIDs to sync sequentially.",
    )
    parser.add_argument(
        "--all-teams",
        action="store_true",
        help="Sync every team present in the database (can be combined with --exclude-team-gids).",
    )
    parser.add_argument(
        "--exclude-team-gids",
        help="Comma-separated list of team GIDs to skip when using --all-teams.",
    )
    parser.add_argument(
        "--resume-all-teams",
        action="store_true",
        help="When used with --all-teams, resume from teams already completed in the JSON state file.",
    )
    parser.add_argument(
        "--all-teams-state-file",
        default=DEFAULT_STATE_PATH,
        help=(
            f"Path to the all-teams progress file (default: {DEFAULT_STATE_PATH}). "
            "The file stores completed_team_gids and an ISO 8601 UTC updated_at timestamp."
        ),
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=DEFAULT_PAGE_SIZE,
        help=f"Page size for task pagination (default: {DEFAULT_PAGE_SIZE}).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=f"Number of concurrent workers for detail fetches (default: {DEFAULT_WORKERS}).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging verbosity (default: INFO).",
    )
    parser.add_argument(
        "--project-archived-scope",
        choices=ARCHIVED_SCOPE_CHOICES,
        default="active",
        help="Limit which projects are synced when selecting by team: active (default), archived, or all.",
    )
    return parser.parse_args(argv)


def schedule_task_detail(
    executor: ThreadPoolExecutor,
    client: AsanaHttpClient,
    task_gid: str,
) -> Future:
    return executor.submit(fetch_task_detail, client, task_gid, TASK_DETAIL_FIELDS)


def schedule_subtask_listing(
    client: AsanaHttpClient,
    parent_gid: str,
    page_size: int,
) -> Generator[Dict, None, None]:
    return fetch_subtasks(client, parent_gid, SUBTASK_FIELDS, page_size=page_size)


def count_actual_project_tasks(connection, project_gid: str) -> int:
    row = connection.execute(
        """
        SELECT COUNT(DISTINCT tp.task_gid) AS task_count
        FROM task_projects tp
        WHERE tp.project_gid = ?
        """,
        (project_gid,),
    ).fetchone()
    if not row:
        return 0
    return int(row["task_count"] or 0)


def build_missing_project_candidate(connection, project_gid: str) -> Optional[MissingProjectMembershipCandidate]:
    row = connection.execute(
        """
        SELECT gid, name, team_gid, team_name, num_tasks
        FROM projects
        WHERE gid = ?
        """,
        (project_gid,),
    ).fetchone()
    if not row:
        return None
    expected_task_count = row["num_tasks"]
    if not expected_task_count or expected_task_count <= 0:
        return None
    actual_task_count = count_actual_project_tasks(connection, project_gid)
    if actual_task_count > 0:
        return None
    return MissingProjectMembershipCandidate(
        project_gid=row["gid"],
        project_name=row["name"] or row["gid"],
        team_gid=row["team_gid"],
        team_name=row["team_name"],
        expected_task_count=expected_task_count,
        actual_task_count=actual_task_count,
    )


def write_missing_project_candidates(
    state_path: Path,
    candidates: List[MissingProjectMembershipCandidate],
) -> Path:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "missing_project_gids": [candidate.project_gid for candidate in candidates],
        "projects": [asdict(candidate) for candidate in candidates],
        "updated_at": synced_at_timestamp().isoformat(),
    }
    state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return state_path


def process_tasks_for_project(
    connection,
    database: Database,
    client: AsanaHttpClient,
    user_backfill: UserBackfill,
    known_custom_fields: Set[str],
    project_gid: str,
    project_name: str,
    page_size: int,
    workers: int,
    sync_time,
    team_name: Optional[str] = None,
) -> None:
    prefix = f"[{team_name}] " if team_name else ""
    logger.info("%sStarting task sync for project '%s' (%s)...", prefix, project_name, project_gid)
    processed = 0
    committed_since = 0
    seen_task_gids: Set[str] = set()
    detail_executor = ThreadPoolExecutor(max_workers=max(1, workers))
    max_pending = max(1, workers) * 4
    pending: Dict[Future, Dict] = {}

    try:
        def schedule_from_summary(summary_task: Dict) -> None:
            task_gid = summary_task.get("gid")
            if not task_gid or task_gid in seen_task_gids:
                return
            seen_task_gids.add(task_gid)
            future = schedule_task_detail(detail_executor, client, task_gid)
            pending[future] = summary_task

        def process_future(summary: Dict, future: Future) -> None:
            nonlocal processed, committed_since
            try:
                detail = future.result()
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed fetching detail for task %s: %s", summary.get("gid"), exc)
                return

            task_payload = merge_task_payload(summary, detail)
            field_gids = collect_custom_field_gids(task_payload)
            ensure_custom_fields(connection, database, client, known_custom_fields, field_gids, sync_time)

            user_gids = collect_user_gids_from_task(task_payload)
            user_backfill.ensure_users(connection, user_gids, sync_time)

            database.upsert_task(connection, task_payload, sync_time)
            processed += 1
            committed_since += 1
            if committed_since >= COMMIT_INTERVAL:
                connection.commit()
                committed_since = 0

            if processed == 1 or processed % PROGRESS_INTERVAL == 0:
                logger.info("%sProcessed %s task(s) so far...", prefix, processed)

            parent_gid = task_payload.get("gid")
            if parent_gid:
                try:
                    for subtask in schedule_subtask_listing(client, parent_gid, page_size):
                        schedule_from_summary(subtask)
                        drain_pending(mode="ready")
                        if len(pending) >= max_pending:
                            drain_pending(mode="any")
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Failed fetching subtasks for %s: %s", parent_gid, exc)

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

            for future in to_process:
                summary = pending.pop(future, None)
                if summary is None:
                    continue
                process_future(summary, future)

        for summary in fetch_project_tasks(client, project_gid, TASK_LIST_FIELDS, page_size=page_size):
            schedule_from_summary(summary)
            drain_pending(mode="ready")
            if len(pending) >= max_pending:
                drain_pending(mode="any")

        drain_pending(mode="all")

        if committed_since:
            connection.commit()

        logger.info("%sCompleted task sync for '%s'. Total tasks processed: %s.", prefix, project_name, processed)
    finally:
        detail_executor.shutdown(wait=True)


def main(argv: Optional[Iterable[str]] = None) -> None:
    args = parse_args(argv)
    validate_scope_args(
        args.project_gid,
        args.team,
        args.team_gids,
        args.all_teams,
        args.exclude_team_gids,
        args.project_gids,
    )
    try:
        log_level = resolve_log_level(args.log_level)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    configure_logging(log_level)

    credentials = load_credentials(DEFAULT_CONFIG_ROOT)
    workspace_gid = args.workspace_gid or credentials.workspace_gid

    client = AsanaHttpClient(
        token=credentials.token,
        skip_ssl_verification=credentials.skip_ssl_verification,
    )
    database = Database(db_path=DEFAULT_DB_PATH)
    sync_time = synced_at_timestamp()

    with database.connect() as connection:
        database.initialize(connection)
        user_backfill = UserBackfill(client, database)
        user_backfill.prime(connection)
        known_custom_fields: Set[str] = {
            row["gid"] for row in connection.execute("SELECT gid FROM custom_fields WHERE gid IS NOT NULL")
        }

        projects = determine_projects(
            connection=connection,
            project_gid=args.project_gid,
            project_gids=args.project_gids,
            use_team_picker=args.team,
            team_gids=args.team_gids,
            all_teams=args.all_teams,
            exclude_team_gids=args.exclude_team_gids,
            team_selector=choose_team,
            archived_scope=args.project_archived_scope,
        )
        if not projects:
            logger.warning("No projects selected for sync.")
            return TaskSyncResult(processed_projects=0, missing_membership_candidates=[], missing_membership_state_path=None)

        completed_teams: Set[str] = set()
        state_path = Path(args.all_teams_state_file)
        missing_project_candidates: List[MissingProjectMembershipCandidate] = []
        missing_project_state_path = Path(DEFAULT_MISSING_PROJECT_STATE_PATH)
        if args.all_teams and args.resume_all_teams:
            completed_teams = _load_completed_teams(state_path)
            if completed_teams:
                logger.info("Resuming all-team sync; %s team(s) already completed.", len(completed_teams))

        logger.info("Preparing to sync %s project(s) in workspace %s.", len(projects), workspace_gid)
        total_projects = len(projects)
        for index, project in enumerate(projects, start=1):
            project_gid = project["project_gid"]
            project_name = project["project_name"]
            team_gid = project.get("team_gid")
            team_name = project.get("team_name")

            if args.all_teams and team_gid in completed_teams:
                logger.info(
                    "Skipping team '%s' (%s) — marked complete in previous run.",
                    team_name or team_gid,
                    team_gid,
                )
                continue

            logger.info("(%s/%s) Syncing project '%s' (team: %s).", index, total_projects, project_name, team_name)
            process_tasks_for_project(
                connection=connection,
                database=database,
                client=client,
                user_backfill=user_backfill,
                known_custom_fields=known_custom_fields,
                project_gid=project_gid,
                project_name=project_name,
                page_size=args.page_size,
                workers=args.workers,
                sync_time=sync_time,
                team_name=team_name,
            )
            candidate = build_missing_project_candidate(connection, project_gid)
            if candidate:
                missing_project_candidates.append(candidate)
                write_missing_project_candidates(missing_project_state_path, missing_project_candidates)
                logger.info(
                    "Recorded candidate project %s (%s) for service-user membership follow-up.",
                    candidate.project_name,
                    candidate.project_gid,
                )

            # Mark team complete once we've finished the last project for that team.
            if args.all_teams:
                is_last_project = index == total_projects
                next_team_gid = None
                if not is_last_project:
                    next_team_gid = projects[index]["team_gid"]  # zero-based access into list
                if next_team_gid != team_gid:
                    if team_gid:
                        completed_teams.add(team_gid)
                        _save_completed_teams(state_path, completed_teams)
                        logger.info("Marked team '%s' (%s) complete.", team_name or team_gid, team_gid)

        connection.commit()

    logger.info("Task sync completed.")
    if missing_project_candidates:
        logger.info(
            "Identified %s project(s) with expected tasks but no imported task rows. Candidate list written to %s.",
            len(missing_project_candidates),
            missing_project_state_path,
        )
    elif missing_project_state_path.exists():
        try:
            missing_project_state_path.unlink()
        except OSError:
            logger.debug("Unable to remove stale missing-project state file at %s.", missing_project_state_path, exc_info=True)
    return TaskSyncResult(
        processed_projects=len(projects),
        missing_membership_candidates=missing_project_candidates,
        missing_membership_state_path=str(missing_project_state_path) if missing_project_candidates else None,
    )


# ---------------------- All-team resume helpers ---------------------- #
def _load_completed_teams(state_path: Path) -> Set[str]:
    if not state_path.exists():
        return set()
    try:
        with state_path.open("r", encoding="utf-8") as fp:
            payload = json.load(fp)
        completed = payload.get("completed_team_gids") or []
        if not isinstance(completed, list):
            return set()
        return {gid for gid in completed if gid}
    except Exception:
        logger.warning("Unable to read all-team state file at %s; starting fresh.", state_path)
        return set()


def _save_completed_teams(state_path: Path, completed: Set[str]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "completed_team_gids": sorted(completed),
        "updated_at": synced_at_timestamp().isoformat(),
    }
    with state_path.open("w", encoding="utf-8") as fp:
        json.dump(payload, fp, indent=2)


if __name__ == "__main__":
    main()
