"""Interactive helpers for selecting teams from the local SQLite cache."""

from __future__ import annotations

import math
import sys


def choose_team(connection) -> tuple[str, str] | None:
    """Display a paginated team picker and return the chosen team (gid, name)."""
    query = """
        SELECT
            teams.gid AS team_gid,
            teams.name AS team_name,
            COUNT(DISTINCT projects.gid) AS project_count,
            MAX(tasks.synced_at) AS last_task_sync
        FROM teams
        LEFT JOIN projects ON projects.team_gid = teams.gid
        LEFT JOIN task_projects ON task_projects.project_gid = projects.gid
        LEFT JOIN tasks ON tasks.gid = task_projects.task_gid
        GROUP BY teams.gid, teams.name
        ORDER BY
            CASE WHEN last_task_sync IS NULL THEN 0 ELSE 1 END ASC,
            project_count DESC,
            teams.name ASC
    """
    rows = list(connection.execute(query))
    if not rows:
        print("No teams found in the database. Run get_teams.py first.", file=sys.stderr)
        return None

    page = 0
    per_page = 10
    total_pages = math.ceil(len(rows) / per_page)
    while True:
        start = page * per_page
        end = start + per_page
        slice_rows = rows[start:end]
        if not slice_rows:
            print("No more teams in this direction.", file=sys.stderr)
            page = max(page - 1, 0)
            continue
        print("\nSelect a team to sync:")
        print(f"{'No.':>4}  {'Team Name':<40}  {'Projects':>8}  {'Last Task Import':<20}")
        print("-" * 80)
        for offset, row in enumerate(slice_rows, start=start + 1):
            last_sync = row["last_task_sync"] or "—"
            print(f"{offset:>4}  {row['team_name']:<40}  {row['project_count']:>8}  {last_sync:<20}")
        print(f"\nPage {page + 1} of {total_pages} (n = next, p = previous, q = quit)")
        choice = input("Enter selection: ").strip().lower()
        if choice == "q":
            return None
        if choice == "n":
            if page + 1 < total_pages:
                page += 1
            else:
                print("Already at last page.", file=sys.stderr)
            continue
        if choice == "p":
            if page > 0:
                page -= 1
            else:
                print("Already at first page.", file=sys.stderr)
            continue
        if not choice.isdigit():
            print("Please enter a team number, 'n', 'p', or 'q'.", file=sys.stderr)
            continue
        index = int(choice)
        if index < 1 or index > len(rows):
            print("Selection out of range.", file=sys.stderr)
            continue
        selected = rows[index - 1]
        return selected["team_gid"], selected["team_name"]
