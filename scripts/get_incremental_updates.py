#!/usr/bin/env python3
"""Incremental sync for tasks, comments, and attachments."""

from __future__ import annotations

import argparse
import math
import logging
import sqlite3
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Set, Tuple

from src.lib.config import load_credentials
from src.lib.database import Database, synced_at_timestamp, resolve_default_db_path
from src.lib.http_client import AsanaHttpClient, AsanaHttpError
from src.lib.logging_utils import configure_logging, resolve_log_level
from src.lib.project_scope import ScopeProject, determine_projects, parse_gid_list, validate_scope_args
from src.lib.team_picker import choose_team
from src.lib.user_backfill import UserBackfill
from src.tasks.task_extras import fetch_task_attachments, fetch_task_comments
from src.tasks.tasks import (
    SUBTASK_FIELDS,
    TASK_DETAIL_FIELDS,
    TASK_LIST_FIELDS,
    collect_user_gids_from_task,
    fetch_project_tasks,
    fetch_subtasks,
    fetch_task_detail,
)


DEFAULT_CONFIG_ROOT = "."
DEFAULT_DB_PATH = "data/asana.sqlite"
DEFAULT_PAGE_SIZE = 100
DEFAULT_WORKERS = 5
DEFAULT_DAYS_BACK = 3
AUTO_PADDING_DAYS = 1
PROGRESS_INTERVAL = 25
COMMIT_INTERVAL = 25
UNASSIGNED_TEAM_KEY = "__UNASSIGNED_TEAM__"
UNASSIGNED_PROJECT_KEY = "__UNASSIGNED_PROJECT__"
logger = logging.getLogger(__name__)


def parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        cleaned = value.strip()
        if cleaned.endswith("Z"):
            cleaned = cleaned[:-1] + "+00:00"
        parsed = datetime.fromisoformat(cleaned)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except Exception:
        logger.warning("Unable to parse timestamp '%s'", value)
        return None


def _task_counts_template() -> Dict[str, int]:
    return {"analyzed": 0, "created": 0, "updated": 0}


def _extra_counts_template() -> Dict[str, int]:
    return {"analyzed": 0, "created": 0, "updated": 0, "deleted": 0}


class SummaryAggregator:
    """Aggregates per-team/project statistics for incremental sync runs."""

    def __init__(
        self,
        team_lookup: Dict[Optional[str], str],
        project_lookup: Dict[Optional[str], str],
        excluded_team_gids: Set[str],
    ) -> None:
        self.team_lookup = team_lookup
        self.project_lookup = project_lookup
        self.excluded_team_gids = excluded_team_gids
        self.team_stats: Dict[str, Dict] = {}
        self.totals = {
            "tasks": _task_counts_template(),
            "comments": _extra_counts_template(),
            "attachments": _extra_counts_template(),
        }

    def record(
        self,
        memberships: List[Dict[str, Optional[str]]],
        task_counts: Dict[str, int],
        comment_counts: Dict[str, int],
        attachment_counts: Dict[str, int],
    ) -> None:
        original = memberships or []
        filtered: List[Dict[str, Optional[str]]] = []
        for mem in original:
            team_gid = mem.get("team_gid")
            if team_gid and team_gid in self.excluded_team_gids:
                continue
            filtered.append(mem)

        if not filtered:
            if original:
                # Task only belongs to excluded teams; skip recording.
                return
            filtered = [
                {
                    "team_gid": None,
                    "team_name": "Unassigned",
                    "project_gid": None,
                    "project_name": "Unassigned",
                }
            ]

        unique_pairs: Set[Tuple[Optional[str], Optional[str]]] = set()
        for mem in filtered:
            team_gid = mem.get("team_gid")
            team_name = mem.get("team_name")
            project_gid = mem.get("project_gid")
            project_name = mem.get("project_name")
            key = (team_gid, project_gid)
            if key in unique_pairs:
                continue
            unique_pairs.add(key)
            team_entry = self._ensure_team_entry(team_gid, team_name)
            project_entry = self._ensure_project_entry(team_entry, project_gid, project_name)
            self._apply_counts(team_entry, project_entry, task_counts, comment_counts, attachment_counts)

        self._apply_totals(task_counts, comment_counts, attachment_counts)

    def _ensure_team_entry(
        self,
        team_gid: Optional[str],
        team_name: Optional[str],
    ) -> Dict:
        key = team_gid or UNASSIGNED_TEAM_KEY
        if key not in self.team_stats:
            name = (
                team_name
                or self.team_lookup.get(team_gid)
                or ("Unassigned" if team_gid is None else f"Team {team_gid}")
            )
            self.team_stats[key] = {
                "team_gid": team_gid,
                "team_name": name,
                "tasks": _task_counts_template(),
                "comments": _extra_counts_template(),
                "attachments": _extra_counts_template(),
                "projects": {},
            }
        return self.team_stats[key]

    def _ensure_project_entry(
        self,
        team_entry: Dict,
        project_gid: Optional[str],
        project_name: Optional[str],
    ) -> Dict:
        key = project_gid or UNASSIGNED_PROJECT_KEY
        projects = team_entry["projects"]
        if key not in projects:
            name = (
                project_name
                or self.project_lookup.get(project_gid)
                or ("Unassigned" if project_gid is None else f"Project {project_gid}")
            )
            projects[key] = {
                "project_gid": project_gid,
                "project_name": name,
                "tasks": _task_counts_template(),
                "comments": _extra_counts_template(),
                "attachments": _extra_counts_template(),
            }
        return projects[key]

    def _apply_counts(
        self,
        team_entry: Dict,
        project_entry: Dict,
        task_counts: Dict[str, int],
        comment_counts: Dict[str, int],
        attachment_counts: Dict[str, int],
    ) -> None:
        for key, value in task_counts.items():
            team_entry["tasks"][key] += value
            project_entry["tasks"][key] += value
        for key, value in comment_counts.items():
            team_entry["comments"][key] += value
            project_entry["comments"][key] += value
        for key, value in attachment_counts.items():
            team_entry["attachments"][key] += value
            project_entry["attachments"][key] += value

    def _apply_totals(
        self,
        task_counts: Dict[str, int],
        comment_counts: Dict[str, int],
        attachment_counts: Dict[str, int],
    ) -> None:
        for key, value in task_counts.items():
            self.totals["tasks"][key] += value
        for key, value in comment_counts.items():
            self.totals["comments"][key] += value
        for key, value in attachment_counts.items():
            self.totals["attachments"][key] += value

    def print_summary(self, lookback_desc: str) -> None:
        logger.info("")
        logger.info("Incremental Sync Summary (lookback: %s)", lookback_desc)
        if not any(self.totals["tasks"].values()):
            logger.info("No tasks were updated in this window.")
            return

        for team_data in sorted(
            self.team_stats.values(), key=lambda t: (t["team_name"] or "").lower()
        ):
            if not any(team_data["tasks"].values()) and not any(team_data["comments"].values()) and not any(
                team_data["attachments"].values()
            ):
                continue
            team_label = team_data["team_name"]
            logger.info("Team: %s", team_label)
            self._print_counts(team_data["tasks"], "Tasks")
            self._print_counts(team_data["comments"], "Comments", include_deleted=True)
            self._print_counts(team_data["attachments"], "Attachments", include_deleted=True)
            logger.info("")

        logger.info("Totals:")
        self._print_counts(self.totals["tasks"], "  Tasks")
        self._print_counts(self.totals["comments"], "  Comments", include_deleted=True)
        self._print_counts(self.totals["attachments"], "  Attachments", include_deleted=True)

    def prompt_project_breakdown(self, mode: str = "prompt") -> None:
        if not any(team["projects"] for team in self.team_stats.values()):
            return
        if mode == "no":
            return
        if mode == "yes":
            response = "y"
        else:
            try:
                response = input("\nView per-project breakdown? [y/N]: ").strip().lower()
            except EOFError:
                response = "n"
        if response not in {"y", "yes"}:
            return

        for team_data in sorted(
            self.team_stats.values(), key=lambda t: (t["team_name"] or "").lower()
        ):
            if not team_data["projects"]:
                continue
            logger.info("")
            logger.info("Team: %s", team_data["team_name"])
            for project_data in sorted(
                team_data["projects"].values(), key=lambda p: (p["project_name"] or "").lower()
            ):
                self._print_project_counts(project_data)

    @staticmethod
    def _print_counts(counts: Dict[str, int], label: str, include_deleted: bool = False) -> None:
        parts = [f"analyzed: {counts['analyzed']}"]
        parts.append(f"updated: {counts['updated']}")
        parts.append(f"created: {counts['created']}")
        if include_deleted:
            parts.append(f"marked deleted: {counts['deleted']}")
        logger.info("%-12s → %s", label, " | ".join(parts))

    @staticmethod
    def _print_project_counts(project_data: Dict) -> None:
        name = project_data["project_name"]
        logger.info("  Project: %s", name)
        SummaryAggregator._print_counts(project_data["tasks"], "    Tasks")
        SummaryAggregator._print_counts(
            project_data["comments"], "    Comments", include_deleted=True
        )
        SummaryAggregator._print_counts(
            project_data["attachments"], "    Attachments", include_deleted=True
        )


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--days-back",
        type=int,
        default=DEFAULT_DAYS_BACK,
        help="Number of days in the past to pull modifications for (mutually exclusive with --auto-from-last-update).",
    )
    parser.add_argument(
        "--workspace-gid",
        help="Override the workspace GID from the resolved credentials (for logging only).",
    )
    parser.add_argument(
        "--project-gid",
        help="Sync a single project by GID.",
    )
    parser.add_argument(
        "--team",
        action="store_true",
        help="Interactively select a team.",
    )
    parser.add_argument(
        "--team-gids",
        help="Comma-separated list of team GIDs to sync sequentially.",
    )
    parser.add_argument(
        "--all-teams",
        action="store_true",
        help="Sync all teams in the database (can exclude via --exclude-team-gids).",
    )
    parser.add_argument(
        "--exclude-team-gids",
        help="Comma-separated team GIDs to skip when using --all-teams.",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=DEFAULT_PAGE_SIZE,
        help=f"Page size for task pagination (default: {DEFAULT_PAGE_SIZE}).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=f"Concurrent workers for detail fetches (default: {DEFAULT_WORKERS}).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging verbosity (default: INFO).",
    )
    parser.add_argument(
        "--project-breakdown",
        choices=("prompt", "yes", "no"),
        default="prompt",
        help=(
            "Control per-project breakdown output at the end of a run: "
            "prompt (default), always show (yes), or skip (no)."
        ),
    )
    parser.add_argument(
        "--auto-from-last-update",
        action="store_true",
        help=(
            "Derive the lookback window from the latest task modified_at in scope; "
            "if none found, fetch all tasks (no modified_on.after filter)."
        ),
    )
    return parser.parse_args(argv)


def ensure_field_definitions(
    connection,
    database: Database,
    client: AsanaHttpClient,
    known_custom_fields: Set[str],
    field_gids: Set[str],
    sync_time,
) -> None:
    from src.tasks.custom_fields import fetch_custom_field  # local import to avoid cycle

    missing = [gid for gid in field_gids if gid and gid not in known_custom_fields]
    for gid in missing:
        custom_field = fetch_custom_field(client, gid)
        database.upsert_custom_field(connection, custom_field, sync_time)
        known_custom_fields.add(gid)


def collect_comment_and_attachment_users(
    comments: Iterable[Dict], attachments: Iterable[Dict]
) -> Set[str]:
    user_gids: Set[str] = set()
    for comment in comments:
        created_by = comment.get("created_by") or {}
        gid = created_by.get("gid")
        if gid:
            user_gids.add(gid)
    for attachment in attachments:
        created_by = attachment.get("created_by") or {}
        gid = created_by.get("gid")
        if gid:
            user_gids.add(gid)
    return user_gids


def schedule_task_detail(
    executor: ThreadPoolExecutor,
    client: AsanaHttpClient,
    task_gid: str,
) -> Future:
    return executor.submit(fetch_task_detail, client, task_gid, TASK_DETAIL_FIELDS)


def schedule_subtask_listing(
    client: AsanaHttpClient,
    parent_gid: str,
    page_size: int,
) -> Iterable[Dict]:
    return fetch_subtasks(client, parent_gid, SUBTASK_FIELDS, page_size=page_size)


def collect_custom_field_gids(task_payload: Dict) -> Set[str]:
    gids: Set[str] = set()
    for field in task_payload.get("custom_fields") or []:
        if isinstance(field, dict):
            gid = field.get("gid")
            if gid:
                gids.add(gid)
    return gids


def extract_memberships(task_payload: Dict) -> List[Dict[str, Optional[str]]]:
    memberships = task_payload.get("memberships") or []
    results: List[Dict[str, Optional[str]]] = []
    for membership in memberships:
        if not isinstance(membership, dict):
            continue
        project = membership.get("project") or {}
        team = project.get("team") or {}
        results.append(
            {
                "project_gid": project.get("gid"),
                "project_name": project.get("name"),
                "team_gid": team.get("gid"),
                "team_name": team.get("name"),
            }
        )
    return results


def collect_task_summaries(iterable: Iterable[Dict]) -> List[Dict]:
    seen: Set[str] = set()
    summaries: List[Dict] = []
    try:
        for item in iterable:
            gid = item.get("gid")
            if not gid or gid in seen:
                continue
            seen.add(gid)
            summaries.append(item)
    except AsanaHttpError as err:
        logger.error("Failed to search for modified tasks: %s", err.body or err)
        raise
    return summaries


def scope_max_modified(
    connection,
    projects: List[ScopeProject],
    all_teams_mode: bool,
    excluded_team_gids: Set[str],
) -> Optional[str]:
    if all_teams_mode:
        if excluded_team_gids:
            placeholders = ",".join("?" for _ in excluded_team_gids)
            query = f"""
                SELECT MAX(t.modified_at) AS latest_modified
                FROM tasks t
                JOIN task_projects tp ON tp.task_gid = t.gid
                JOIN projects p ON p.gid = tp.project_gid
                WHERE p.team_gid NOT IN ({placeholders})
            """
            params = tuple(excluded_team_gids)
        else:
            query = "SELECT MAX(modified_at) AS latest_modified FROM tasks"
            params = ()
    else:
        gids = [proj.get("project_gid") for proj in projects if proj.get("project_gid")]
        if not gids:
            return None
        placeholders = ",".join("?" for _ in gids)
        query = f"""
            SELECT MAX(t.modified_at) AS latest_modified
            FROM tasks t
            JOIN task_projects tp ON tp.task_gid = t.gid
            WHERE tp.project_gid IN ({placeholders})
        """
        params = tuple(gids)

    row = connection.execute(query, params).fetchone()
    return row["latest_modified"] if row else None


def resolve_modified_since(
    connection,
    projects: List[ScopeProject],
    all_teams_mode: bool,
    excluded_team_gids: Set[str],
    use_auto: bool,
    requested_days_back: int,
) -> Tuple[Optional[str], Optional[int], str]:
    now = datetime.now(tz=timezone.utc)
    if use_auto:
        latest = scope_max_modified(connection, projects, all_teams_mode, excluded_team_gids)
        if latest:
            baseline = parse_iso_datetime(latest)
            if baseline:
                start = baseline - timedelta(days=AUTO_PADDING_DAYS)
                delta = now - start
                days_back = max(1, math.ceil(delta.total_seconds() / 86400))
                modified_since = start.strftime("%Y-%m-%d")
                desc = (
                    f"auto from last update (padding {AUTO_PADDING_DAYS}d) → {days_back} day(s), since {modified_since}"
                )
                return modified_since, days_back, desc
            logger.warning("Latest modified timestamp could not be parsed; falling back to full history.")
        desc = "all history (no prior tasks found for scope)"
        return None, None, desc

    since_time = now - timedelta(days=requested_days_back)
    modified_since = since_time.strftime("%Y-%m-%d")
    desc = f"{requested_days_back} day(s) (since {modified_since})"
    return modified_since, requested_days_back, desc


def fetch_workspace_modified_tasks(
    client: AsanaHttpClient,
    workspace_gid: str,
    modified_since: Optional[str],
    page_size: int,
    project_gid: Optional[str] = None,
) -> Iterable[Dict]:
    params = {
        "opt_fields": ",".join(TASK_LIST_FIELDS),
        "limit": str(page_size),
    }
    if modified_since:
        params["modified_on.after"] = modified_since
    if project_gid:
        params["projects.any"] = project_gid
    endpoint = f"/workspaces/{workspace_gid}/tasks/search"
    return client.get_paginated(endpoint, params=params)


def build_membership_from_row(row: sqlite3.Row) -> Dict:
    project = {"gid": row["project_gid"], "name": row["project_name"]}
    team_gid = row["team_gid"]
    team_name = row["team_name"]
    if team_gid or team_name:
        project["team"] = {"gid": team_gid, "name": team_name}
    membership: Dict[str, object] = {
        "project": project,
        "is_primary": bool(row["is_primary"]),
    }
    section_gid = row["section_gid"]
    section_name = row["section_name"]
    if section_gid or section_name:
        membership["section"] = {"gid": section_gid, "name": section_name}
    return membership


def fetch_memberships_from_db(connection, task_gid: str) -> List[Dict]:
    rows = connection.execute(
        """
        SELECT
            tp.project_gid,
            tp.section_gid,
            tp.section_name,
            tp.is_primary,
            p.name AS project_name,
            p.team_gid,
            t.name AS team_name
        FROM task_projects tp
        LEFT JOIN projects p ON p.gid = tp.project_gid
        LEFT JOIN teams t ON t.gid = p.team_gid
        WHERE tp.task_gid = ?
        ORDER BY tp.is_primary DESC, COALESCE(p.name, tp.project_gid)
        """,
        (task_gid,),
    ).fetchall()
    return [build_membership_from_row(row) for row in rows]


def ensure_memberships(
    connection,
    client: AsanaHttpClient,
    payload: Dict,
    membership_cache: Dict[str, List[Dict]],
) -> None:
    if payload.get("memberships"):
        task_gid = payload.get("gid")
        if task_gid:
            membership_cache[task_gid] = payload["memberships"]
        return
    derived = derive_memberships_from_parent(connection, client, payload, membership_cache)
    if derived:
        payload["memberships"] = derived
        task_gid = payload.get("gid")
        if task_gid:
            membership_cache[task_gid] = derived


def derive_memberships_from_parent(
    connection,
    client: AsanaHttpClient,
    payload: Dict,
    membership_cache: Dict[str, List[Dict]],
) -> List[Dict]:
    parent = payload.get("parent") or {}
    parent_gid = parent.get("gid")
    if not parent_gid:
        return []
    return lookup_memberships_for_task(
        connection,
        client,
        parent_gid,
        membership_cache,
        visited=set(),
    )


def lookup_memberships_for_task(
    connection,
    client: AsanaHttpClient,
    task_gid: Optional[str],
    membership_cache: Dict[str, List[Dict]],
    visited: Set[str],
) -> List[Dict]:
    if not task_gid or task_gid in visited:
        return []
    if task_gid in membership_cache:
        return membership_cache[task_gid]

    rows = fetch_memberships_from_db(connection, task_gid)
    if rows:
        membership_cache[task_gid] = rows
        return rows

    visited.add(task_gid)
    parent_row = connection.execute(
        "SELECT parent_gid FROM tasks WHERE gid = ?",
        (task_gid,),
    ).fetchone()
    if parent_row and parent_row["parent_gid"]:
        inherited = lookup_memberships_for_task(
            connection,
            client,
            parent_row["parent_gid"],
            membership_cache,
            visited,
        )
        if inherited:
            membership_cache[task_gid] = inherited
            return inherited

    try:
        detail = fetch_task_detail(client, task_gid, TASK_DETAIL_FIELDS)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Unable to infer memberships for task %s: %s", task_gid, exc)
        return []

    memberships = detail.get("memberships") or []
    if memberships:
        membership_cache[task_gid] = memberships
        return memberships

    ancestor_gid = (detail.get("parent") or {}).get("gid")
    if ancestor_gid and ancestor_gid not in visited:
        inherited = lookup_memberships_for_task(
            connection,
            client,
            ancestor_gid,
            membership_cache,
            visited,
        )
        if inherited:
            membership_cache[task_gid] = inherited
            return inherited
    return []


def collect_workspace_summaries(
    client: AsanaHttpClient,
    workspace_gid: str,
    modified_since: Optional[str],
    page_size: int,
    projects: List[ScopeProject],
) -> List[Dict]:
    summary_map: Dict[str, Dict] = {}

    def ingest(label: str, iterable: Iterable[Dict]) -> None:
        start_count = len(summary_map)
        logger.info("[All Teams] %s...", label)
        for item in iterable:
            gid = item.get("gid")
            if not gid or gid in summary_map:
                continue
            summary_map[gid] = item
        added = len(summary_map) - start_count
        logger.info("[All Teams] %s → %s task(s) queued (total: %s).", label, added, len(summary_map))

    ingest(
        "Scanning workspace (global)",
        fetch_workspace_modified_tasks(client, workspace_gid, modified_since, page_size),
    )

    def scan_project(project: ScopeProject) -> Tuple[str, Iterable[Dict]]:
        project_gid = project.get("project_gid")
        if not project_gid:
            return "", ()
        project_name = project.get("project_name") or project_gid
        label = f"Scanning project {project_name} ({project_gid})"
        iterable = fetch_workspace_modified_tasks(
            client,
            workspace_gid,
            modified_since,
            page_size,
            project_gid=project_gid,
        )
        return label, iterable

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [
            executor.submit(scan_project, project)
            for project in projects
            if project.get("project_gid")
        ]
        for future in as_completed(futures):
            label, iterable = future.result()
            if label:
                ingest(label, iterable)

    return list(summary_map.values())


def fetch_project_modified_tasks(
    client: AsanaHttpClient,
    project_gid: str,
    modified_since: Optional[str],
    page_size: int,
) -> Iterable[Dict]:
    return fetch_project_tasks(
        client,
        project_gid,
        TASK_LIST_FIELDS,
        page_size=page_size,
        modified_since=modified_since,
    )


def process_tasks(
    summaries: List[Dict],
    label: str,
    connection,
    database: Database,
    client: AsanaHttpClient,
    user_backfill: UserBackfill,
    known_custom_fields: Set[str],
    existing_tasks: Set[str],
    excluded_team_gids: Set[str],
    page_size: int,
    workers: int,
    sync_time,
    aggregator: SummaryAggregator,
) -> int:
    if not summaries:
        logger.info("%sNo task modifications found in this window.", label)
        return 0

    logger.info("%sProcessing %s modified task(s)...", label, len(summaries))

    executor = ThreadPoolExecutor(max_workers=max(1, workers))
    queue: deque[Tuple[Dict, Future]] = deque()
    seen_task_gids: Set[str] = set()
    membership_cache: Dict[str, List[Dict]] = {}

    def enqueue(summary_task: Dict) -> None:
        task_gid = summary_task.get("gid")
        if not task_gid or task_gid in seen_task_gids:
            return
        memberships = extract_memberships(summary_task)
        if excluded_team_gids and memberships:
            team_candidates = [m.get("team_gid") for m in memberships if m.get("team_gid")]
            if team_candidates and all(team in excluded_team_gids for team in team_candidates):
                return
        seen_task_gids.add(task_gid)
        future = schedule_task_detail(executor, client, task_gid)
        queue.append((summary_task, future))

    for summary in summaries:
        enqueue(summary)

    processed = 0
    committed_since = 0

    try:
        while queue:
            summary, future = queue.popleft()
            try:
                detail = future.result()
            except Exception as exc:  # noqa: BLE001
                logger.error("%sFailed to fetch detail for task %s: %s", label, summary.get("gid"), exc)
                continue

            payload = dict(detail or {})
            for key in ("memberships", "custom_fields", "workspace", "parent"):
                if not payload.get(key) and summary.get(key):
                    payload[key] = summary.get(key)
            for shallow_key in (
                "assignee",
                "assignee_status",
                "assignee_status_updated_at",
                "completed",
                "completed_at",
                "completed_by",
                "due_on",
                "due_at",
                "start_on",
                "start_at",
                "permalink_url",
                "actual_time_minutes",
                "liked",
                "num_likes",
            ):
                if shallow_key not in payload and shallow_key in summary:
                    payload[shallow_key] = summary.get(shallow_key)

            task_gid = payload.get("gid")
            if not task_gid:
                continue

            ensure_memberships(connection, client, payload, membership_cache)

            field_gids = collect_custom_field_gids(payload)
            ensure_field_definitions(connection, database, client, known_custom_fields, field_gids, sync_time)

            user_gids = collect_user_gids_from_task(payload)
            user_backfill.ensure_users(connection, user_gids, sync_time)

            task_created = task_gid not in existing_tasks
            database.upsert_task(connection, payload, sync_time)
            if task_created:
                existing_tasks.add(task_gid)

            comments = fetch_task_comments(client, task_gid)
            attachments = fetch_task_attachments(client, task_gid)

            extra_user_gids = collect_comment_and_attachment_users(comments, attachments)
            user_backfill.ensure_users(connection, extra_user_gids, sync_time)

            comment_stats = database.replace_task_comments(connection, task_gid, comments, sync_time)
            attachment_stats = database.replace_task_attachments(connection, task_gid, attachments, sync_time)

            task_counts = {
                "analyzed": 1,
                "created": 1 if task_created else 0,
                "updated": 0 if task_created else 1,
            }

            memberships = extract_memberships(payload)
            aggregator.record(memberships, task_counts, comment_stats, attachment_stats)

            processed += 1
            committed_since += 1
            if committed_since >= COMMIT_INTERVAL:
                connection.commit()
                committed_since = 0

            if processed == 1 or processed % PROGRESS_INTERVAL == 0:
                logger.info(
                    "%sProcessed %s task(s) (comments: %s, attachments: %s).",
                    label,
                    processed,
                    comment_stats["analyzed"],
                    attachment_stats["analyzed"],
                )

            try:
                for subtask in schedule_subtask_listing(client, task_gid, page_size):
                    enqueue(subtask)
            except Exception as exc:  # noqa: BLE001
                logger.warning("%sFailed to fetch subtasks for %s: %s", label, task_gid, exc)
    finally:
        executor.shutdown(wait=True)

    if committed_since:
        connection.commit()

    logger.info("%sCompleted incremental sync for %s task(s).", label, processed)
    return processed


def main(argv: Optional[Iterable[str]] = None) -> None:
    args = parse_args(argv)
    try:
        log_level = resolve_log_level(args.log_level)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    configure_logging(log_level)

    validate_scope_args(
        args.project_gid,
        args.team,
        args.team_gids,
        args.all_teams,
        args.exclude_team_gids,
        None,
    )

    credentials = load_credentials(DEFAULT_CONFIG_ROOT)
    workspace_gid = args.workspace_gid or credentials.workspace_gid

    client = AsanaHttpClient(
        token=credentials.token,
        skip_ssl_verification=credentials.skip_ssl_verification,
    )
    database = Database(db_path=resolve_default_db_path(DEFAULT_DB_PATH))
    sync_time = synced_at_timestamp()

    all_teams_mode = (
        args.all_teams
        and not args.project_gid
        and not args.team
        and not args.team_gids
    )
    excluded_team_gids = set(parse_gid_list(args.exclude_team_gids))

    with database.connect() as connection:
        database.initialize(connection)
        user_backfill = UserBackfill(client, database)
        user_backfill.prime(connection)

        team_lookup = {
            row["gid"]: row["name"]
            for row in connection.execute("SELECT gid, name FROM teams")
        }
        project_lookup = {
            row["gid"]: row["name"]
            for row in connection.execute("SELECT gid, name FROM projects")
        }

        aggregator = SummaryAggregator(team_lookup, project_lookup, excluded_team_gids)

        existing_tasks = {
            row["gid"] for row in connection.execute("SELECT gid FROM tasks")
        }
        known_custom_fields: Set[str] = {
            row["gid"]
            for row in connection.execute("SELECT gid FROM custom_fields WHERE gid IS NOT NULL")
        }

        projects = determine_projects(
            connection=connection,
            project_gid=args.project_gid,
            use_team_picker=args.team,
            team_gids=args.team_gids,
            all_teams=args.all_teams,
            exclude_team_gids=args.exclude_team_gids,
            team_selector=choose_team,
        )

        modified_since, effective_days_back, lookback_desc = resolve_modified_since(
            connection=connection,
            projects=projects,
            all_teams_mode=all_teams_mode,
            excluded_team_gids=excluded_team_gids,
            use_auto=args.auto_from_last_update,
            requested_days_back=args.days_back,
        )

        logger.info(
            "Starting incremental sync for workspace %s (lookback: %s).",
            workspace_gid,
            lookback_desc,
        )

        if all_teams_mode:
            summaries = collect_workspace_summaries(
                client,
                workspace_gid,
                modified_since,
                args.page_size,
                projects,
            )
            process_tasks(
                summaries,
                label="[All Teams] ",
                connection=connection,
                database=database,
                client=client,
                user_backfill=user_backfill,
                known_custom_fields=known_custom_fields,
                existing_tasks=existing_tasks,
                excluded_team_gids=excluded_team_gids,
                page_size=args.page_size,
                workers=args.workers,
                sync_time=sync_time,
                aggregator=aggregator,
            )
        else:
            for project in projects:
                team_gid = project.get("team_gid")
                if team_gid and team_gid in excluded_team_gids:
                    continue
                project_gid = project["project_gid"]
                project_name = project["project_name"]
                label = f"[{project.get('team_name') or 'Project'}] "
                summaries = collect_task_summaries(
                    fetch_project_modified_tasks(
                        client, project_gid, modified_since, args.page_size
                    )
                )
                process_tasks(
                    summaries,
                    label=label,
                    connection=connection,
                    database=database,
                    client=client,
                    user_backfill=user_backfill,
                    known_custom_fields=known_custom_fields,
                    existing_tasks=existing_tasks,
                    excluded_team_gids=excluded_team_gids,
                    page_size=args.page_size,
                    workers=args.workers,
                    sync_time=sync_time,
                    aggregator=aggregator,
                )

        connection.commit()

    aggregator.print_summary(lookback_desc)
    aggregator.prompt_project_breakdown(mode=args.project_breakdown)


if __name__ == "__main__":
    main()
