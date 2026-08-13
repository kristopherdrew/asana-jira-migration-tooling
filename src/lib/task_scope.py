"""Shared helpers for resolving explicit task-GID export scopes."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


def parse_task_gids(
    task_gids: Optional[str] = None,
    task_gids_file: Optional[str] = None,
) -> List[str]:
    """Parse an inline or file-backed task GID list, preserving input order."""

    if bool(task_gids) == bool(task_gids_file):
        raise SystemExit("Provide exactly one of --task-gids or --task-gids-file.")
    if task_gids_file:
        path = Path(task_gids_file)
        if not path.exists():
            raise SystemExit(f"Task gids file not found: {task_gids_file}")
        task_gids = path.read_text(encoding="utf-8").replace("\r", "\n").replace("\n", ",")

    ordered: List[str] = []
    seen: Set[str] = set()
    for gid in (task_gids or "").split(","):
        gid = gid.strip()
        if gid and gid not in seen:
            seen.add(gid)
            ordered.append(gid)
    if not ordered:
        raise SystemExit("No valid task gids provided.")
    return ordered


def resolve_task_projects(
    connection: sqlite3.Connection,
    task_gids: List[str],
    logger: logging.Logger,
) -> Tuple[List[Tuple[str, str]], Dict[str, Set[str]]]:
    """Assign each requested task to one project and return per-project filters."""

    placeholders = ",".join("?" for _ in task_gids)
    rows = connection.execute(
        f"""
        SELECT t.gid AS task_gid, tp.project_gid, p.name AS project_name,
               COALESCE(tp.is_primary, 0) AS is_primary
        FROM tasks t
        LEFT JOIN task_projects tp ON tp.task_gid = t.gid
        LEFT JOIN projects p ON p.gid = tp.project_gid
        WHERE t.gid IN ({placeholders})
        ORDER BY t.gid, is_primary DESC, tp.project_gid
        """,
        task_gids,
    ).fetchall()
    memberships: Dict[str, List[sqlite3.Row]] = {}
    for row in rows:
        memberships.setdefault(row["task_gid"], []).append(row)

    missing = [gid for gid in task_gids if gid not in memberships]
    projectless = [
        gid for gid in task_gids
        if gid in memberships and not memberships[gid][0]["project_gid"]
    ]
    if missing:
        logger.warning("Task gids not found: %s", ", ".join(missing))
    if projectless:
        logger.warning("Task gids have no project membership: %s", ", ".join(projectless))

    project_names: Dict[str, str] = {}
    filters: Dict[str, Set[str]] = {}
    for gid in task_gids:
        candidates = memberships.get(gid) or []
        if not candidates or not candidates[0]["project_gid"]:
            continue
        selected = candidates[0]
        project_gid = selected["project_gid"]
        project_names[project_gid] = selected["project_name"] or project_gid
        filters.setdefault(project_gid, set()).add(gid)
    if not filters:
        raise SystemExit("No requested tasks with project memberships were found.")
    projects = [(gid, project_names[gid]) for gid in filters]
    return projects, filters
