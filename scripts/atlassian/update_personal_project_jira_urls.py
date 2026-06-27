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
touched. A row that resolves to a real My Tasks link is ``updated``; a row where no
proper link can be generated is ``cleared`` (the dead synthetic URL is wiped rather
than left in place). With ``--clear-first`` the run is split into two passes — clear
the field on every in-scope issue, then set the new value on the ones that have a
link — so no stale value can survive even if the set pass misses an issue.
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

from src.lib.config import load_credentials, resolve_jira_base_url
from src.lib.database import Database, resolve_default_db_path
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
    parser.add_argument("--database", default=resolve_default_db_path(), help=f"Path to the SQLite database (default: {DEFAULT_DB_PATH}).")
    parser.add_argument("--workspace-gid", help="Optional Asana workspace GID used when building URLs.")
    parser.add_argument(
        "--prod",
        action="store_true",
        help="Target the production Jira site (JIRA_BASE_URL). Default: sandbox (JIRA_SANDBOX_BASE_URL).",
    )
    parser.add_argument("--jira-project-key", required=True, help="Jira project key the issues live in (e.g., MIG).")
    parser.add_argument("--task-issue-type", default="Task", help="Issue type name the project issues use (default: Task).")
    parser.add_argument("--field-map", help="Optional JSON file overriding column -> Jira custom field name/id mappings.")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help=f"Concurrent workers for the update pass (default: {DEFAULT_WORKERS}).")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help=f"Directory for the run report CSV (default: {DEFAULT_OUTPUT_DIR}/).")
    parser.add_argument("--limit", type=int, help="Cap the number of issues updated this run.")
    parser.add_argument("--clear-first", action="store_true", help="Clear the field on every in-scope issue first, then set the new values, so no stale value survives if the set pass misses an issue.")
    parser.add_argument("--dry-run", action="store_true", help="Resolve the field and print the JSON payloads without updating issues.")
    parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt before updating issues.")
    parser.add_argument("--log-level", default="INFO", help="Logging verbosity (default: INFO).")
    return parser.parse_args(argv)


def _is_my_tasks_url(url: str) -> bool:
    return url.startswith(MY_TASKS_URL_PREFIX) and url.endswith(MY_TASKS_URL_SUFFIX)


def _clear_value(schema: Dict[str, object]) -> object:
    """Return the value that clears a Jira field of this schema type.

    Array fields clear with ``[]``; everything else (the string/url custom field
    ``Project (Asana)`` uses) clears with ``null``."""
    return [] if str(schema.get("type")) == "array" else None


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


def _run_batch(
    client: JiraClient,
    items: List[Tuple[str, Dict[str, object]]],
    workers: int,
    log_label: str,
) -> Dict[str, Optional[str]]:
    """PUT each ``(jira_key, payload)`` item, returning ``jira_key -> error or None``.

    Runs concurrently across ``workers`` and requeues rate-limited items for a few
    more rounds (matching the create script's behavior)."""
    outcomes: Dict[str, Optional[str]] = {}

    def worker(item: Tuple[str, Dict[str, object]]) -> Tuple[Tuple[str, Dict[str, object]], Optional[str], bool]:
        jira_key, payload = item
        try:
            client.update_issue(jira_key, payload)
            return item, None, False
        except JiraApiError as exc:
            return item, str(exc), _is_rate_limited(exc)

    queue = list(items)
    round_no = 0
    while queue:
        requeue: List[Tuple[str, Dict[str, object]]] = []
        with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
            future_map = {executor.submit(worker, item): item for item in queue}
            for future in as_completed(future_map):
                item, error, rate_limited = future.result()
                jira_key = item[0]
                if rate_limited and round_no < MAX_RATE_LIMIT_ROUNDS:
                    requeue.append(item)
                    logger.warning("Rate limited (%s) %s; requeued (round %s).", log_label, jira_key, round_no + 1)
                    continue
                outcomes[jira_key] = error
                if error:
                    logger.error("Failed to %s %s: %s", log_label, jira_key, error)
                else:
                    logger.info("%s %s", log_label, jira_key)
        if not requeue:
            break
        round_no += 1
        time.sleep(RATE_LIMIT_REQUEUE_DELAY * round_no)
        queue = requeue
    return outcomes


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
    clear_first: bool = False,
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

        # Decide what to do with each personal row before touching Jira. Rows that
        # resolve to a real My Tasks link are `updated`; rows where no proper link
        # could be generated are `cleared` (the dead synthetic URL is wiped rather
        # than left in place). The same clear payload is reused for every issue.
        clear_payload: Dict[str, object] = {"fields": {field_id: _clear_value(schema)}}
        results: List[Dict[str, str]] = []
        pending: List[Dict[str, object]] = []  # {asana_gid, jira_key, url, action, set_payload}
        for row in personal_rows:
            asana_gid = row["_asana_gid"]
            url = (row.get(TARGET_COLUMN) or "").strip()
            jira_key = existing.get((row["_kind"], asana_gid))
            if not jira_key:
                results.append(_result(asana_gid, "", url, "skipped-unmapped", "no Jira issue mapped"))
                continue
            if _is_my_tasks_url(url):
                action = "updated"
                set_payload: Optional[Dict[str, object]] = {"fields": {field_id: format_custom_value(schema, url)}}
            else:
                action = "cleared"
                set_payload = None
            pending.append({"asana_gid": asana_gid, "jira_key": jira_key, "url": url, "action": action, "set_payload": set_payload})

        if limit is not None and len(pending) > limit:
            for p in pending[limit:]:
                results.append(_result(p["asana_gid"], p["jira_key"], p["url"], "skipped-limit", ""))
            pending = pending[:limit]

        logger.info(
            "%s issue(s) pending (%s); %s skipped (unmapped/limit).",
            len(pending),
            "clear-first then set" if clear_first else "single pass",
            len(results),
        )

        if dry_run:
            for p in pending:
                if clear_first:
                    logger.info("[dry-run] %s clear payload:\n%s", p["jira_key"], json.dumps(clear_payload, indent=2))
                payload = p["set_payload"] if p["action"] == "updated" else clear_payload
                logger.info("[dry-run] %s (%s) payload:\n%s", p["jira_key"], p["action"], json.dumps(payload, indent=2))
                results.append(_result(p["asana_gid"], p["jira_key"], p["url"], f"dry-run-{p['action']}", "clear-first" if clear_first else ""))
        elif pending:
            if not assume_yes:
                action_desc = "clear then update" if clear_first else "update"
                answer = input(
                    f"About to {action_desc} Project (Asana) on {len(pending)} issue(s) in {jira_project_key}. Continue? [y/N] "
                ).strip().lower()
                if answer not in {"y", "yes"}:
                    raise SystemExit("Aborted by user.")

            if clear_first:
                # Pass 1 — clear the field on every in-scope issue so no stale value
                # can survive. Pass 2 — set the new link on the ones that have one.
                clear_outcomes = _run_batch(
                    client, [(p["jira_key"], clear_payload) for p in pending], workers, "cleared"
                )
                set_outcomes = _run_batch(
                    client,
                    [(p["jira_key"], p["set_payload"]) for p in pending if p["action"] == "updated"],
                    workers,
                    "updated",
                )
                for p in pending:
                    key = p["jira_key"]
                    if p["action"] == "updated":
                        error = set_outcomes.get(key)
                        if error:
                            results.append(_result(p["asana_gid"], key, p["url"], "error", f"set failed (field left cleared): {error}"))
                        else:
                            results.append(_result(p["asana_gid"], key, p["url"], "updated", ""))
                    else:
                        error = clear_outcomes.get(key)
                        if error:
                            results.append(_result(p["asana_gid"], key, p["url"], "error", error))
                        else:
                            results.append(_result(p["asana_gid"], key, p["url"], "cleared", ""))
            else:
                outcomes = _run_batch(
                    client,
                    [(p["jira_key"], p["set_payload"] if p["action"] == "updated" else clear_payload) for p in pending],
                    workers,
                    "update",
                )
                for p in pending:
                    error = outcomes.get(p["jira_key"])
                    if error:
                        results.append(_result(p["asana_gid"], p["jira_key"], p["url"], "error", error))
                    else:
                        results.append(_result(p["asana_gid"], p["jira_key"], p["url"], p["action"], ""))

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
        jira_base_url = resolve_jira_base_url(load_credentials(), prod=args.prod)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    try:
        report_path = update_personal_project_jira_urls(
            database_path=args.database,
            jira_base_url=jira_base_url,
            jira_project_key=args.jira_project_key,
            output_dir=Path(args.output_dir),
            workspace_gid=args.workspace_gid,
            task_issue_type=args.task_issue_type,
            field_map_path=args.field_map,
            workers=max(args.workers, 1),
            limit=args.limit,
            clear_first=args.clear_first,
            dry_run=args.dry_run,
            assume_yes=args.yes,
        )
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(str(exc)) from exc

    logger.info("Personal-project Project URL update finished: %s", report_path)


if __name__ == "__main__":
    main()
