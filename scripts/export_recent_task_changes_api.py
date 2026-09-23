#!/usr/bin/env python3
"""List selected tasks whose recent Asana data differs from the local SQLite snapshot."""

from __future__ import annotations

import argparse
import csv
import logging
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from src.lib.config import load_credentials
from src.lib.database import Database, resolve_default_db_path
from src.lib.http_client import AsanaHttpClient, AsanaHttpError
from src.lib.logging_utils import configure_logging, resolve_log_level
from src.lib.project_scope import parse_gid_list, read_project_gids_file
from src.tasks.tasks import TASK_DETAIL_FIELDS, fetch_task_detail

DEFAULT_CONFIG_ROOT = "."
DEFAULT_OUTPUT_DIR = "exports"
DEFAULT_DAYS_BACK = 14
DEFAULT_PAGE_SIZE = 100
DEFAULT_WORKERS = 5
TASK_FIELDS = tuple(TASK_DETAIL_FIELDS)
CSV_FIELDS = (
    "task_gid", "difference_types", "jira_key", "selected_project_gids",
    "api_name", "db_name",
    "api_due_on", "db_due_on",
    "api_due_at", "db_due_at",
    "api_completed", "db_completed",
    "api_completed_at", "db_completed_at",
)
logger = logging.getLogger(__name__)


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--project-gids", action="append", help="Comma-separated project GIDs; may be repeated.")
    scope.add_argument("--project-gids-file", help="Newline- or comma-separated GIDs, or a CSV project_gid column.")
    parser.add_argument("--days-back", type=int, default=DEFAULT_DAYS_BACK, help="UTC calendar days before today; cutoff is midnight UTC (default: 14).")
    parser.add_argument("--workspace-gid", help="Override the workspace GID from credentials.")
    parser.add_argument("--database", default=resolve_default_db_path(), help="SQLite snapshot to compare (default: data/asana.sqlite).")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="Output directory (default: exports/).")
    parser.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE, help="Asana page size (default: 100).")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help="Concurrent project queries (default: 5).")
    parser.add_argument("--jira-site", help="Named Jira site key to scope the reported Jira Task Key to; if omitted, keys from all sites are reported.")
    parser.add_argument("--log-level", default="INFO", help="Logging verbosity (default: INFO).")
    return parser.parse_args(argv)


def resolve_project_gids(project_gids: Optional[Sequence[str]], project_gids_file: Optional[str]) -> List[str]:
    raw = read_project_gids_file(project_gids_file) if project_gids_file else ",".join(project_gids or ())
    result = list(dict.fromkeys(parse_gid_list(raw)))
    if not result:
        raise SystemExit("At least one project GID is required.")
    return result


def resolve_cutoff(days_back: int, now: Optional[datetime] = None) -> str:
    if days_back < 0:
        raise SystemExit("--days-back must be zero or greater.")
    current_date = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).date()
    cutoff_date = current_date - timedelta(days=days_back)
    cutoff = datetime.combine(cutoff_date, datetime.min.time(), tzinfo=timezone.utc)
    return cutoff.isoformat(timespec="seconds").replace("+00:00", "Z")


def fetch_project_changes(client: AsanaHttpClient, workspace_gid: str, project_gid: str, cutoff: str, page_size: int) -> List[Dict[str, Any]]:
    params = {
        "projects.any": project_gid,
        "modified_at.after": cutoff,
        "opt_fields": ",".join(TASK_FIELDS),
        "limit": str(page_size),
    }
    return list(client.get_paginated(f"/workspaces/{workspace_gid}/tasks/search", params=params))


def _project_memberships(task: Dict[str, Any], selected: set[str]) -> Tuple[List[str], List[str]]:
    gids: List[str] = []
    names: List[str] = []
    for membership in task.get("memberships") or []:
        project = membership.get("project") or {}
        gid = project.get("gid")
        if gid in selected and gid not in gids:
            gids.append(gid)
            names.append(project.get("name") or "")
    return gids, names


def deduplicate_tasks(results: Iterable[Tuple[str, List[Dict[str, Any]]]], project_gids: Sequence[str]) -> List[Dict[str, Any]]:
    selected = set(project_gids)
    tasks: Dict[str, Dict[str, Any]] = {}
    queried: Dict[str, List[str]] = {}
    for project_gid, payloads in results:
        for task in payloads:
            task_gid = task.get("gid")
            if not task_gid:
                continue
            tasks.setdefault(task_gid, task)
            if project_gid not in queried.setdefault(task_gid, []):
                queried[task_gid].append(project_gid)
    rows: List[Dict[str, Any]] = []
    for task_gid, task in tasks.items():
        project_memberships, project_names = _project_memberships(task, selected)
        row = dict(task)
        row["_selected_project_gids"] = project_memberships
        row["_selected_project_names"] = project_names
        row["_queried_project_gids"] = queried[task_gid]
        rows.append(row)
    return sorted(rows, key=lambda item: (item.get("modified_at") or "", item.get("gid") or ""), reverse=True)


def _api_scalar(task: Dict[str, Any], field: str) -> Any:
    return task.get(field)


COMPARE_FIELDS = ("name", "due_on", "due_at", "completed", "completed_at")


def _normalized(value: Any) -> Any:
    if isinstance(value, bool):
        return int(value)
    return value


def _db_memberships(connection: sqlite3.Connection, task_gid: str, selected: set[str]) -> List[Tuple[Any, ...]]:
    rows = connection.execute(
        """
        SELECT project_gid, section_gid, section_name, is_primary
        FROM task_projects
        WHERE task_gid = ? AND project_gid IN ({})
        ORDER BY project_gid, section_gid
        """.format(",".join("?" for _ in selected)),
        (task_gid, *sorted(selected)),
    ).fetchall() if selected else []
    return [tuple(row) for row in rows]


def _db_selected_project_gids(connection: sqlite3.Connection, task_gid: str, selected: set[str]) -> List[str]:
    return [membership[0] for membership in _db_memberships(connection, task_gid, selected)]


def _api_memberships(task: Dict[str, Any], selected: set[str]) -> List[Tuple[Any, ...]]:
    memberships = []
    for membership in task.get("memberships") or []:
        project = membership.get("project") or {}
        if project.get("gid") in selected:
            section = membership.get("section") or {}
            memberships.append((project.get("gid"), section.get("gid"), section.get("name"), int(bool(membership.get("is_primary")))))
    return sorted(set(memberships))


def load_recent_db_tasks(connection: sqlite3.Connection, project_gids: Sequence[str], cutoff: str) -> Dict[str, Dict[str, Any]]:
    if not project_gids:
        return {}
    placeholders = ",".join("?" for _ in project_gids)
    rows = connection.execute(
        f"""
        SELECT DISTINCT t.*
        FROM tasks t
        JOIN task_projects tp ON tp.task_gid = t.gid
        WHERE tp.project_gid IN ({placeholders})
          AND t.modified_at IS NOT NULL
          AND julianday(t.modified_at) > julianday(?)
        """,
        (*project_gids, cutoff),
    ).fetchall()
    return {row["gid"]: dict(row) for row in rows}


def load_jira_keys(connection: sqlite3.Connection, task_gids: Sequence[str], jira_site_key: Optional[str] = None) -> Dict[str, str]:
    unique_gids = sorted({gid for gid in task_gids if gid})
    if not unique_gids:
        return {}
    placeholders = ",".join("?" for _ in unique_gids)
    site_clause = "jira_site_key = ? AND " if jira_site_key else ""
    params = ((jira_site_key,) if jira_site_key else ()) + tuple(unique_gids)
    rows = connection.execute(
        f"SELECT task_gid, jira_key FROM task_jira_mappings WHERE {site_clause}task_gid IN ({placeholders})",
        params,
    ).fetchall()
    keys: Dict[str, List[str]] = {}
    for row in rows:
        keys.setdefault(row["task_gid"], []).append(row["jira_key"])
    return {task_gid: ";".join(sorted(values)) for task_gid, values in keys.items()}


def compare_task(task: Dict[str, Any], db_row: Optional[Dict[str, Any]]) -> List[str]:
    if db_row is None:
        return ["missing_in_db"]
    differences: List[str] = []
    for field in COMPARE_FIELDS:
        if _normalized(_api_scalar(task, field)) != _normalized(db_row.get(field)):
            differences.append(field)
    return differences


def csv_row(task: Dict[str, Any], differences: Sequence[str], db_row: Optional[Dict[str, Any]], jira_key: str = "") -> Dict[str, Any]:
    db_row = db_row or {}
    return {
        "task_gid": task.get("gid", ""),
        "difference_types": ";".join(differences),
        "jira_key": jira_key,
        "selected_project_gids": ";".join(task.get("_selected_project_gids") or []),
        "api_name": task.get("name", ""),
        "db_name": db_row.get("name", ""),
        "api_due_on": task.get("due_on", ""),
        "db_due_on": db_row.get("due_on", ""),
        "api_due_at": task.get("due_at", ""),
        "db_due_at": db_row.get("due_at", ""),
        "api_completed": task.get("completed", ""),
        "db_completed": db_row.get("completed", ""),
        "api_completed_at": task.get("completed_at", ""),
        "db_completed_at": db_row.get("completed_at", ""),
    }


def compare_tasks(
    api_tasks: Sequence[Dict[str, Any]],
    db_tasks: Dict[str, Dict[str, Any]],
    jira_keys: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    jira_keys = jira_keys or {}
    api_by_gid = {task["gid"]: task for task in api_tasks if task.get("gid")}
    entries: List[Tuple[str, Dict[str, Any]]] = []
    for task_gid in sorted(set(api_by_gid) | set(db_tasks)):
        task = api_by_gid.get(task_gid)
        db_row = db_tasks.get(task_gid)
        differences = compare_task(task, db_row) if task else ["missing_in_api"]
        if differences:
            modified_at = (task or {}).get("modified_at") or (db_row or {}).get("modified_at") or ""
            row = csv_row(task or {"gid": task_gid}, differences, db_row, jira_keys.get(task_gid, ""))
            entries.append((modified_at, row))
    return [row for _, row in sorted(entries, key=lambda entry: (entry[0], entry[1]["task_gid"]), reverse=True)]


def resolve_missing_in_api_tasks(
    client: AsanaHttpClient,
    api_tasks: Sequence[Dict[str, Any]],
    db_tasks: Dict[str, Dict[str, Any]],
    connection: sqlite3.Connection,
    project_gids: Sequence[str],
    workers: int,
) -> List[Dict[str, Any]]:
    """Directly retrieve DB-only tasks to distinguish missing search results from stale data."""
    selected = set(project_gids)
    api_gids = {task.get("gid") for task in api_tasks}
    missing_gids = sorted(gid for gid in db_tasks if gid not in api_gids)
    if not missing_gids:
        return []
    resolved: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {
            executor.submit(fetch_task_detail, client, task_gid, TASK_FIELDS): task_gid
            for task_gid in missing_gids
        }
        for future in as_completed(futures):
            task_gid = futures[future]
            try:
                task = future.result()
            except AsanaHttpError as exc:
                logger.warning("Direct task lookup %s failed: HTTP %s: %s", task_gid, exc.status, exc.body or exc)
            except Exception as exc:  # noqa: BLE001 - retain missing_in_api for failed lookups
                logger.warning("Direct task lookup %s failed: %s", task_gid, exc)
            else:
                task["_selected_project_gids"] = _db_selected_project_gids(connection, task_gid, selected)
                resolved.append(task)
                logger.info("Direct task lookup %s succeeded", task_gid)
    return resolved


def run_queries(client: AsanaHttpClient, workspace_gid: str, project_gids: Sequence[str], cutoff: str, page_size: int, workers: int) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    results: List[Tuple[str, List[Dict[str, Any]]]] = []
    errors: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {executor.submit(fetch_project_changes, client, workspace_gid, gid, cutoff, page_size): gid for gid in project_gids}
        for future in as_completed(futures):
            project_gid = futures[future]
            try:
                payloads = future.result()
            except AsanaHttpError as exc:
                logger.warning("Project %s query failed: HTTP %s: %s", project_gid, exc.status, exc.body or exc)
                errors.append({"project_gid": project_gid, "status": exc.status, "body": exc.body})
            except Exception as exc:  # noqa: BLE001 - preserve per-project diagnostics
                logger.warning("Project %s query failed: %s", project_gid, exc)
                errors.append({"project_gid": project_gid, "status": None, "error": str(exc)})
            else:
                logger.info("Project %s -> %s changed task(s)", project_gid, len(payloads))
                results.append((project_gid, payloads))
    return deduplicate_tasks(results, project_gids), errors


def write_outputs(output_dir: str, rows: List[Dict[str, Any]]) -> Path:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    stem = destination / f"recent_task_differences_{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    csv_path = stem.with_suffix(".csv")
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return csv_path


def main(argv: Optional[Iterable[str]] = None) -> None:
    args = parse_args(argv)
    try:
        configure_logging(resolve_log_level(args.log_level))
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    project_gids = resolve_project_gids(args.project_gids, args.project_gids_file)
    cutoff = resolve_cutoff(args.days_back)
    credentials = load_credentials(DEFAULT_CONFIG_ROOT)
    client = AsanaHttpClient(token=credentials.token, skip_ssl_verification=credentials.skip_ssl_verification)
    tasks, errors = run_queries(client, args.workspace_gid or credentials.workspace_gid, project_gids, cutoff, args.page_size, args.workers)
    successful_projects = [
        project_gid for project_gid in project_gids
        if project_gid not in {error["project_gid"] for error in errors}
    ]
    database = Database(args.database)
    connection = database.connect()
    try:
        db_tasks = load_recent_db_tasks(connection, successful_projects, cutoff)
        direct_tasks = resolve_missing_in_api_tasks(
            client, tasks, db_tasks, connection, successful_projects, args.workers
        )
        all_tasks = tasks + direct_tasks
        jira_keys = load_jira_keys(
            connection, set(db_tasks) | {task["gid"] for task in all_tasks if task.get("gid")}, args.jira_site
        )
        differences = compare_tasks(all_tasks, db_tasks, jira_keys)
        csv_path = write_outputs(args.output_dir, differences)
    finally:
        connection.close()
    logger.info("Found %s task(s) with API/database differences; wrote %s", len(differences), csv_path)
    if errors:
        logger.warning("%s project query error(s); affected projects were omitted from comparison", len(errors))


if __name__ == "__main__":
    main()
