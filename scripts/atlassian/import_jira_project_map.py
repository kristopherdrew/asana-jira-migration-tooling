#!/usr/bin/env python3
"""Import Jira project associations (project_gid, project_name, project_key) into SQLite."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Dict

PROJECT_ROOT = Path(__file__).resolve().parent.parent

import os
import sys
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.lib.database import Database, resolve_default_db_path  # noqa: E402
from src.lib.logging_utils import configure_logging, resolve_log_level  # noqa: E402

DEFAULT_DB_PATH = "data/asana.sqlite"


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True, help="Path to CSV with project_gid,project_name,project_key")
    parser.add_argument("--database", default=resolve_default_db_path(), help=f"Path to SQLite DB (default: {DEFAULT_DB_PATH}).")
    parser.add_argument("--log-level", default="INFO", help="Logging verbosity (default: INFO).")
    parser.add_argument("--jira-site", help="Named Jira site key for these mappings.")
    return parser.parse_args(argv)


def read_rows(csv_path: Path) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    with csv_path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            rows.append({
                "project_gid": (row.get("project_gid") or "").strip(),
                "project_name": (row.get("project_name") or "").strip(),
                "project_key": (row.get("project_key") or "").strip(),
            })
    return rows


def main(argv: Iterable[str] | None = None) -> None:
    args = parse_args(argv)
    log_level = resolve_log_level(args.log_level)
    configure_logging(log_level)

    csv_path = Path(args.csv)
    if not csv_path.exists():
        raise SystemExit(f"CSV not found: {csv_path}")

    mappings = read_rows(csv_path)
    database = Database(db_path=args.database)
    now = datetime.now(timezone.utc)
    with database.connect() as conn:
        database.initialize(conn)
        count = database.upsert_jira_project_mappings(conn, mappings, now, jira_site_key=args.jira_site)
    print(f"Upserted {count} Jira project association(s) from {csv_path}")


if __name__ == "__main__":  # pragma: no cover
    main()
