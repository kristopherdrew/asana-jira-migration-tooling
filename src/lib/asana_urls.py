"""Helpers for building Asana deep links from stored identifiers."""

from __future__ import annotations

from typing import Optional


def build_team_url(team_gid: Optional[str]) -> str:
    if not team_gid:
        return ""
    return f"https://app.asana.com/0/{team_gid}/"


def build_project_url(workspace_gid: Optional[str], project_gid: Optional[str]) -> str:
    if not workspace_gid or not project_gid:
        return ""
    return f"https://app.asana.com/1/{workspace_gid}/project/{project_gid}"


def build_user_url(workspace_gid: Optional[str], user_gid: Optional[str]) -> str:
    if not workspace_gid or not user_gid:
        return ""
    return f"https://app.asana.com/1/{workspace_gid}/profile/{user_gid}"


def infer_workspace_gid(connection) -> Optional[str]:
    candidates: list[str] = []
    seen: set[str] = set()
    for table in ("user_workspaces", "tasks"):
        rows = connection.execute(
            f"""
            SELECT DISTINCT workspace_gid
            FROM {table}
            WHERE workspace_gid IS NOT NULL
              AND workspace_gid != ''
            """
        ).fetchall()
        for row in rows:
            workspace_gid = (row["workspace_gid"] or "").strip()
            if not workspace_gid or workspace_gid in seen:
                continue
            seen.add(workspace_gid)
            candidates.append(workspace_gid)
            if len(candidates) > 1:
                return None
    if len(candidates) == 1:
        return candidates[0]
    return None
