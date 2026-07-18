#!/usr/bin/env python3
"""Set the "Last working day" date field on migrated project Jira issues.

For every Asana project that was migrated to a Jira **Task** (the
``team_project_jira_mappings`` rows with ``kind='project'``), this PUTs the
project's most recent task-update date — ``MAX(tasks.modified_at)``, the same value
the project last-updated CSV report computes — onto the Jira issue's date custom
field (``customfield_11475`` / "Last working day") via
``PUT /rest/api/3/issue/{key}``, editing the issues in place (comments, attachments,
and history are preserved — no delete/recreate).

Projects with no tasks (no last-updated date) are skipped, leaving their Jira field
untouched. Unmapped projects are skipped too. The field is written as a plain
``YYYY-MM-DD`` date string, which is what a Jira date custom field expects on edit;
the field must be on the issue's edit screen or the per-issue PUT is reported as an
error (the run continues). Defaults to the sandbox Jira site; pass ``--prod`` to
target production. Mirrors ``update_personal_project_jira_urls.py``.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.lib.config import load_credentials, resolve_jira_base_url
from src.lib.database import Database, resolve_default_db_path
from src.lib.date_formatting import format_report_date
from src.lib.jira_client import JiraClient
from src.lib.logging_utils import configure_logging, resolve_log_level
from scripts.export_project_last_updated_report import fetch_project_last_updated
from scripts.atlassian.create_team_project_jira_issues import (
    DEFAULT_WORKERS,
    ensure_atlassian_credentials,
)
from scripts.atlassian.update_personal_project_jira_urls import _run_batch

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = "data/asana.sqlite"
DEFAULT_OUTPUT_DIR = "exports"

# "Last working day" date custom field the last-updated date is written to.
DEFAULT_FIELD_ID = "customfield_11475"

REPORT_FIELDNAMES = ["project_gid", "project_name", "jira_key", "last_updated", "status", "message"]


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default=resolve_default_db_path(), help=f"Path to the SQLite database (default: {DEFAULT_DB_PATH}).")
    parser.add_argument(
        "--prod",
        action="store_true",
        help="Target the production Jira site (JIRA_BASE_URL). Default: sandbox (JIRA_SANDBOX_BASE_URL).",
    )
    parser.add_argument("--field-id", default=DEFAULT_FIELD_ID, help=f"Jira date custom field id to set (default: {DEFAULT_FIELD_ID}).")
    parser.add_argument("--team-gid", help="Optional team GID to scope the update to one team's projects.")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help=f"Concurrent workers for the update pass (default: {DEFAULT_WORKERS}).")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help=f"Directory for the run report CSV (default: {DEFAULT_OUTPUT_DIR}/).")
    parser.add_argument("--limit", type=int, help="Cap the number of issues updated this run.")
    parser.add_argument("--dry-run", action="store_true", help="Resolve the values and print the JSON payloads without updating issues.")
    parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt before updating issues.")
    parser.add_argument("--log-level", default="INFO", help="Logging verbosity (default: INFO).")
    return parser.parse_args(argv)


def _result(project_gid: str, project_name: str, jira_key: str, last_updated: str, status: str, message: str) -> Dict[str, str]:
    return {
        "project_gid": project_gid,
        "project_name": project_name,
        "jira_key": jira_key,
        "last_updated": last_updated,
        "status": status,
        "message": message,
    }


def write_report(rows: List[Dict[str, str]], output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=REPORT_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)
    return output_path


def set_project_last_updated_jira_field(
    database_path: str,
    jira_base_url: str,
    output_dir: Path,
    *,
    field_id: str = DEFAULT_FIELD_ID,
    team_gid: Optional[str] = None,
    workers: int = DEFAULT_WORKERS,
    limit: Optional[int] = None,
    dry_run: bool = False,
    assume_yes: bool = False,
    client: Optional[JiraClient] = None,
) -> Path:
    credentials = load_credentials()
    username, token = ensure_atlassian_credentials(credentials)

    database = Database(db_path=database_path)
    with database.connect() as connection:
        database.initialize(connection)
        project_rows = fetch_project_last_updated(connection, team_gid)
        mappings = database.load_team_project_jira_mappings(connection)

    # Reverse map: Asana project GID -> Jira Task key (kind='project' only).
    project_to_key = {
        asana_gid: jira_key
        for (kind, asana_gid), jira_key in mappings.items()
        if kind == "project" and asana_gid
    }

    results: List[Dict[str, str]] = []
    pending: List[Dict[str, object]] = []  # {project_gid, project_name, jira_key, date_str, payload}
    for row in project_rows:
        project_gid = row["project_gid"]
        project_name = row["project_name"]
        jira_key = project_to_key.get(project_gid)
        date_str = format_report_date(row["latest_task_update"])
        if not jira_key:
            results.append(_result(project_gid, project_name, "", date_str, "skipped-unmapped", "no Jira issue mapped"))
            continue
        if not date_str:
            results.append(_result(project_gid, project_name, jira_key, "", "skipped-no-date", "project has no tasks / last-updated date"))
            continue
        payload = {"fields": {field_id: date_str}}
        pending.append(
            {
                "project_gid": project_gid,
                "project_name": project_name,
                "jira_key": jira_key,
                "date_str": date_str,
                "payload": payload,
            }
        )

    if limit is not None and len(pending) > limit:
        for p in pending[limit:]:
            results.append(_result(p["project_gid"], p["project_name"], p["jira_key"], p["date_str"], "skipped-limit", ""))
        pending = pending[:limit]

    logger.info(
        "%s issue(s) pending update; %s skipped (unmapped/no-date/limit).",
        len(pending),
        len(results),
    )

    if dry_run:
        for p in pending:
            logger.info("[dry-run] %s payload:\n%s", p["jira_key"], json.dumps(p["payload"], indent=2))
            results.append(_result(p["project_gid"], p["project_name"], p["jira_key"], p["date_str"], "dry-run-updated", ""))
    elif pending:
        if client is None:
            client = JiraClient(
                base_url=jira_base_url,
                username=username,
                token=token,
                skip_ssl_verification=credentials.skip_ssl_verification,
            )
        if not assume_yes:
            answer = input(
                f"About to set {field_id} on {len(pending)} Jira issue(s). Continue? [y/N] "
            ).strip().lower()
            if answer not in {"y", "yes"}:
                raise SystemExit("Aborted by user.")

        outcomes = _run_batch(client, [(p["jira_key"], p["payload"]) for p in pending], workers, "update")
        for p in pending:
            error = outcomes.get(p["jira_key"])
            if error:
                results.append(_result(p["project_gid"], p["project_name"], p["jira_key"], p["date_str"], "error", error))
            else:
                results.append(_result(p["project_gid"], p["project_name"], p["jira_key"], p["date_str"], "updated", ""))

    report_path = write_report(results, output_dir / "project_last_updated_jira_update_report.csv")
    return report_path


def main(argv: Optional[Iterable[str]] = None) -> None:
    args = parse_args(argv)
    try:
        log_level = resolve_log_level(args.log_level)
    except ValueError as exc:  # noqa: BLE001
        raise SystemExit(str(exc)) from exc
    configure_logging(log_level)

    try:
        jira_base_url = resolve_jira_base_url(load_credentials(), prod=args.prod)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    try:
        report_path = set_project_last_updated_jira_field(
            database_path=args.database,
            jira_base_url=jira_base_url,
            output_dir=Path(args.output_dir),
            field_id=args.field_id,
            team_gid=args.team_gid,
            workers=max(args.workers, 1),
            limit=args.limit,
            dry_run=args.dry_run,
            assume_yes=args.yes,
        )
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(str(exc)) from exc

    logger.info("Project last-updated Jira field update finished: %s", report_path)


if __name__ == "__main__":
    main()
