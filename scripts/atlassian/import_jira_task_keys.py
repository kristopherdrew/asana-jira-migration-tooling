#!/usr/bin/env python3
"""Import Jira issue keys mapped to Asana task IDs into SQLite."""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.lib.database import Database, DEFAULT_DB_PATH, resolve_default_db_path
from src.lib.logging_utils import configure_logging, resolve_log_level


logger = logging.getLogger(__name__)
DEFAULT_JIRA_COLUMN = "Issue key"
DEFAULT_TASK_COLUMN = "External issue ID"


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        default=resolve_default_db_path(),
        help="Path to the SQLite database (default: data/asana.sqlite).",
    )
    parser.add_argument(
        "--csv",
        required=True,
        help=(
            "Path to the Jira CSV export (must contain 'Issue key' and the task column you specify; "
            "defaults to 'External issue ID')."
        ),
    )
    parser.add_argument(
        "--jira-column",
        default=DEFAULT_JIRA_COLUMN,
        help=(
            "CSV header for the Jira key (default: 'Issue key'). Use if your export labels it differently."
        ),
    )
    parser.add_argument(
        "--task-column",
        default=DEFAULT_TASK_COLUMN,
        help=(
            "CSV header that contains the Asana task identifier (default: 'External issue ID'; "
            "override if your export labels the field differently, e.g. 'Custom field (Asana Task ID)')."
        ),
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging verbosity (default: INFO).",
    )
    parser.add_argument("--jira-site", help="Named Jira site key for these mappings.")
    return parser.parse_args(argv)


def import_jira_keys_from_csv(
    database_path: str,
    csv_path: Path,
    jira_column: str = DEFAULT_JIRA_COLUMN,
    task_column: str = DEFAULT_TASK_COLUMN,
    jira_site_key: Optional[str] = None,
) -> Dict[str, int]:
    csv_path = csv_path.resolve()
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    rows = _read_mapping_rows(csv_path, jira_column, task_column)
    logger.info("Read %s row(s) from %s", len(rows), csv_path)

    database = Database(db_path=database_path)
    with database.connect() as connection:
        database.initialize(connection)

        known_tasks = _known_task_gids(connection)
        if not known_tasks:
            logger.warning("No tasks found in %s; inserting Jira keys without validation.", database_path)

        filtered_rows: List[Dict[str, str]] = []
        for entry in rows:
            if known_tasks and entry["task_gid"] not in known_tasks:
                logger.warning(
                    "Task %s not found in database; skipping Jira key %s.",
                    entry["task_gid"],
                    entry["jira_key"],
                )
                continue
            filtered_rows.append(entry)

        sync_time = datetime.now(timezone.utc)
        summary = database.upsert_task_jira_mappings(
            connection,
            filtered_rows,
            sync_time,
            jira_site_key=jira_site_key,
        )

    written = summary["written"]
    total_processed = len(rows)
    logger.info(
        "Stored %s Jira key(s) (processed=%s, skipped=%s).",
        written,
        total_processed,
        total_processed - written,
    )
    return {"processed": total_processed, "written": written, "skipped": total_processed - written}


def _read_mapping_rows(
    csv_path: Path,
    jira_column: str,
    task_column: str,
) -> List[Dict[str, str]]:
    with csv_path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError("CSV file is missing a header row.")

        reader.fieldnames = [_normalize_header(name) for name in reader.fieldnames]
        header_set = set(reader.fieldnames)
        if jira_column not in header_set:
            raise ValueError(f"Column '{jira_column}' not found in CSV header.")
        if task_column not in header_set:
            raise ValueError(f"Column '{task_column}' not found in CSV header.")

        rows: List[Dict[str, str]] = []
        seen_jira: Dict[str, int] = {}
        seen_tasks: Dict[str, int] = {}
        for index, row in enumerate(reader, start=2):
            jira_key = (row.get(jira_column) or "").strip()
            task_gid = (row.get(task_column) or "").strip()
            if not jira_key or not task_gid:
                logger.debug("Skipping blank row %s (jira_key=%s, task_gid=%s)", index, jira_key, task_gid)
                continue
            if jira_key in seen_jira:
                raise ValueError(
                    f"Jira key '{jira_key}' duplicated in rows {seen_jira[jira_key]} and {index}."
                )
            if task_gid in seen_tasks:
                raise ValueError(
                    f"Asana task '{task_gid}' duplicated in rows {seen_tasks[task_gid]} and {index}."
                )
            seen_jira[jira_key] = index
            seen_tasks[task_gid] = index
            rows.append({"jira_key": jira_key, "task_gid": task_gid})
    return rows


def _normalize_header(name: str) -> str:
    return name.lstrip("\ufeff").strip()


def _known_task_gids(connection) -> set[str]:
    rows = connection.execute("SELECT gid FROM tasks").fetchall()
    return {row["gid"] for row in rows}


def main(argv: Optional[Iterable[str]] = None) -> None:
    args = parse_args(argv)
    try:
        log_level = resolve_log_level(args.log_level)
    except ValueError as exc:  # noqa: BLE001
        raise SystemExit(str(exc)) from exc
    configure_logging(log_level)

    try:
        summary = import_jira_keys_from_csv(
            database_path=args.database,
            csv_path=Path(args.csv),
            jira_column=args.jira_column,
            task_column=args.task_column,
            jira_site_key=args.jira_site,
        )
    except FileNotFoundError as exc:
        raise SystemExit(str(exc)) from exc
    except ValueError as exc:  # noqa: BLE001
        raise SystemExit(str(exc)) from exc

    logger.info(
        "Import complete: processed=%s written=%s skipped=%s",
        summary["processed"],
        summary["written"],
        summary["skipped"],
    )


if __name__ == "__main__":
    main()
