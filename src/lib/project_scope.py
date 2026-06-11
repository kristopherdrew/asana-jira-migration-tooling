"""Helpers for resolving project scopes across multiple scripts."""

from __future__ import annotations

from typing import Callable, Dict, Iterable, List, Optional


ScopeProject = Dict[str, Optional[str]]
ARCHIVED_SCOPE_CHOICES = ("active", "archived", "all")


def _archived_clause(scope: Optional[str]) -> str:
    if scope == "active":
        return " AND COALESCE(p.archived, 0) = 0"
    if scope == "archived":
        return " AND COALESCE(p.archived, 0) = 1"
    if scope is not None and scope not in ARCHIVED_SCOPE_CHOICES:
        raise ValueError(f"Unknown archived scope '{scope}'.")
    return ""


def parse_gid_list(raw: Optional[str]) -> List[str]:
    """Return a cleaned list of Asana GIDs from a comma-separated string."""
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def validate_scope_args(
    project_gid: Optional[str],
    use_team_picker: bool,
    team_gids: Optional[str],
    all_teams: bool,
    exclude_team_gids: Optional[str],
) -> None:
    """Ensure the caller specified exactly one scope source."""
    scope_flags = [
        bool(project_gid),
        bool(use_team_picker),
        bool(team_gids),
        bool(all_teams),
    ]
    if sum(scope_flags) == 0:
        raise SystemExit(
            "No project scope specified. Provide --project-gid, --team, "
            "--team-gids, or --all-teams."
        )
    if sum(scope_flags) > 1:
        raise SystemExit(
            "Multiple project scopes specified. Choose only one of "
            "--project-gid, --team, --team-gids, or --all-teams."
        )
    if exclude_team_gids and not all_teams:
        raise SystemExit("--exclude-team-gids can only be used with --all-teams.")


def _projects_for_team(connection, team_gid: str, archived_scope: Optional[str]) -> List[ScopeProject]:
    rows = list(
        connection.execute(
            f"""
            SELECT p.gid, p.name, p.team_gid, t.name AS team_name
            FROM projects p
            LEFT JOIN teams t ON t.gid = p.team_gid
            WHERE p.team_gid = ?{_archived_clause(archived_scope)}
            ORDER BY p.name
            """,
            (team_gid,),
        )
    )
    return [
        {
            "project_gid": row["gid"],
            "project_name": row["name"],
            "team_gid": row["team_gid"],
            "team_name": row["team_name"],
        }
        for row in rows
    ]


def determine_projects(
    connection,
    project_gid: Optional[str],
    use_team_picker: bool,
    team_gids: Optional[str],
    all_teams: bool,
    exclude_team_gids: Optional[str],
    team_selector: Callable[[object], Optional[tuple]],
    archived_scope: Optional[str] = None,
) -> List[ScopeProject]:
    """Resolve the set of projects to process based on CLI scope arguments."""
    validate_scope_args(project_gid, use_team_picker, team_gids, all_teams, exclude_team_gids)

    if project_gid:
        row = connection.execute(
            """
            SELECT p.gid, p.name, p.team_gid, t.name AS team_name
            FROM projects p
            LEFT JOIN teams t ON t.gid = p.team_gid
            WHERE p.gid = ?
            """,
            (project_gid,),
        ).fetchone()
        if not row:
            raise SystemExit(f"Project with gid {project_gid} not found in database.")
        return [
            {
                "project_gid": row["gid"],
                "project_name": row["name"],
                "team_gid": row["team_gid"],
                "team_name": row["team_name"],
            }
        ]

    if team_gids:
        parsed_team_gids = parse_gid_list(team_gids)
        if not parsed_team_gids:
            raise SystemExit("No valid GIDs provided in --team-gids.")
        projects: List[ScopeProject] = []
        for team_gid in parsed_team_gids:
            team_row = connection.execute(
                "SELECT gid, name FROM teams WHERE gid = ?",
                (team_gid,),
            ).fetchone()
            if not team_row:
                raise SystemExit(f"Team with gid {team_gid} not found in database.")
            team_projects = _projects_for_team(connection, team_gid, archived_scope)
            if not team_projects:
                print(f"No projects linked to team '{team_row['name']}' ({team_gid}). Skipping.")
                continue
            print(f"Queueing {len(team_projects)} project(s) for team '{team_row['name']}'.")
            projects.extend(team_projects)
        if not projects:
            raise SystemExit("No projects found for the requested team GIDs.")
        return projects

    if all_teams:
        excluded = set(parse_gid_list(exclude_team_gids))
        rows = list(connection.execute("SELECT gid, name FROM teams ORDER BY name"))
        projects: List[ScopeProject] = []
        for team in rows:
            if team["gid"] in excluded:
                continue
            team_projects = _projects_for_team(connection, team["gid"], archived_scope)
            if not team_projects:
                continue
            print(f"Queueing {len(team_projects)} project(s) for team '{team['name']}'.")
            projects.extend(team_projects)
        if not projects:
            raise SystemExit("No projects found when scanning all teams.")
        return projects

    if use_team_picker:
        selection = team_selector(connection)
        if not selection:
            print("No team selected. Exiting.")
            raise SystemExit(0)
        team_gid, team_name = selection
        team_projects = _projects_for_team(connection, team_gid, archived_scope)
        if not team_projects:
            raise SystemExit(f"No projects linked to team '{team_name}'.")
        print(f"Selected team '{team_name}' with {len(team_projects)} projects.")
        return team_projects

    raise SystemExit(
        "No project specified. Provide --project-gid, --team, --team-gids, or --all-teams."
    )
