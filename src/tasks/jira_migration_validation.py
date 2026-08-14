"""Validate Asana task coverage against recovered Jira issue mappings."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class ValidationSummary:
    total: int
    found: int
    missing: int
    coverage_percent: float


def resolve_jira_site(
    connection: sqlite3.Connection,
    jira_site_key: Optional[str],
    project_gids: Optional[List[str]] = None,
    team_gid: Optional[str] = None,
    task_gids: Optional[List[str]] = None,
) -> str:
    if jira_site_key:
        return jira_site_key
    params: List[str] = []
    where = ""
    if project_gids:
        placeholders = ",".join("?" for _ in project_gids)
        where = f" WHERE project_gid IN ({placeholders})"
        params.extend(project_gids)
    elif team_gid:
        where = " WHERE project_gid IN (SELECT gid FROM projects WHERE team_gid = ?)"
        params.append(team_gid)
    elif task_gids:
        placeholders = ",".join("?" for _ in task_gids)
        where = (
            " WHERE project_gid IN (SELECT project_gid FROM task_projects "
            f"WHERE task_gid IN ({placeholders}))"
        )
        params.extend(task_gids)
    rows = connection.execute(
        f"SELECT DISTINCT jira_site_key FROM jira_project_mappings{where} ORDER BY jira_site_key",
        params,
    ).fetchall()
    sites = [row["jira_site_key"] for row in rows]
    if len(sites) == 1:
        return sites[0]
    if not sites:
        raise ValueError("No Asana Project to Jira Space associations were found.")
    raise ValueError("Multiple Jira sites have project associations; pass --jira-site.")


def fetch_task_validation(
    connection: sqlite3.Connection,
    jira_site_key: Optional[str] = None,
    project_gids: Optional[List[str]] = None,
    team_gid: Optional[str] = None,
    task_gids: Optional[List[str]] = None,
) -> tuple[str, List[Dict[str, object]], ValidationSummary]:
    """Return distinct associated tasks and their single Jira mapping."""
    site = resolve_jira_site(connection, jira_site_key, project_gids, team_gid, task_gids)
    params: List[str] = [site, site]
    project_filter = ""
    if project_gids:
        placeholders = ",".join("?" for _ in project_gids)
        known = {
            row["gid"]
            for row in connection.execute(
                f"SELECT gid FROM projects WHERE gid IN ({placeholders})", project_gids
            )
        }
        unknown = [gid for gid in project_gids if gid not in known]
        if unknown:
            raise ValueError(f"Project {unknown[0]} was not found in the Asana snapshot.")
        associated = {
            row["project_gid"]
            for row in connection.execute(
                f"SELECT project_gid FROM jira_project_mappings WHERE jira_site_key = ? AND project_gid IN ({placeholders})",
                (site, *project_gids),
            )
        }
        missing_associations = [gid for gid in project_gids if gid not in associated]
        if missing_associations:
            raise ValueError(
                f"Project {missing_associations[0]} has no Jira Space association for site {site}."
            )
        project_filter = f" AND p.gid IN ({placeholders})"
        params.extend(project_gids)
    elif team_gid:
        if not connection.execute("SELECT 1 FROM teams WHERE gid = ?", (team_gid,)).fetchone():
            raise ValueError(f"Team {team_gid} was not found in the Asana snapshot.")
        project_filter = " AND p.team_gid = ?"
        params.append(team_gid)
    elif task_gids:
        placeholders = ",".join("?" for _ in task_gids)
        known = {
            row["gid"]
            for row in connection.execute(
                f"SELECT gid FROM tasks WHERE gid IN ({placeholders})", task_gids
            )
        }
        unknown = [gid for gid in task_gids if gid not in known]
        if unknown:
            raise ValueError(f"Task {unknown[0]} was not found in the Asana snapshot.")
        associated = {
            row["task_gid"]
            for row in connection.execute(
                f"""
                SELECT DISTINCT tp.task_gid
                FROM task_projects tp
                JOIN jira_project_mappings jpm ON jpm.project_gid = tp.project_gid
                WHERE jpm.jira_site_key = ? AND tp.task_gid IN ({placeholders})
                """,
                (site, *task_gids),
            )
        }
        missing_associations = [gid for gid in task_gids if gid not in associated]
        if missing_associations:
            raise ValueError(
                f"Task {missing_associations[0]} does not belong to a project associated with Jira site {site}."
            )
        project_filter = f" AND t.gid IN ({placeholders})"
        params.extend(task_gids)
    rows = connection.execute(
        f"""
        SELECT t.gid AS task_gid,
               t.name AS task_name,
               t.completed,
               GROUP_CONCAT(DISTINCT p.gid) AS project_gids,
               GROUP_CONCAT(DISTINCT p.name) AS project_names,
               tjm.jira_key
        FROM tasks t
        JOIN task_projects tp ON tp.task_gid = t.gid
        JOIN projects p ON p.gid = tp.project_gid
        JOIN jira_project_mappings jpm
          ON jpm.project_gid = p.gid AND jpm.jira_site_key = ?
        LEFT JOIN task_jira_mappings tjm
          ON tjm.task_gid = t.gid AND tjm.jira_site_key = ?
        WHERE 1 = 1 {project_filter}
        GROUP BY t.gid, t.name, t.completed, tjm.jira_key
        ORDER BY CASE WHEN tjm.jira_key IS NULL THEN 0 ELSE 1 END,
                 t.name COLLATE NOCASE,
                 t.gid
        """,
        params,
    ).fetchall()
    records = [dict(row) for row in rows]
    total = len(records)
    found = sum(1 for row in records if row["jira_key"])
    missing = total - found
    percentage = round((found / total * 100.0) if total else 0.0, 2)
    return site, records, ValidationSummary(total, found, missing, percentage)
