#!/usr/bin/env python3
"""Export recurring tasks (Asana's undocumented `recurrence` field) for a set of project GIDs.

DB-first, live-Asana-fallback: for each project GID, reads recurrence from the local SQLite
snapshot when it's already been captured (`tasks.recurrence`/`recurrence_checked_at`, populated
by the normal task sync), and only queries Asana directly to fill gaps — an unsynced project
falls back to a full live walk, while individual tasks synced before this column existed get a
single-task live lookup. Live-queried values are written back to SQLite so re-running the report
against the same projects later needs no further API calls. Writes three CSVs: one row per
recurring task instance found, a best-effort series summary grouping instances by project +
task name + recurrence pattern (Asana exposes no true series identifier, so the grouping is a
heuristic), and an error report of every task whose live recurrence lookup failed (e.g. 403/404)
with its status and message, for direct investigation.
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.lib.config import load_credentials
from src.lib.database import Database, resolve_default_db_path
from src.lib.http_client import AsanaHttpClient, AsanaHttpError
from src.lib.logging_utils import configure_logging, resolve_log_level
from src.lib.project_scope import parse_gid_list, read_project_gids_file
from src.tasks.recurring_tasks import (
    RecurringTaskInstance,
    RecurringTaskLookupError,
    RecurringTaskSeries,
    collect_recurring_task_instances_hybrid,
    group_into_series,
)


DEFAULT_CONFIG_ROOT = "."
DEFAULT_OUTPUT_DIR = "exports"
DEFAULT_PAGE_SIZE = 100
INSTANCE_FIELDNAMES = [
    "project_gid",
    "project_name",
    "task_gid",
    "task_name",
    "task_type",
    "parent_gid",
    "parent_name",
    "recurrence_type",
    "recurrence_frequency",
    "due_on",
    "completed",
    "permalink_url",
]
SERIES_FIELDNAMES = [
    "project_gid",
    "project_name",
    "task_name",
    "recurrence_type",
    "recurrence_frequency",
    "occurrence_count",
    "first_due_on",
    "last_due_on",
]
ERROR_FIELDNAMES = [
    "project_gid",
    "project_name",
    "task_gid",
    "task_name",
    "status",
    "message",
]
logger = logging.getLogger(__name__)


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-gids", action="append", default=None, help="Comma-separated project GIDs to scan (may be repeated).")
    parser.add_argument("--project-gids-file", help="UTF-8 file containing comma- or newline-separated project GIDs.")
    parser.add_argument(
        "--database",
        default=resolve_default_db_path(),
        help="Path to the SQLite database (default: data/asana.sqlite). Recurrence is read from here "
        "when already synced, and live-queried gaps are written back to it.",
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help="Directory to store the CSV exports (default: exports/).",
    )
    parser.add_argument(
        "--filename",
        help="Optional filename for the instances CSV. Defaults to recurring_tasks_instances_<timestamp>.csv. "
        "The series summary is derived from this name.",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=DEFAULT_PAGE_SIZE,
        help=f"Page size for task/subtask pagination (default: {DEFAULT_PAGE_SIZE}).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging verbosity (default: INFO).",
    )
    return parser.parse_args(argv)


def write_instances_csv(path: Path, instances: List[RecurringTaskInstance]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=INSTANCE_FIELDNAMES)
        writer.writeheader()
        for instance in instances:
            writer.writerow(
                {
                    "project_gid": instance.project_gid,
                    "project_name": instance.project_name,
                    "task_gid": instance.task_gid,
                    "task_name": instance.task_name,
                    "task_type": instance.task_type,
                    "parent_gid": instance.parent_gid,
                    "parent_name": instance.parent_name,
                    "recurrence_type": instance.recurrence_type,
                    "recurrence_frequency": instance.recurrence_frequency,
                    "due_on": instance.due_on,
                    "completed": instance.completed,
                    "permalink_url": instance.permalink_url,
                }
            )


def write_series_csv(path: Path, series: List[RecurringTaskSeries]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SERIES_FIELDNAMES)
        writer.writeheader()
        for row in series:
            writer.writerow(
                {
                    "project_gid": row.project_gid,
                    "project_name": row.project_name,
                    "task_name": row.task_name,
                    "recurrence_type": row.recurrence_type,
                    "recurrence_frequency": row.recurrence_frequency,
                    "occurrence_count": row.occurrence_count,
                    "first_due_on": row.first_due_on,
                    "last_due_on": row.last_due_on,
                }
            )


def write_errors_csv(path: Path, errors: List[RecurringTaskLookupError]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=ERROR_FIELDNAMES)
        writer.writeheader()
        for error in errors:
            writer.writerow(
                {
                    "project_gid": error.project_gid,
                    "project_name": error.project_name,
                    "task_gid": error.task_gid,
                    "task_name": error.task_name,
                    "status": error.status,
                    "message": error.message,
                }
            )


def export_recurring_tasks_report(
    client: AsanaHttpClient,
    database: Database,
    project_gids: List[str],
    output_dir: Path,
    filename: Optional[str] = None,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> tuple[Path, Path, Path]:
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    if not filename:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        filename = f"recurring_tasks_instances_{timestamp}.csv"

    instances_path = output_dir / filename
    series_path = instances_path.with_name(f"{instances_path.stem}_series_summary{instances_path.suffix}")
    errors_path = instances_path.with_name(f"{instances_path.stem}_errors{instances_path.suffix}")

    all_instances: List[RecurringTaskInstance] = []
    all_errors: List[RecurringTaskLookupError] = []
    with database.connect() as connection:
        database.initialize(connection)
        for index, project_gid in enumerate(project_gids, start=1):
            logger.info("(%s/%s) Scanning project %s for recurring tasks...", index, len(project_gids), project_gid)
            try:
                instances, errors = collect_recurring_task_instances_hybrid(client, database, connection, project_gid, page_size=page_size)
            except AsanaHttpError as exc:
                logger.warning("Skipping project %s: %s", project_gid, exc)
                all_errors.append(
                    RecurringTaskLookupError(
                        project_gid=project_gid,
                        project_name="",
                        task_gid="",
                        task_name="",
                        status=str(exc.status),
                        message=str(exc),
                    )
                )
                continue
            logger.info(
                "Found %s recurring task instance(s) in project %s (%s lookup failure(s)).",
                len(instances),
                project_gid,
                len(errors),
            )
            all_instances.extend(instances)
            all_errors.extend(errors)

    write_instances_csv(instances_path, all_instances)
    series = group_into_series(all_instances)
    write_series_csv(series_path, series)
    write_errors_csv(errors_path, all_errors)

    logger.info(
        "Exported %s recurring task instance(s) across %s inferred series to %s and %s (%s lookup failure(s) written to %s). "
        "Series grouping is a best-effort heuristic (name + recurrence pattern) — Asana exposes no true series id.",
        len(all_instances),
        len(series),
        instances_path,
        series_path,
        len(all_errors),
        errors_path,
    )
    return instances_path, series_path, errors_path


def main(argv: Optional[Iterable[str]] = None) -> None:
    args = parse_args(argv)

    raw_project_gids = list(args.project_gids or [])
    if args.project_gids_file:
        raw_project_gids.append(read_project_gids_file(args.project_gids_file))
    project_gids = parse_gid_list(",".join(raw_project_gids)) if raw_project_gids else []
    if not project_gids:
        raise SystemExit("No project scope specified. Provide --project-gids or --project-gids-file.")

    try:
        log_level = resolve_log_level(args.log_level)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    configure_logging(log_level)

    credentials = load_credentials(DEFAULT_CONFIG_ROOT)
    client = AsanaHttpClient(token=credentials.token, skip_ssl_verification=credentials.skip_ssl_verification)
    database = Database(db_path=args.database)

    instances_path, series_path, errors_path = export_recurring_tasks_report(
        client=client,
        database=database,
        project_gids=project_gids,
        output_dir=Path(args.output_dir),
        filename=args.filename,
        page_size=args.page_size,
    )
    logger.info("Recurring tasks report completed: %s, %s, %s", instances_path, series_path, errors_path)


if __name__ == "__main__":
    main()
