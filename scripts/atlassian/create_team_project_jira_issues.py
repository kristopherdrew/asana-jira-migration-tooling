#!/usr/bin/env python3
"""Create Jira Work Items for Asana teams and projects directly via the REST API.

A direct alternative to the CSV import produced by
``export_team_project_jira_csv.py``: this script POSTs Epics (one per team) and
child Tasks (one per project) to Jira Cloud, resolving issue-type and custom-field
IDs at runtime via ``createmeta``, linking each task to its team's epic by real
issue key, and persisting Asana→Jira mappings so re-runs skip work already done.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.lib.adf import text_to_adf
from src.lib.config import Credentials, load_credentials
from src.lib.database import Database
from src.lib.jira_client import JiraApiError, JiraClient
from src.lib.logging_utils import configure_logging, resolve_log_level
from scripts.atlassian.export_team_project_jira_csv import build_team_project_rows

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = "data/asana.sqlite"
DEFAULT_OUTPUT_DIR = "exports"
DEFAULT_WORKERS = 4

# Maps an internal row column to the Jira custom field *name* it should populate.
# Override individual entries with a --field-map JSON file when the Jira field is
# named differently. Standard fields (summary, description, assignee, labels,
# parent) are handled separately and are not listed here.
DEFAULT_FIELD_MAP: Dict[str, str] = {
    "Team URL": "Team URL",
    "Project URL": "Project URL",
    "Instructions": "Instructions",
    "Project Status": "Project Status",
}

REPORT_FIELDNAMES = ["kind", "asana_gid", "summary", "jira_key", "status", "message"]


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default=DEFAULT_DB_PATH, help=f"Path to the SQLite database (default: {DEFAULT_DB_PATH}).")
    parser.add_argument("--workspace-gid", help="Optional Asana workspace GID used to build project URLs.")
    parser.add_argument("--jira-base-url", required=True, help="Base URL for the Jira site (e.g., https://example.atlassian.net).")
    parser.add_argument("--jira-project-key", required=True, help="Target Jira project key where issues are created (e.g., MIG).")
    parser.add_argument("--epic-issue-type", default="Epic", help="Issue type name for team epics (default: Epic).")
    parser.add_argument("--task-issue-type", default="Task", help="Issue type name for project tasks (default: Task).")
    parser.add_argument("--field-map", help="Optional JSON file overriding column -> Jira custom field name mappings.")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help=f"Concurrent workers for task creation (default: {DEFAULT_WORKERS}).")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help=f"Directory for the run report CSV (default: {DEFAULT_OUTPUT_DIR}/).")
    parser.add_argument("--limit", type=int, help="Cap the number of issues created this run (epics counted first). Skips are not counted.")
    parser.add_argument("--dry-run", action="store_true", help="Resolve fields and print the JSON payloads without creating issues.")
    parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt before creating issues.")
    parser.add_argument("--log-level", default="INFO", help="Logging verbosity (default: INFO).")
    return parser.parse_args(argv)


def ensure_atlassian_credentials(creds: Credentials) -> Tuple[str, str]:
    if not creds.atlassian_username or not creds.atlassian_token:
        raise ValueError("Resolved credentials must include 'ATLASSIAN_USERNAME' and 'ATLASSIAN_TOKEN' to create Jira issues.")
    return creds.atlassian_username, creds.atlassian_token


def load_field_map(path: Optional[str]) -> Dict[str, str]:
    """Return column -> Jira field name map, applying overrides from a JSON file."""
    field_map = dict(DEFAULT_FIELD_MAP)
    if not path:
        return field_map
    with open(path, "r", encoding="utf-8") as handle:
        overrides = json.load(handle)
    if not isinstance(overrides, dict):
        raise ValueError("--field-map JSON must be an object of column -> Jira field name.")
    for column, jira_name in overrides.items():
        field_map[str(column)] = str(jira_name)
    return field_map


def resolve_issue_type_id(issue_types: List[Dict[str, object]], name: str) -> str:
    target = name.strip().casefold()
    for issue_type in issue_types:
        if str(issue_type.get("name", "")).strip().casefold() == target:
            return str(issue_type.get("id"))
    available = ", ".join(sorted(str(it.get("name", "")) for it in issue_types))
    raise ValueError(f"Issue type '{name}' not found for project. Available: {available}")


def _is_active_status(status: Optional[str]) -> bool:
    return (status or "").strip().casefold() == "active"


def format_custom_value(schema: Dict[str, object], value: str) -> object:
    """Format a string value for a Jira custom field based on its createmeta schema."""
    field_type = str(schema.get("type", "string"))
    if field_type == "option":
        return {"value": value}
    if field_type == "array":
        items = str(schema.get("items", ""))
        if items == "option":
            return [{"value": value}]
        return [value]
    if field_type == "number":
        try:
            return float(value) if "." in value else int(value)
        except ValueError:
            return value
    if field_type == "doc":
        return text_to_adf(value)
    return value


def build_issue_payload(
    row: Dict[str, str],
    *,
    project_key: str,
    issue_type_id: str,
    field_defs: Dict[str, Dict[str, object]],
    custom_field_map: Dict[str, str],
    parent_key: Optional[str] = None,
) -> Dict[str, object]:
    """Assemble the ``{"fields": {...}}`` payload for one issue."""
    available_ids = {str(defn.get("fieldId")) for defn in field_defs.values()}
    fields: Dict[str, object] = {
        "project": {"key": project_key},
        "issuetype": {"id": issue_type_id},
        "summary": (row.get("Summary") or "").strip() or "(no summary)",
    }

    description = (row.get("Description") or "").strip()
    if "description" in available_ids:
        # Prefer a pre-built rich ADF document (Task rows) and fall back to converting
        # the plain-text description (Epic rows have no override).
        override = (row.get("_adf_overrides") or {}).get("Description")
        if override is not None:
            fields["description"] = override
        elif description:
            fields["description"] = text_to_adf(description)

    account_id = (row.get("Owner Atlassian ID") or "").strip()
    if account_id and "assignee" in available_ids and _is_active_status(row.get("Owner Atlassian Status")):
        fields["assignee"] = {"accountId": account_id}

    if "labels" in available_ids:
        labels = []
        for raw in (row.get("Owner Source"), row.get("Labels"), row.get("Project Type")):
            token = (raw or "").strip()
            if token:
                labels.append("_".join(token.split()))
        if labels:
            fields["labels"] = labels

    if parent_key and "parent" in available_ids:
        fields["parent"] = {"key": parent_key}

    adf_overrides = row.get("_adf_overrides") or {}
    for column, jira_name in custom_field_map.items():
        value = (row.get(column) or "").strip()
        if not value:
            continue
        defn = field_defs.get(jira_name)
        if not defn:
            continue
        field_id = str(defn.get("fieldId"))
        schema = defn.get("schema") or {}
        # Prefer a pre-built rich ADF document when the Jira field is rich-text;
        # otherwise format the plain string per the field's schema type.
        override = adf_overrides.get(column)
        if override is not None and str(schema.get("type")) == "doc":
            fields[field_id] = override
        else:
            fields[field_id] = format_custom_value(schema, value)

    return {"fields": fields}


def _resolve_missing_field_warnings(
    custom_field_map: Dict[str, str],
    field_defs: Dict[str, Dict[str, object]],
    issue_type_name: str,
) -> None:
    for column, jira_name in custom_field_map.items():
        if jira_name not in field_defs:
            logger.warning(
                "Custom field '%s' (for column '%s') is not available on the %s create screen; values will be skipped.",
                jira_name,
                column,
                issue_type_name,
            )


def write_report(rows: List[Dict[str, str]], output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=REPORT_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)
    return output_path


def create_team_project_jira_issues(
    database_path: str,
    jira_base_url: str,
    jira_project_key: str,
    output_dir: Path,
    *,
    workspace_gid: Optional[str] = None,
    epic_issue_type: str = "Epic",
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

    database = Database(db_path=database_path)
    with database.connect() as connection:
        database.initialize(connection)
        epic_rows, task_rows = build_team_project_rows(connection, workspace_gid)
        existing = database.load_team_project_jira_mappings(connection)

        if client is None:
            client = JiraClient(
                base_url=jira_base_url,
                username=username,
                token=token,
                skip_ssl_verification=credentials.skip_ssl_verification,
            )

        issue_types = client.get_createmeta_issue_types(jira_project_key)
        epic_type_id = resolve_issue_type_id(issue_types, epic_issue_type)
        task_type_id = resolve_issue_type_id(issue_types, task_issue_type)
        epic_fields = client.get_createmeta_fields(jira_project_key, epic_type_id)
        task_fields = client.get_createmeta_fields(jira_project_key, task_type_id)
        _resolve_missing_field_warnings(custom_field_map, epic_fields, epic_issue_type)
        _resolve_missing_field_warnings(custom_field_map, task_fields, task_issue_type)

        pending_epics = [r for r in epic_rows if (r["_kind"], r["_asana_gid"]) not in existing]
        pending_tasks = [r for r in task_rows if (r["_kind"], r["_asana_gid"]) not in existing]
        total_pending = len(pending_epics) + len(pending_tasks)
        logger.info(
            "%s epic(s) and %s task(s) pending creation (%s already mapped).",
            len(pending_epics),
            len(pending_tasks),
            len(existing),
        )

        if not dry_run and not assume_yes:
            planned = total_pending if limit is None else min(limit, total_pending)
            answer = input(
                f"About to create up to {planned} issue(s) in Jira project {jira_project_key}. Continue? [y/N] "
            ).strip().lower()
            if answer not in {"y", "yes"}:
                raise SystemExit("Aborted by user.")

        results: List[Dict[str, str]] = []
        created_keys: Dict[Tuple[str, str], str] = dict(existing)
        created_count = 0
        dry_counter = 0

        def limit_reached() -> bool:
            return limit is not None and created_count >= limit

        # Record skips for already-mapped entries up front.
        for row in epic_rows + task_rows:
            key = (row["_kind"], row["_asana_gid"])
            if key in existing:
                results.append({
                    "kind": row["_kind"],
                    "asana_gid": row["_asana_gid"],
                    "summary": row.get("Summary", ""),
                    "jira_key": existing[key],
                    "status": "skipped-existing",
                    "message": "",
                })

        # Pass A — epics (sequential; tasks depend on their keys).
        for row in pending_epics:
            key = (row["_kind"], row["_asana_gid"])
            payload = build_issue_payload(
                row,
                project_key=jira_project_key,
                issue_type_id=epic_type_id,
                field_defs=epic_fields,
                custom_field_map=custom_field_map,
            )
            if limit_reached():
                results.append(_result(row, "", "skipped-limit", ""))
                continue
            if dry_run:
                dry_counter += 1
                dry_key = f"{jira_project_key}-NEW{dry_counter}"
                created_keys[key] = dry_key
                logger.info("[dry-run] Epic payload:\n%s", json.dumps(payload, indent=2))
                results.append(_result(row, dry_key, "dry-run", ""))
                continue
            try:
                response = client.create_issue(payload)
                jira_key = str(response.get("key", ""))
                created_keys[key] = jira_key
                database.upsert_team_project_jira_mappings(
                    connection,
                    [{"kind": row["_kind"], "asana_gid": row["_asana_gid"], "jira_key": jira_key}],
                    datetime.now(timezone.utc),
                )
                created_count += 1
                results.append(_result(row, jira_key, "created", ""))
                logger.info("Created epic %s for %s", jira_key, row.get("Summary", ""))
            except (JiraApiError, ValueError) as exc:
                results.append(_result(row, "", "error", str(exc)))
                logger.error("Failed to create epic for %s: %s", row.get("Summary", ""), exc)

        # Pass B — tasks (parallel network calls; mappings persisted on the main thread).
        remaining = pending_tasks if limit is None else pending_tasks[: max(0, limit - created_count)]
        if len(remaining) < len(pending_tasks):
            for row in pending_tasks[len(remaining):]:
                results.append(_result(row, "", "skipped-limit", ""))

        def build_task(row: Dict[str, str]) -> Tuple[Dict[str, str], Dict[str, object], Optional[str]]:
            parent_key = created_keys.get((row["_parent_kind"], row["_parent_asana_gid"]))
            payload = build_issue_payload(
                row,
                project_key=jira_project_key,
                issue_type_id=task_type_id,
                field_defs=task_fields,
                custom_field_map=custom_field_map,
                parent_key=parent_key,
            )
            return row, payload, parent_key

        if dry_run:
            for row in remaining:
                _, payload, _ = build_task(row)
                dry_counter += 1
                dry_key = f"{jira_project_key}-NEW{dry_counter}"
                logger.info("[dry-run] Task payload:\n%s", json.dumps(payload, indent=2))
                results.append(_result(row, dry_key, "dry-run", ""))
        elif remaining:
            def worker(row: Dict[str, str]) -> Tuple[Dict[str, str], Optional[str], Optional[str]]:
                _, payload, parent_key = build_task(row)
                if not parent_key:
                    return row, None, "parent epic was not created (skipped or failed)"
                try:
                    response = client.create_issue(payload)
                    return row, str(response.get("key", "")), None
                except (JiraApiError, ValueError) as exc:
                    return row, None, str(exc)

            with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
                future_map = {executor.submit(worker, row): row for row in remaining}
                for future in as_completed(future_map):
                    row, jira_key, error_message = future.result()
                    if error_message:
                        results.append(_result(row, "", "error", error_message))
                        logger.error("Failed to create task for %s: %s", row.get("Summary", ""), error_message)
                        continue
                    database.upsert_team_project_jira_mappings(
                        connection,
                        [{"kind": row["_kind"], "asana_gid": row["_asana_gid"], "jira_key": jira_key}],
                        datetime.now(timezone.utc),
                    )
                    created_count += 1
                    results.append(_result(row, jira_key, "created", ""))
                    logger.info("Created task %s for %s", jira_key, row.get("Summary", ""))

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    report_path = Path(output_dir) / f"team_project_jira_create_{timestamp}.csv"
    write_report(results, report_path)
    logger.info(
        "Run complete: %s created, %s skipped, %s error(s). Report: %s",
        sum(1 for r in results if r["status"] in {"created", "dry-run"}),
        sum(1 for r in results if r["status"].startswith("skipped")),
        sum(1 for r in results if r["status"] == "error"),
        report_path,
    )
    return report_path


def _result(row: Dict[str, str], jira_key: str, status: str, message: str) -> Dict[str, str]:
    return {
        "kind": row["_kind"],
        "asana_gid": row["_asana_gid"],
        "summary": row.get("Summary", ""),
        "jira_key": jira_key,
        "status": status,
        "message": message,
    }


def main(argv: Optional[Iterable[str]] = None) -> None:
    args = parse_args(argv)
    try:
        log_level = resolve_log_level(args.log_level)
    except ValueError as exc:  # noqa: BLE001
        raise SystemExit(str(exc)) from exc
    configure_logging(log_level)

    try:
        report_path = create_team_project_jira_issues(
            database_path=args.database,
            jira_base_url=args.jira_base_url,
            jira_project_key=args.jira_project_key,
            output_dir=Path(args.output_dir),
            workspace_gid=args.workspace_gid,
            epic_issue_type=args.epic_issue_type,
            task_issue_type=args.task_issue_type,
            field_map_path=args.field_map,
            workers=max(args.workers, 1),
            limit=args.limit,
            dry_run=args.dry_run,
            assume_yes=args.yes,
        )
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(str(exc)) from exc

    logger.info("Jira team/project issue creation finished: %s", report_path)


if __name__ == "__main__":
    main()
