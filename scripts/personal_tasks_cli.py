from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass(frozen=True)
class AssigneeTarget:
    selector: str
    label: str


def resolve_assignees(connection, assignee: Optional[str], all_users: bool) -> List[AssigneeTarget]:
    """Determine which assignees to sync for personal task scripts."""
    if all_users:
        rows = connection.execute(
            """
            SELECT gid, COALESCE(name, gid) AS name
            FROM users
            WHERE gid IS NOT NULL
            ORDER BY name COLLATE NOCASE
            """
        ).fetchall()
        selectors = [AssigneeTarget(selector=row["gid"], label=row["name"]) for row in rows if row["gid"]]
        if not selectors:
            raise SystemExit(
                "No users with GIDs were found in SQLite. Run `python3 -m scripts.get_users` before --all-users."
            )
        return selectors

    selector = assignee or "me"
    label = selector
    if selector != "me":
        row = connection.execute("SELECT COALESCE(name, ?) AS name FROM users WHERE gid = ?", (selector, selector)).fetchone()
        if row and row["name"]:
            label = row["name"]
    return [AssigneeTarget(selector=selector, label=label)]
