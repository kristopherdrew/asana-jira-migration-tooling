"""Shared team analysis helpers for report and Jira export selection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional


def _clean(value: Optional[str]) -> str:
    return (value or "").strip()


def _is_active_atlassian(status: Optional[str]) -> bool:
    return _clean(status).casefold() == "active"


def _display_value(atlassian_email: str, email: str, name: str, gid: str) -> str:
    return atlassian_email or email or name or gid


@dataclass(frozen=True)
class TeamCandidate:
    user_gid: str
    display_name: str
    email: str
    atlassian_id: str
    atlassian_email: str
    atlassian_status: str
    is_active_atlassian: bool
    count: int = 0

    @property
    def display_value(self) -> str:
        return _display_value(self.atlassian_email, self.email, self.display_name, self.user_gid)


@dataclass(frozen=True)
class TeamAnalysis:
    team_gid: str
    team_name: str
    first_admin_gid: str
    first_admin_email: str
    additional_admin_emails: str
    effective_admin: str
    effective_admin_gid: str
    effective_admin_email: str
    effective_admin_atlassian_id: str
    effective_admin_atlassian_email: str
    effective_admin_atlassian_status: str
    effective_admin_source: str
    project_owner: str
    project_owner_gid: str
    project_owner_email: str
    project_owner_atlassian_id: str
    project_owner_atlassian_email: str
    project_owner_atlassian_status: str
    project_owner_project_count: int
    task_creator: str
    task_creator_gid: str
    task_creator_email: str
    task_creator_atlassian_id: str
    task_creator_atlassian_email: str
    task_creator_atlassian_status: str
    task_creator_task_count: int


def _row_to_candidate(
    row: dict,
    gid_key: str,
    display_key: str,
    email_key: str,
    atlassian_id_key: str,
    atlassian_email_key: str,
    atlassian_status_key: str,
    count_key: Optional[str] = None,
) -> TeamCandidate:
    count = int(row[count_key] or 0) if count_key else 0
    atlassian_status = _clean(row[atlassian_status_key])
    return TeamCandidate(
        user_gid=_clean(row[gid_key]),
        display_name=_clean(row[display_key]),
        email=_clean(row[email_key]),
        atlassian_id=_clean(row[atlassian_id_key]),
        atlassian_email=_clean(row[atlassian_email_key]),
        atlassian_status=atlassian_status,
        is_active_atlassian=_is_active_atlassian(atlassian_status),
        count=count,
    )


def _choose_first_active_admin(admins: List[TeamCandidate]) -> Optional[TeamCandidate]:
    for admin in admins:
        if admin.is_active_atlassian:
            return admin
    return None


def _choose_top_active_candidate(candidates: List[TeamCandidate]) -> Optional[TeamCandidate]:
    active_candidates = [candidate for candidate in candidates if candidate.is_active_atlassian]
    if not active_candidates:
        return None
    return sorted(
        active_candidates,
        key=lambda candidate: (-candidate.count, candidate.display_value.casefold(), candidate.user_gid),
    )[0]


def build_team_analysis(connection) -> Dict[str, TeamAnalysis]:
    teams = {
        row["gid"]: row["name"] or row["gid"]
        for row in connection.execute(
            """
            SELECT gid, name
            FROM teams
            """
        ).fetchall()
    }

    admin_rows = connection.execute(
        """
        SELECT
            tm.team_gid,
            tm.user_gid,
            COALESCE(u.name, '') AS user_name,
            COALESCE(u.email, '') AS user_email,
            COALESCE(ua.atlassian_account_id, '') AS atlassian_account_id,
            COALESCE(ua.atlassian_email, '') AS atlassian_email,
            COALESCE(ua.atlassian_status, '') AS atlassian_status
        FROM team_members tm
        LEFT JOIN users u
            ON u.gid = tm.user_gid
        LEFT JOIN user_atlassian_accounts ua
            ON ua.user_gid = tm.user_gid
        WHERE COALESCE(tm.is_admin, 0) = 1
        ORDER BY tm.team_gid,
            COALESCE(u.email, u.name, tm.user_gid) COLLATE NOCASE,
            tm.user_gid
        """
    ).fetchall()

    owner_rows = connection.execute(
        """
        SELECT
            p.team_gid,
            p.owner_gid AS user_gid,
            COALESCE(u.name, '') AS user_name,
            COALESCE(u.email, '') AS user_email,
            COALESCE(ua.atlassian_account_id, '') AS atlassian_account_id,
            COALESCE(ua.atlassian_email, '') AS atlassian_email,
            COALESCE(ua.atlassian_status, '') AS atlassian_status,
            COUNT(DISTINCT p.gid) AS project_count
        FROM projects p
        LEFT JOIN users u
            ON u.gid = p.owner_gid
        LEFT JOIN user_atlassian_accounts ua
            ON ua.user_gid = p.owner_gid
        WHERE p.team_gid IS NOT NULL
            AND TRIM(COALESCE(p.owner_gid, '')) != ''
        GROUP BY p.team_gid, p.owner_gid
        ORDER BY p.team_gid,
            project_count DESC,
            COALESCE(u.email, u.name, p.owner_gid) COLLATE NOCASE,
            p.owner_gid
        """
    ).fetchall()

    creator_rows = connection.execute(
        """
        SELECT
            p.team_gid,
            t.created_by_gid AS user_gid,
            COALESCE(u.name, '') AS user_name,
            COALESCE(u.email, '') AS user_email,
            COALESCE(ua.atlassian_account_id, '') AS atlassian_account_id,
            COALESCE(ua.atlassian_email, '') AS atlassian_email,
            COALESCE(ua.atlassian_status, '') AS atlassian_status,
            COUNT(DISTINCT t.gid) AS task_count
        FROM tasks t
        JOIN task_projects tp
            ON tp.task_gid = t.gid
        JOIN projects p
            ON p.gid = tp.project_gid
        LEFT JOIN users u
            ON u.gid = t.created_by_gid
        LEFT JOIN user_atlassian_accounts ua
            ON ua.user_gid = t.created_by_gid
        WHERE p.team_gid IS NOT NULL
            AND TRIM(COALESCE(t.created_by_gid, '')) != ''
        GROUP BY p.team_gid, t.created_by_gid
        ORDER BY p.team_gid,
            task_count DESC,
            COALESCE(u.email, u.name, t.created_by_gid) COLLATE NOCASE,
            t.created_by_gid
        """
    ).fetchall()

    admins_by_team: Dict[str, List[TeamCandidate]] = {team_gid: [] for team_gid in teams}
    for row in admin_rows:
        candidate = _row_to_candidate(
            row,
            gid_key="user_gid",
            display_key="user_name",
            email_key="user_email",
            atlassian_id_key="atlassian_account_id",
            atlassian_email_key="atlassian_email",
            atlassian_status_key="atlassian_status",
        )
        admins_by_team.setdefault(row["team_gid"], []).append(candidate)

    owners_by_team: Dict[str, List[TeamCandidate]] = {team_gid: [] for team_gid in teams}
    for row in owner_rows:
        candidate = _row_to_candidate(
            row,
            gid_key="user_gid",
            display_key="user_name",
            email_key="user_email",
            atlassian_id_key="atlassian_account_id",
            atlassian_email_key="atlassian_email",
            atlassian_status_key="atlassian_status",
            count_key="project_count",
        )
        owners_by_team.setdefault(row["team_gid"], []).append(candidate)

    creators_by_team: Dict[str, List[TeamCandidate]] = {team_gid: [] for team_gid in teams}
    for row in creator_rows:
        candidate = _row_to_candidate(
            row,
            gid_key="user_gid",
            display_key="user_name",
            email_key="user_email",
            atlassian_id_key="atlassian_account_id",
            atlassian_email_key="atlassian_email",
            atlassian_status_key="atlassian_status",
            count_key="task_count",
        )
        creators_by_team.setdefault(row["team_gid"], []).append(candidate)

    analyses: Dict[str, TeamAnalysis] = {}
    for team_gid, team_name in teams.items():
        admins = admins_by_team.get(team_gid, [])
        project_owners = owners_by_team.get(team_gid, [])
        task_creators = creators_by_team.get(team_gid, [])

        first_admin = admins[0] if admins else None
        first_active_admin = _choose_first_active_admin(admins)
        top_project_owner = project_owners[0] if project_owners else None
        top_active_task_creator = _choose_top_active_candidate(task_creators)

        effective_candidate: Optional[TeamCandidate]
        effective_source = ""
        if first_active_admin is not None:
            effective_candidate = first_active_admin
            effective_source = "team_admin"
        elif top_project_owner is not None and top_project_owner.is_active_atlassian:
            effective_candidate = top_project_owner
            effective_source = "project_owner"
        else:
            effective_candidate = top_active_task_creator
            if effective_candidate is not None:
                effective_source = "task_creator"

        analyses[team_gid] = TeamAnalysis(
            team_gid=team_gid,
            team_name=team_name,
            first_admin_gid=first_admin.user_gid if first_admin else "",
            first_admin_email=first_admin.display_value if first_admin else "",
            additional_admin_emails=", ".join(
                admin.display_value for admin in admins[1:] if admin.display_value
            ),
            effective_admin=effective_candidate.display_value if effective_candidate else "",
            effective_admin_gid=effective_candidate.user_gid if effective_candidate else "",
            effective_admin_email=effective_candidate.email if effective_candidate else "",
            effective_admin_atlassian_id=effective_candidate.atlassian_id if effective_candidate else "",
            effective_admin_atlassian_email=effective_candidate.atlassian_email if effective_candidate else "",
            effective_admin_atlassian_status=effective_candidate.atlassian_status if effective_candidate else "",
            effective_admin_source=effective_source,
            project_owner=top_project_owner.display_value if top_project_owner else "",
            project_owner_gid=top_project_owner.user_gid if top_project_owner else "",
            project_owner_email=top_project_owner.email if top_project_owner else "",
            project_owner_atlassian_id=top_project_owner.atlassian_id if top_project_owner else "",
            project_owner_atlassian_email=top_project_owner.atlassian_email if top_project_owner else "",
            project_owner_atlassian_status=top_project_owner.atlassian_status if top_project_owner else "",
            project_owner_project_count=top_project_owner.count if top_project_owner else 0,
            task_creator=top_active_task_creator.display_value if top_active_task_creator else "",
            task_creator_gid=top_active_task_creator.user_gid if top_active_task_creator else "",
            task_creator_email=top_active_task_creator.email if top_active_task_creator else "",
            task_creator_atlassian_id=top_active_task_creator.atlassian_id if top_active_task_creator else "",
            task_creator_atlassian_email=top_active_task_creator.atlassian_email if top_active_task_creator else "",
            task_creator_atlassian_status=top_active_task_creator.atlassian_status if top_active_task_creator else "",
            task_creator_task_count=top_active_task_creator.count if top_active_task_creator else 0,
        )

    return analyses
