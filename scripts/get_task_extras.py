#!/usr/bin/env python3
"""CLI script to fetch task comments and attachments and persist them to SQLite."""

from __future__ import annotations

import argparse
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Iterable, List, Optional, Set

from src.lib.config import load_credentials
from src.lib.database import Database, synced_at_timestamp, resolve_default_db_path
from src.lib.http_client import AsanaHttpClient
from src.lib.logging_utils import configure_logging, resolve_log_level
from src.lib.project_scope import determine_projects, validate_scope_args
from src.lib.team_picker import choose_team
from src.lib.user_backfill import UserBackfill
from src.tasks.task_extras import fetch_task_attachments, fetch_task_comments


DEFAULT_CONFIG_ROOT = "."
DEFAULT_DB_PATH = "data/asana.sqlite"
DEFAULT_WORKERS = 5
PROGRESS_INTERVAL = 25
COMMIT_INTERVAL = 25
logger = logging.getLogger(__name__)


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workspace-gid",
        help="Override the workspace GID from the resolved credentials (for logging only).",
    )
    parser.add_argument(
        "--project-gid",
        help="Sync a single project by GID instead of using the team filters.",
    )
    parser.add_argument(
        "--personal",
        action="store_true",
        help="Sync extras for personal tasks (projects flagged as personal_<user_gid>).",
    )
    parser.add_argument(
        "--personal-assignees",
        help="Comma-separated list of user GIDs whose personal task extras should be synced (implies --personal).",
    )
    parser.add_argument(
        "--team",
        action="store_true",
        help="Interactively select a team to sync.",
    )
    parser.add_argument(
        "--team-gids",
        help="Comma-separated list of team GIDs to sync sequentially.",
    )
    parser.add_argument(
        "--all-teams",
        action="store_true",
        help="Sync every team present in the database (combine with --exclude-team-gids).",
    )
    parser.add_argument(
        "--exclude-team-gids",
        help="Comma-separated team GIDs to skip when using --all-teams.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=f"Number of concurrent workers (default: {DEFAULT_WORKERS}).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging verbosity (default: INFO).",
    )
    return parser.parse_args(argv)


def project_task_ids(connection, project_gid: str) -> List[str]:
    rows = connection.execute(
        """
        SELECT DISTINCT task_gid
        FROM task_projects
        WHERE project_gid = ?
        ORDER BY task_gid
        """,
        (project_gid,),
    ).fetchall()
    return [row["task_gid"] for row in rows]


def collect_user_gids(comments: Iterable[Dict], attachments: Iterable[Dict]) -> Set[str]:
    user_gids: Set[str] = set()
    for comment in comments:
        created_by = comment.get("created_by") or {}
        gid = created_by.get("gid")
        if gid:
            user_gids.add(gid)
    for attachment in attachments:
        created_by = attachment.get("created_by") or {}
        gid = created_by.get("gid")
        if gid:
            user_gids.add(gid)
    return user_gids


def process_project(
    connection,
    database: Database,
    client: AsanaHttpClient,
    user_backfill: UserBackfill,
    project: Dict[str, Optional[str]],
    workers: int,
    sync_time,
) -> None:
    project_gid = project["project_gid"]
    project_name = project["project_name"]
    team_name = project.get("team_name")
    prefix = f"[{team_name}] " if team_name else ""

    task_ids = project_task_ids(connection, project_gid)
    if not task_ids:
        logger.info(
            "%sNo tasks stored for project '%s' (%s). Skipping.",
            prefix,
            project_name,
            project_gid,
        )
        return

    logger.info(
        "%sFetching comments and attachments for %s task(s) in '%s'...",
        prefix,
        len(task_ids),
        project_name,
    )

    def worker(task_gid: str) -> tuple[str, List[Dict], List[Dict]]:
        comments = fetch_task_comments(client, task_gid)
        attachments = fetch_task_attachments(client, task_gid)
        return task_gid, comments, attachments

    processed = 0
    committed_since = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        future_map = {executor.submit(worker, task_gid): task_gid for task_gid in task_ids}
        for future in as_completed(future_map):
            task_gid = future_map[future]
            try:
                _, comments, attachments = future.result()
            except Exception as exc:  # noqa: BLE001
                logger.error("%sFailed to fetch extras for task %s: %s", prefix, task_gid, exc)
                continue

            user_gids = collect_user_gids(comments, attachments)
            user_backfill.ensure_users(connection, user_gids, sync_time)

            database.replace_task_comments(connection, task_gid, comments, sync_time)
            database.replace_task_attachments(connection, task_gid, attachments, sync_time)

            processed += 1
            committed_since += 1
            if committed_since >= COMMIT_INTERVAL:
                connection.commit()
                committed_since = 0

            if processed == 1 or processed % PROGRESS_INTERVAL == 0:
                logger.info("%sProcessed extras for %s task(s)...", prefix, processed)

    if committed_since:
        connection.commit()

    logger.info(
        "%sCompleted extras sync for '%s'. Tasks processed: %s.",
        prefix,
        project_name,
        processed,
    )


def main(argv: Optional[Iterable[str]] = None) -> None:
    args = parse_args(argv)
    if not args.personal and not args.personal_assignees:
        validate_scope_args(
            args.project_gid,
            args.team,
            args.team_gids,
            args.all_teams,
            args.exclude_team_gids,
            None,
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
    database = Database(db_path=resolve_default_db_path(DEFAULT_DB_PATH))
    sync_time = synced_at_timestamp()

    with database.connect() as connection:
        database.initialize(connection)
        user_backfill = UserBackfill(client, database)
        user_backfill.prime(connection)

        projects: List[Dict[str, Optional[str]]] = []
        if args.personal or args.personal_assignees:
            assignee_gids: List[str] = []
            if args.personal_assignees:
                assignee_gids = [gid.strip() for gid in args.personal_assignees.split(",") if gid.strip()]
            if assignee_gids:
                placeholders = ",".join("?" for _ in assignee_gids)
                rows = connection.execute(
                    f"""
                    SELECT gid, name, team_gid, NULL AS team_name
                    FROM projects
                    WHERE gid IN ({placeholders})
                      AND is_personal_project = 1
                    """,
                    assignee_gids,
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT gid, name, team_gid, NULL AS team_name
                    FROM projects
                    WHERE is_personal_project = 1
                    ORDER BY name
                    """
                ).fetchall()
            if not rows:
                logger.warning("No personal projects found. Run get_personal_tasks first.")
            else:
                projects = [
                    {
                        "project_gid": row["gid"],
                        "project_name": row["name"],
                        "team_gid": row["team_gid"],
                        "team_name": row["team_name"],
                    }
                    for row in rows
                    if row["gid"]
                ]
        else:
            projects = determine_projects(
                connection=connection,
                project_gid=args.project_gid,
                use_team_picker=args.team,
                team_gids=args.team_gids,
                all_teams=args.all_teams,
                exclude_team_gids=args.exclude_team_gids,
                team_selector=choose_team,
            )

        if not projects:
            logger.warning("No projects selected for extras sync.")
            return

        logger.info(
            "Preparing to sync extras for %s project(s) in workspace %s.",
            len(projects),
            workspace_gid,
        )
        for project in projects:
            process_project(
                connection=connection,
                database=database,
                client=client,
                user_backfill=user_backfill,
                project=project,
                workers=args.workers,
                sync_time=sync_time,
            )
        connection.commit()

    logger.info("Task extras sync completed.")


if __name__ == "__main__":
    main()
