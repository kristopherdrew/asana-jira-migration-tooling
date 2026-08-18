#!/usr/bin/env python3
"""CLI script to fetch Asana projects and persist them to SQLite."""

from __future__ import annotations

import argparse
import logging
from typing import Iterable, Optional

from src.lib.config import load_credentials
from src.lib.database import Database, synced_at_timestamp, resolve_default_db_path
from src.lib.http_client import AsanaHttpClient
from src.lib.logging_utils import configure_logging, resolve_log_level
from src.tasks.projects import fetch_project_task_counts, fetch_team_projects, fetch_workspace_projects


DEFAULT_CONFIG_ROOT = "."
DEFAULT_DB_PATH = "data/asana.sqlite"
PROJECT_PAGE_SIZE = 100
ARCHIVE_SCOPES = ("active", "archived", "all")
PROGRESS_INTERVAL = 10
logger = logging.getLogger(__name__)


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workspace-gid",
        help="Override the workspace GID from the resolved credentials.",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=PROJECT_PAGE_SIZE,
        help=f"Page size for API pagination (default: {PROJECT_PAGE_SIZE}).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging verbosity (default: INFO).",
    )
    parser.add_argument(
        "--archived-scope",
        choices=ARCHIVE_SCOPES,
        default="active",
        help="Which projects to include: active (default), archived, or all.",
    )
    parser.add_argument(
        "--team-gids",
        help="Comma-separated team GIDs whose projects to sync instead of the workspace.",
    )
    return parser.parse_args(argv)


def _archived_filters(scope: str) -> Iterable[Optional[bool]]:
    if scope == "archived":
        return [True]
    if scope == "all":
        return [False, True]
    return [False]


def main(argv: Optional[Iterable[str]] = None) -> None:
    args = parse_args(argv)
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

    project_count = 0
    custom_field_links = 0
    custom_field_setting_links = 0
    team_gids = [gid.strip() for gid in (args.team_gids or "").split(",") if gid.strip()]

    logger.info("Starting project sync for workspace %s", workspace_gid)

    with database.connect() as connection:
        database.initialize(connection)

        seen_project_gids = set()
        for archived_flag in _archived_filters(args.archived_scope):
            scope_label = (
                "archived"
                if archived_flag is True
                else "active"
                if archived_flag is False
                else "all"
            )
            logger.info("Fetching %s projects%s...", scope_label, " for selected teams" if team_gids else "")
            project_sources = (
                (
                    project
                    for team_gid in team_gids
                    for project in fetch_team_projects(client, team_gid, page_size=args.page_size, archived=archived_flag)
                )
                if team_gids
                else fetch_workspace_projects(client, workspace_gid, page_size=args.page_size, archived=archived_flag)
            )
            for project in project_sources:
                project_gid = project.get("gid")
                if project_gid in seen_project_gids:
                    continue
                seen_project_gids.add(project_gid)
                if project_gid:
                    try:
                        project.update(fetch_project_task_counts(client, project_gid))
                    except Exception as exc:  # pragma: no cover - defensive
                        logger.warning("Unable to fetch task counts for project %s: %s", project_gid, exc)
                database.upsert_project(connection, project, sync_time)
                project_count += 1
                custom_field_links += len(project.get("custom_fields") or [])
                custom_field_setting_links += len(project.get("custom_field_settings") or [])

                if project_count == 1 or project_count % PROGRESS_INTERVAL == 0:
                    logger.info("Processed %s project(s)...", project_count)

        connection.commit()

    logger.info("Synced %s projects into %s.", project_count, DEFAULT_DB_PATH)
    logger.info(
        "Captured %s project custom field entries and %s custom field settings.",
        custom_field_links,
        custom_field_setting_links,
    )


if __name__ == "__main__":
    main()
