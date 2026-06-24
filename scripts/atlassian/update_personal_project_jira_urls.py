#!/usr/bin/env python3
"""Repair the ``Project (Asana)`` field on already-created personal-project Jira issues.

Personal task trackers were originally exported with a synthetic
``https://app.asana.com/1/<ws>/project/personal_<user_gid>`` URL that 404s because
no real Asana project backs it. Now that ``get_users`` captures each user's My Tasks
list GID, ``build_team_project_rows`` emits a working
``https://app.asana.com/0/<user_task_list_gid>/list`` link instead. This script PUTs
that corrected value onto the existing Jira issues in place (``PUT /rest/api/3/issue``),
preserving their comments, attachments, and history — no delete/recreate.

Only personal-project Task issues that already have an Asana->Jira mapping are
touched, and only when the row resolves to a real My Tasks link; rows that would
fall back to a profile URL or blank are skipped and flagged for manual review.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.lib.config import load_credentials
from src.lib.database import Database
from src.lib.jira_client import JiraApiError, JiraClient
from src.lib.logging_utils import configure_logging, resolve_log_level
from scripts.atlassian.export_team_project_jira_csv import build_team_project_rows
from scripts.atlassian.create_team_project_jira_issues import (
    DEFAULT_WORKERS,
    MAX_RATE_LIMIT_ROUNDS,
    RATE_LIMIT_REQUEUE_DELAY,
    _is_rate_limited,
    _resolve_field_def,
    ensure_atlassian_credentials,
    format_custom_value,
    load_field_map,
    resolve_issue_type_id,
)

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = "data/asana.sqlite"
DEFAULT_OUTPUT_DIR = "exports"

# The column whose value we repair, and the Jira field name it maps to by default
# (overridable via --field-map, mirroring create_team_project_jira_issues).
TARGET_COLUMN = "Project URL"

# A real "My Tasks" deep link looks like https://app.asana.com/0/<gid>/list. Any
# other value (the profile-URL fallback or blank) is left for manual review.
MY_TASKS_URL_PREFIX = "https://app.asana.com/0/"
MY_TASKS_URL_SUFFIX = "/list"

REPORT_FIELDNAMES = ["asana_gid", "jira_key", "project_url", "status", "message"]


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default=DEFAULT_DB_PATH, help=f"Path to the SQLite database (default: {DEFAULT_DB_PATH}).")
    parser.add_argument("--workspace-gid", help="Optional Asana workspace GID used when building URLs.")
    parser.add_argument("--jira-base-url", required=True, help="Base URL for the Jira site (e.g., https://example.atlassian.net).")
    parser.add_argument("--jira-project-key", required=True, help="Jira project key the issues live in (e.g., MIG).")
    parser.add_argument("--task-issue-type", default="Task", help="Issue type name the project issues use (default: Task).")
    parser.add_argument("--field-map", help="Optional JSON file overriding column -> Jira custom field name/id mappings.")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help=f"Concurrent workers for the update pass (default: {DEFAULT_WORKERS}).")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help=f"Directory for the run report CSV (default: {DEFAULT_OUTPUT_DIR}/).")
    parser.add_argument("--limit", type=int, help="Cap the number of issues updated this run.")
    parser.add_argument("--dry-run", action="store_true", help="Resolve the field and print the JSON payloads without updating issues.")
    parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt before updating issues.")
    parser.add_argument("--log-level", default="INFO", help="Logging verbosity (default: INFO).")
    return parser.parse_args(argv)


def _is_my_tasks_url(url: str) -> bool:
    return url.startswith(MY_TASKS_URL_PREFIX) and url.endswith(MY_TASKS_URL_SUFFIX)


def _result(asana_gid: str, jira_key: str, project_url: str, status: str, message: str) -> Dict[str, str]:
    return {
        "asana_gid": asana_gid,
        "jira_key": jira_key,
        "project_url": project_url,
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


def update_personal_project_jira_urls(
    database_path: str,
    jira_base_url: str,
    jira_project_key: str,
    output_dir: Path,
    *,
    workspace_gid: Optional[str] = None,
    task_issue_type: str = "Task",
    field_map_path: Optional[str] = None,
    workers: int = DEFAULT_WORKERS,
    limit: Optional[int] = None,
    dry_run: bool = False,
    assume_yes: bool = False,
    client: Optional[JiraClient] = None,
) -> Path:
    credentials = load_credentials()
    username, token = ensure_atlassian_credentials(credentials)
    custom_field_map = load_field_map(field_map_path)
    field_ref = custom_field_map.get(TARGET_COLUMN)
    if not field_ref:
        raise ValueError(f"No Jira field mapping found for the '{TARGET_COLUMN}' column.")

    database = Database(db_path=database_path)
    with database.connect() as connection:
        database.initialize(connection)
        _epic_rows, task_rows = build_team_project_rows(connection, workspace_gid)
        existing = database.load_team_project_jira_mappings(connection)

        personal_rows = [r for r in task_rows if r.get("Project Type") == "personal_project"]
        logger.info("Found %s personal-project task row(s).", len(personal_rows))

        if client is None:
            client = JiraClient(
                base_url=jira_base_url,
                username=username,
                token=token,
                skip_ssl_verification=credentials.skip_ssl_verification,
            )

        issue_types = client.get_createmeta_issue_types(jira_project_key)
        task_type_id = resolve_issue_type_id(issue_types, task_issue_type)
        task_fields = client.get_createmeta_fields(jira_project_key, task_type_id)
        defn = _resolve_field_def(task_fields, field_ref)
        if defn is None:
            raise ValueError(
                f"Jira field '{field_ref}' (for column '{TARGET_COLUMN}') is not available "
                f"on the {task_issue_type} screen; cannot update."
            )
        field_id = str(defn.get("fieldId"))
        schema = defn.get("schema") or {}

        # Decide what to do with each personal row before touching Jira.
        results: List[Dict[str, str]] = []
        pending: List[Tuple[str, str, str, Dict[str, object]]] = []  # (asana_gid, jira_key, url, payload)
        for row in personal_rows:
            asana_gid = row["_asana_gid"]
            url = (row.get(TARGET_COLUMN) or "").strip()
            jira_key = existing.get((row["_kind"], asana_gid))
            if not jira_key:
                results.append(_result(asana_gid, "", url, "skipped-unmapped", "no Jira issue mapped"))
                continue
            if not _is_my_tasks_url(url):
                results.append(
                    _result(asana_gid, jira_key, url, "skipped-fallback", "no My Tasks link (profile/blank)")
                )
                continue
            payload = {"fields": {field_id: format_custom_value(schema, url)}}
            pending.append((asana_gid, jira_key, url, payload))

        if limit is not None and len(pending) > limit:
            for asana_gid, jira_key, url, _payload in pending[limit:]:
                results.append(_result(asana_gid, jira_key, url, "skipped-limit", ""))
            pending = pending[:limit]

        logger.info(
            "%s issue(s) pending update; %s skipped (unmapped/fallback/limit).",
            len(pending),
            len(results),
        )

        if dry_run:
            for asana_gid, jira_key, url, payload in pending:
                logger.info("[dry-run] %s update payload:\n%s", jira_key, json.dumps(payload, indent=2))
                results.append(_result(asana_gid, jira_key, url, "dry-run", ""))
        elif pending:
            if not assume_yes:
                answer = input(
                    f"About to update Project (Asana) on {len(pending)} issue(s) in {jira_project_key}. Continue? [y/N] "
                ).strip().lower()
                if answer not in {"y", "yes"}:
                    raise SystemExit("Aborted by user.")

            def worker(item: Tuple[str, str, str, Dict[str, object]]) -> Dict[str, object]:
                asana_gid, jira_key, url, payload = item
                try:
                    client.update_issue(jira_key, payload)
                    return {"item": item, "error": None, "rate_limited": False}
                except JiraApiError as exc:
                    return {"item": item, "error": str(exc), "rate_limited": _is_rate_limited(exc)}

            # Rate-limited updates are requeued and retried in later rounds.
            queue = list(pending)
            update_round = 0
            while queue:
                requeue: List[Tuple[str, str, str, Dict[str, object]]] = []
                with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
                    future_map = {executor.submit(worker, item): item for item in queue}
                    for future in as_completed(future_map):
                        res = future.result()
                        asana_gid, jira_key, url, _payload = res["item"]
                        if res["rate_limited"] and update_round < MAX_RATE_LIMIT_ROUNDS:
                            requeue.append(res["item"])
                            logger.warning("Rate limited updating %s; requeued (round %s).", jira_key, update_round + 1)
                            continue
                        if res["error"]:
                            results.append(_result(asana_gid, jira_key, url, "error", res["error"]))
                            logger.error("Failed to update %s: %s", jira_key, res["error"])
                            continue
                        results.append(_result(asana_gid, jira_key, url, "updated", ""))
                        logger.info("Updated %s -> %s", jira_key, url)
                if not requeue:
                    break
                update_round += 1
                time.sleep(RATE_LIMIT_REQUEUE_DELAY * update_round)
                queue = requeue

    report_path = write_report(results, output_dir / "personal_project_url_update_report.csv")
    return report_path


def main(argv: Optional[Iterable[str]] = None) -> None:
    args = parse_args(argv)
    try:
        log_level = resolve_log_level(args.log_level)
    except ValueError as exc:  # noqa: BLE001
        raise SystemExit(str(exc)) from exc
    configure_logging(log_level)

    try:
        report_path = update_personal_project_jira_urls(
            database_path=args.database,
            jira_base_url=args.jira_base_url,
            jira_project_key=args.jira_project_key,
            output_dir=Path(args.output_dir),
            workspace_gid=args.workspace_gid,
            task_issue_type=args.task_issue_type,
            field_map_path=args.field_map,
            workers=max(args.workers, 1),
            limit=args.limit,
            dry_run=args.dry_run,
            assume_yes=args.yes,
        )
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(str(exc)) from exc

    logger.info("Personal-project Project URL update finished: %s", report_path)


if __name__ == "__main__":
    main()
