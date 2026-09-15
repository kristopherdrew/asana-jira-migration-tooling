#!/usr/bin/env python3
"""Export tasks recently changed in selected Asana projects without changing SQLite."""

from __future__ import annotations

import argparse
import csv
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from src.lib.config import load_credentials
from src.lib.http_client import AsanaHttpClient, AsanaHttpError
from src.lib.logging_utils import configure_logging, resolve_log_level
from src.lib.project_scope import parse_gid_list, read_project_gids_file

DEFAULT_CONFIG_ROOT = "."
DEFAULT_OUTPUT_DIR = "exports"
DEFAULT_DAYS_BACK = 14
DEFAULT_PAGE_SIZE = 100
DEFAULT_WORKERS = 5
TASK_FIELDS = (
    "gid", "name", "resource_type", "resource_subtype", "created_at", "modified_at",
    "completed", "completed_at", "parent.gid", "parent.name", "permalink_url",
    "memberships.project.gid", "memberships.project.name", "memberships.is_primary",
    "memberships.section.gid", "memberships.section.name",
)
CSV_FIELDS = (
    "task_gid", "name", "modified_at", "created_at", "completed", "completed_at",
    "parent_gid", "parent_name", "permalink_url", "selected_project_gids",
    "selected_project_names", "queried_project_gids",
)
logger = logging.getLogger(__name__)


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--project-gids", action="append", help="Comma-separated project GIDs; may be repeated.")
    scope.add_argument("--project-gids-file", help="Newline- or comma-separated GIDs, or a CSV project_gid column.")
    parser.add_argument("--since", help="UTC ISO-8601 cutoff override; defaults to now minus 14 days.")
    parser.add_argument("--days-back", type=int, default=DEFAULT_DAYS_BACK, help="Lookback window when --since is omitted (default: 14).")
    parser.add_argument("--workspace-gid", help="Override the workspace GID from credentials.")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="Output directory (default: exports/).")
    parser.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE, help="Asana page size (default: 100).")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help="Concurrent project queries (default: 5).")
    parser.add_argument("--log-level", default="INFO", help="Logging verbosity (default: INFO).")
    return parser.parse_args(argv)


def resolve_project_gids(project_gids: Optional[Sequence[str]], project_gids_file: Optional[str]) -> List[str]:
    raw = read_project_gids_file(project_gids_file) if project_gids_file else ",".join(project_gids or ())
    result = list(dict.fromkeys(parse_gid_list(raw)))
    if not result:
        raise SystemExit("At least one project GID is required.")
    return result


def resolve_cutoff(since: Optional[str], days_back: int, now: Optional[datetime] = None) -> str:
    if days_back < 1:
        raise SystemExit("--days-back must be at least 1.")
    if since:
        value = since.strip().replace("Z", "+00:00") if since.endswith("Z") else since.strip()
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise SystemExit(f"Invalid --since timestamp: {since}") from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        cutoff = parsed.astimezone(timezone.utc)
    else:
        cutoff = (now or datetime.now(timezone.utc)).astimezone(timezone.utc) - timedelta(days=days_back)
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


def csv_row(task: Dict[str, Any]) -> Dict[str, Any]:
    parent = task.get("parent") or {}
    return {
        "task_gid": task.get("gid", ""), "name": task.get("name", ""),
        "modified_at": task.get("modified_at", ""), "created_at": task.get("created_at", ""),
        "completed": task.get("completed", ""), "completed_at": task.get("completed_at", ""),
        "parent_gid": parent.get("gid", ""), "parent_name": parent.get("name", ""),
        "permalink_url": task.get("permalink_url", ""),
        "selected_project_gids": ";".join(task.get("_selected_project_gids") or []),
        "selected_project_names": ";".join(task.get("_selected_project_names") or []),
        "queried_project_gids": ";".join(task.get("_queried_project_gids") or []),
    }


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


def write_outputs(output_dir: str, cutoff: str, project_gids: Sequence[str], tasks: List[Dict[str, Any]], errors: List[Dict[str, Any]]) -> Tuple[Path, Path]:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    stem = destination / f"recent_task_changes_{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    csv_path, json_path = stem.with_suffix(".csv"), stem.with_suffix(".json")
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(csv_row(task) for task in tasks)
    raw_tasks = [{key: value for key, value in task.items() if not key.startswith("_")} for task in tasks]
    with json_path.open("w", encoding="utf-8") as handle:
        json.dump({"cutoff": cutoff, "project_gids": list(project_gids), "task_count": len(tasks), "errors": errors, "tasks": raw_tasks}, handle, indent=2, ensure_ascii=False)
    return csv_path, json_path


def main(argv: Optional[Iterable[str]] = None) -> None:
    args = parse_args(argv)
    try:
        configure_logging(resolve_log_level(args.log_level))
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    project_gids = resolve_project_gids(args.project_gids, args.project_gids_file)
    cutoff = resolve_cutoff(args.since, args.days_back)
    credentials = load_credentials(DEFAULT_CONFIG_ROOT)
    client = AsanaHttpClient(token=credentials.token, skip_ssl_verification=credentials.skip_ssl_verification)
    tasks, errors = run_queries(client, args.workspace_gid or credentials.workspace_gid, project_gids, cutoff, args.page_size, args.workers)
    csv_path, json_path = write_outputs(args.output_dir, cutoff, project_gids, tasks, errors)
    logger.info("Exported %s unique changed task(s) to %s and %s", len(tasks), csv_path, json_path)
    if errors:
        logger.warning("%s project query error(s) recorded in %s", len(errors), json_path)


if __name__ == "__main__":
    main()
