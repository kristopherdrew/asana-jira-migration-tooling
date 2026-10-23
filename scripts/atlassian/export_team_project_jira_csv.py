#!/usr/bin/env python3
"""Export Asana teams and projects into a single Jira import CSV."""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, NamedTuple, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.lib.adf import (
    BulletList,
    Heading,
    Paragraph,
    Run,
    render_blocks_adf,
    render_blocks_text,
)
from src.lib.asana_urls import (
    build_project_url,
    build_team_url,
    build_user_task_list_url,
    infer_workspace_gid,
)
from src.lib.atlassian_status import is_active_atlassian_status
from src.lib.database import Database, resolve_default_db_path
from src.lib.date_formatting import format_report_date, parse_iso_datetime
from src.lib.logging_utils import configure_logging, resolve_log_level
from src.lib.project_scope import parse_gid_list, read_project_gids_file
from src.lib.team_analysis import build_team_analysis


DEFAULT_DB_PATH = "data/asana.sqlite"
DEFAULT_OUTPUT_DIR = "exports"
logger = logging.getLogger(__name__)


# Instruction copy is authored once as structured blocks, then rendered to plain text
# (for the CSV export) and to rich ADF (for the direct Jira API path) so the two paths
# never drift. See src/lib/adf.py for the block model.
EPIC_INSTRUCTION_BLOCKS = [
    Heading("Instructions", level=1),
    Paragraph(["This Epic represents an Asana team that you are the effective owner of."]),
    Paragraph(["The Epic assignee is responsible for:"]),
    BulletList(
        [
            [
                "If someone else should be representing this Asana team, please reassign "
                "this epic to that person."
            ],
            [
                "Review all tasks within the Epic (which represent individual Asana "
                "projects). If a project owner is incorrect, reassign the task immediately."
            ],
            ["Ensure a migration decision has been recorded for every project by July 8."],
        ]
    ),
    Paragraph(
        [
            "The Epic should not be considered complete until all child tasks contain a "
            "documented migration decision and any required supporting information."
        ]
    ),
]


TASK_INSTRUCTION_BLOCKS = [
    Heading("Instructions", level=1),
    Paragraph(
        ["A migration decision is required for this project. Select one of the following options:"]
    ),
    Heading("Active", level=3),
    BulletList(
        [
            [
                "Select Active if the project is currently being used, ongoing work is "
                "taking place or there are reporting requirements."
            ],
            [
                "The migration team will be reaching out with next steps to align on Jira "
                "project configurations."
            ],
        ]
    ),
    Heading("Archive-Read Only", level=3),
    BulletList(
        [
            [
                "Select Archive if the project is no longer actively used but the "
                "historical information should be retained and be able to be queried."
            ],
            [
                "Archived projects will be migrated in a simplified format focused on "
                "preserving project data and records. Configuration, workflows, and other "
                "advanced project settings will not be recreated."
            ],
        ]
    ),
    Heading("Archive-Preservation Only", level=3),
    BulletList(
        [
            [
                "Select Archive if the project is no longer actively used but the "
                "historical information should be retained and will ",
                Run("NOT", bold=True),
                " be able to be queried. Data will be preserved and can be unarchived if "
                "needed by the Roblox Atlassian team.",
            ],
            [
                "Archived projects will be migrated in a simplified format focused on "
                "preserving project data and records. Configuration, workflows, and other "
                "advanced project settings will not be recreated."
            ],
        ]
    ),
    Heading("Abandon", level=3),
    BulletList(
        [
            [
                "Select Abandon if the project is no longer needed and the data does not "
                "need to be migrated."
            ],
            [
                "Projects marked as Abandon will be excluded from the migration. You will "
                "lose access to this data once the migration is complete."
            ],
        ]
    ),
    Heading("Required Action", level=2),
    Paragraph(
        [
            "Review the project and record a migration decision of Active, Archive-Read "
            "Only, Archive-Preservation Only or Abandon. If additional context is needed to "
            "support the decision, include it in the task comments or designated assessment "
            "fields."
        ]
    ),
]


PERSONAL_TASK_INSTRUCTION_BLOCKS = [
    Heading("Instructions", level=1),
    Paragraph(
        [
            "A migration decision is required for this project containing personal tasks. "
            "This has been classified as a personal task tracker because it is connected to "
            "your user account, not a team. Select one of the following options:"
        ]
    ),
    Heading("Active-Personal Task Tracker", level=3),
    BulletList(
        [
            [
                "Select Active if the project is currently being used or ongoing work is "
                "taking place."
            ],
            [
                "This project will be migrated into a personal Task Tracker space that will "
                "only be visible to you."
            ],
        ]
    ),
    Heading("Abandon", level=3),
    BulletList(
        [
            [
                "Select Abandon if the project is no longer needed and the data does not "
                "need to be migrated."
            ],
            [
                "Projects marked as Abandon will be excluded from the migration. You will "
                "lose access to this data once the migration is complete."
            ],
        ]
    ),
    Heading("Required Action", level=2),
    Paragraph(
        [
            "Review the project and record a migration decision of Active or Abandon. If "
            "additional context is needed to support the decision, include it in the task "
            "comments or designated assessment fields."
        ]
    ),
]


# Plain-text renderings preserve the existing public constant names (imported by the
# CSV export rows and by tests); the ADF renderings feed the direct-API creator.
EPIC_INSTRUCTIONS = render_blocks_text(EPIC_INSTRUCTION_BLOCKS)
TASK_INSTRUCTIONS = render_blocks_text(TASK_INSTRUCTION_BLOCKS)
PERSONAL_TASK_INSTRUCTIONS = render_blocks_text(PERSONAL_TASK_INSTRUCTION_BLOCKS)
EPIC_INSTRUCTIONS_ADF = render_blocks_adf(EPIC_INSTRUCTION_BLOCKS)
TASK_INSTRUCTIONS_ADF = render_blocks_adf(TASK_INSTRUCTION_BLOCKS)
PERSONAL_TASK_INSTRUCTIONS_ADF = render_blocks_adf(PERSONAL_TASK_INSTRUCTION_BLOCKS)


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        default=resolve_default_db_path(),
        help=f"Path to the SQLite database (default: {DEFAULT_DB_PATH}).",
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help=f"Directory to store the CSV export (default: {DEFAULT_OUTPUT_DIR}/).",
    )
    parser.add_argument(
        "--filename",
        help="Optional filename for the export. Defaults to team_project_jira_import_<timestamp>.csv.",
    )
    parser.add_argument(
        "--workspace-gid",
        help="Optional Asana workspace GID used to build project URLs.",
    )
    parser.add_argument(
        "--include-empty-projects",
        action="store_true",
        help="Create a Jira Task for projects that have zero synced tasks (default: skip empty projects).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging verbosity (default: INFO).",
    )
    parser.add_argument(
        "--project-gids",
        help="Comma-separated project GIDs to export (default: all projects).",
    )
    parser.add_argument(
        "--project-gids-file",
        help="UTF-8 file containing comma- or newline-separated project GIDs.",
    )
    parser.add_argument(
        "--team-gids",
        help="Team GID to limit export to projects in that team.",
    )
    return parser.parse_args(argv)


def _bool_to_text(value: Optional[int]) -> str:
    if value is None:
        return ""
    return "true" if bool(value) else "false"


def _count_to_text(value: Optional[int]) -> str:
    if value is None:
        return ""
    return f"{value:,}"


def _clean_text(value: Optional[str]) -> str:
    return (value or "").strip()


def _sectioned_description_blocks(sections: list) -> list:
    """Render labeled sections of key/value pairs into structured ADF blocks: a
    level-3 heading per section followed by a bullet list with bold field names."""
    blocks: list = []
    for title, pairs in sections:
        blocks.append(Heading(title, level=3))
        blocks.append(
            BulletList([[Run(f"{key}:", bold=True), f" {value}"] for key, value in pairs])
        )
    return blocks


def _label_for_last_active(last_task_updated_at: Optional[str]) -> str:
    parsed = parse_iso_datetime(last_task_updated_at)
    if not parsed:
        return ""
    return f"last_active_{parsed.year}"


def _fetch_project_custom_fields(connection, project_gid: str) -> dict[str, str]:
    """Fetch all custom field values for a project, keyed by field name."""
    rows = connection.execute(
        """
        SELECT
            name,
            COALESCE(enum_value_name, CAST(number_value AS TEXT), text_value, '') as value
        FROM project_custom_fields
        WHERE project_gid = ?
        ORDER BY name
        """,
        (project_gid,),
    ).fetchall()

    # Group multi-value fields (like people fields) with semicolon delimiter
    fields: dict[str, list[str]] = {}
    for row in rows:
        field_name = _clean_text(row[0])
        field_value = _clean_text(row[1])
        if field_name:
            if field_name not in fields:
                fields[field_name] = []
            if field_value:
                fields[field_name].append(field_value)

    # Join multi-value fields with semicolon
    return {name: "; ".join(values) for name, values in fields.items()}


def _discover_all_project_custom_fields(connection) -> list[str]:
    """Discover all unique custom field names across all projects, sorted."""
    rows = connection.execute(
        "SELECT DISTINCT name FROM project_custom_fields WHERE name IS NOT NULL ORDER BY name"
    ).fetchall()
    return [row[0] for row in rows]


def _discover_project_custom_fields_for_projects(connection, project_gids: list[str]) -> list[str]:
    """Discover unique custom field names that have values in the specified projects."""
    if not project_gids:
        return []
    placeholders = ",".join(["?"] * len(project_gids))
    rows = connection.execute(
        f"""
        SELECT DISTINCT name
        FROM project_custom_fields
        WHERE project_gid IN ({placeholders})
        AND name IS NOT NULL
        ORDER BY name
        """,
        project_gids,
    ).fetchall()
    return [row[0] for row in rows]


def _build_team_description_blocks(row: dict, analysis) -> list:
    """Build the Team->Epic description as structured ADF blocks: two sections
    (Primary / Implied Details), each a level-3 heading followed by a bold-keyed
    bullet list. The majority project owner across the team is labeled
    ``majority_project_owner`` to distinguish it from a single project's owner."""
    sections = [
        (
            "Primary Details",
            [
                ("first_admin_email", analysis.first_admin_email if analysis else ""),
                ("additional_admin_emails", analysis.additional_admin_emails if analysis else ""),
                ("project_count_total", _count_to_text(row["project_count_total"])),
                ("project_count_active", _count_to_text(row["project_count_active"])),
                ("project_count_archived", _count_to_text(row["project_count_archived"])),
                ("actual_task_count_total", _count_to_text(row["actual_task_count_total"])),
            ],
        ),
        (
            "Implied Details",
            [
                ("effective_admin", analysis.effective_admin_name if analysis else ""),
                ("effective_admin_email", analysis.effective_admin_email if analysis else ""),
                ("effective_admin_source", analysis.effective_admin_source if analysis else ""),
                ("majority_project_owner", analysis.project_owner_name if analysis else ""),
                ("majority_project_owner_email", analysis.project_owner_email if analysis else ""),
                ("majority_project_owner_atlassian_status", analysis.project_owner_atlassian_status if analysis else ""),
                ("majority_project_owner_project_count", _count_to_text(analysis.project_owner_project_count if analysis else 0)),
                ("task_creator", analysis.task_creator_name if analysis else ""),
                ("task_creator_email", analysis.task_creator_email if analysis else ""),
                ("task_creator_atlassian_status", analysis.task_creator_atlassian_status if analysis else ""),
                ("task_creator_task_count", _count_to_text(analysis.task_creator_task_count if analysis else 0)),
            ],
        ),
    ]
    return _sectioned_description_blocks(sections)


def _is_active_atlassian_status(status: Optional[str]) -> bool:
    return is_active_atlassian_status(status)


class _EffectiveOwner(NamedTuple):
    """Resolved effective owner of a project. ``email`` keeps the raw users-table
    email (used in the description); ``atlassian_id``/``atlassian_status`` drive the
    Jira assignee for the Task row."""

    source: str
    name: str
    email: str
    atlassian_id: str
    atlassian_status: str


def _effective_owner_from_project(row: dict) -> _EffectiveOwner:
    """Resolve a project's effective owner, walking increasingly-broad active
    Atlassian candidates so a real person is assignable wherever one exists:

    1. the project owner when their Atlassian account is active,
    2. otherwise the project's top active task creator,
    3. otherwise a top active project member.

    The ``top_active_task_creators`` and ``top_active_project_members`` CTEs only
    surface active, mapped Atlassian users, so any fallback they provide is always
    assignable. Returns an empty owner when none applies (the project has no active,
    mapped owner/creator/member)."""
    owner_name = _row_owner_from_project(row)
    if owner_name and _is_active_atlassian_status(row["owner_atlassian_status"]):
        return _EffectiveOwner(
            "project_owner",
            owner_name,
            _clean_text(row["owner_email"]),
            _clean_text(row["owner_atlassian_id"]),
            _clean_text(row["owner_atlassian_status"]),
        )
    creator_name = _clean_text(row["top_task_creator_name"])
    if creator_name:
        return _EffectiveOwner(
            "task_creator",
            creator_name,
            _clean_text(row["top_task_creator_email"]),
            _clean_text(row["top_task_creator_atlassian_id"]),
            _clean_text(row["top_task_creator_atlassian_status"]),
        )
    member_name = _clean_text(row["top_member_name"])
    if member_name:
        return _EffectiveOwner(
            "project_member",
            member_name,
            _clean_text(row["top_member_email"]),
            _clean_text(row["top_member_atlassian_id"]),
            _clean_text(row["top_member_atlassian_status"]),
        )
    return _EffectiveOwner("", "", "", "", "")


def _build_project_description_blocks(row: dict, custom_fields: Optional[dict[str, str]] = None) -> list:
    """Build the Project->Task description as structured ADF blocks: multiple sections,
    each a level-3 heading followed by a bullet list whose item keys are bold."""
    effective = _effective_owner_from_project(row)
    sections = [
        (
            "Primary Details",
            [
                ("actual_task_count", _count_to_text(row["actual_task_count"])),
                ("owner_name", _row_owner_from_project(row)),
                ("owner_email", _clean_text(row["owner_email"])),
                ("owner_atlassian_status", _clean_text(row["owner_atlassian_status"])),
                ("project_created_at", format_report_date(row["created_at"])),
                ("project_completed", _bool_to_text(row["completed"])),
                ("project_completed_at", format_report_date(row["completed_at"])),
            ],
        ),
        (
            "Secondary Details",
            [
                ("num_incomplete_tasks", _count_to_text(row["num_incomplete_tasks"])),
                ("num_completed_tasks", _count_to_text(row["num_completed_tasks"])),
                ("last_task_created_at", format_report_date(row["last_task_created_at"])),
                ("last_task_resolved_at", format_report_date(row["last_task_resolved_at"])),
                ("last_task_updated_at", format_report_date(row["last_task_updated_at"])),
            ],
        ),
    ]

    # Add custom fields section if any exist
    if custom_fields:
        custom_field_pairs = [(name, _clean_text(value)) for name, value in custom_fields.items() if value]
        if custom_field_pairs:
            sections.append(("Project Custom Fields", custom_field_pairs))

    sections.append(
        (
            "Implied Details",
            [
                ("effective_owner_source", effective.source),
                ("effective_owner_name", effective.name),
                ("effective_owner_email", effective.email),
                ("effective_owner_atlassian_status", effective.atlassian_status),
                ("top_task_creator_name", _clean_text(row["top_task_creator_name"])),
                ("top_task_creator_email", _clean_text(row["top_task_creator_email"])),
                ("top_task_creator_atlassian_status", _clean_text(row["top_task_creator_atlassian_status"])),
                ("top_task_creator_task_count", _count_to_text(row["top_task_creator_task_count"])),
                ("top_member_name", _clean_text(row["top_member_name"])),
                ("top_member_email", _clean_text(row["top_member_email"])),
                ("top_member_atlassian_status", _clean_text(row["top_member_atlassian_status"])),
            ],
        )
    )

    return _sectioned_description_blocks(sections)


def _row_owner_from_project(row: dict) -> str:
    return _clean_text(row["owner_display_name"]) or _clean_text(row["owner_name"]) or _clean_text(row["owner_gid"])


def _row_owner_email_from_project(row: dict) -> str:
    return _clean_text(row["owner_atlassian_email"]) or _clean_text(row["owner_email"])


def build_team_project_rows(
    connection,
    workspace_gid: Optional[str] = None,
    include_empty_projects: bool = False,
    project_gids_filter: Optional[list[str]] = None,
    team_gid_filter: Optional[str] = None,
) -> tuple[list[dict], list[dict]]:
    """Build the Team→Epic and Project→Task rows shared by the CSV exporter and
    the direct Jira issue creator.

    Returns ``(epic_rows, task_rows)``. Each row carries the CSV columns plus
    internal ``_kind``/``_asana_gid`` (and, for tasks, ``_parent_kind``/
    ``_parent_asana_gid``) metadata keys used to resolve real Jira parent keys when
    creating issues via the API. The CSV writer ignores the underscore-prefixed
    keys.

    By default, projects with zero synced tasks (``actual_task_count`` is NULL)
    are skipped. Pass ``include_empty_projects=True`` to emit a Project→Task row
    for every project regardless of task count (empty projects render a task
    count of ``0``)."""
    resolved_workspace_gid = workspace_gid or infer_workspace_gid(connection)

    # Build WHERE clause for project filtering
    where_clause = ""
    query_params = []
    if project_gids_filter:
        placeholders = ",".join(["?"] * len(project_gids_filter))
        where_clause = f"WHERE p.gid IN ({placeholders})"
        query_params = project_gids_filter
    elif team_gid_filter:
        where_clause = "WHERE p.team_gid = ?"
        query_params = [team_gid_filter]

    project_rows = connection.execute(
        f"""
            WITH actual_task_counts AS (
                SELECT
                    tp.project_gid,
                    COUNT(DISTINCT t.gid) AS actual_task_count,
                    MAX(t.created_at) AS last_task_created_at,
                    MAX(t.completed_at) AS last_task_resolved_at,
                    MAX(t.modified_at) AS last_task_updated_at
                FROM task_projects tp
                JOIN tasks t
                    ON t.gid = tp.task_gid
                GROUP BY tp.project_gid
            ),
            top_active_task_creators AS (
                SELECT
                    project_gid,
                    creator_name,
                    creator_email,
                    creator_atlassian_id,
                    creator_atlassian_status,
                    task_count
                FROM (
                    SELECT
                        tp.project_gid AS project_gid,
                        COALESCE(u.name, '') AS creator_name,
                        COALESCE(u.email, '') AS creator_email,
                        COALESCE(ua.atlassian_account_id, '') AS creator_atlassian_id,
                        COALESCE(ua.atlassian_status, '') AS creator_atlassian_status,
                        COUNT(DISTINCT t.gid) AS task_count,
                        ROW_NUMBER() OVER (
                            PARTITION BY tp.project_gid
                            ORDER BY COUNT(DISTINCT t.gid) DESC,
                                COALESCE(ua.atlassian_email, u.email, u.name, t.created_by_gid) COLLATE NOCASE,
                                t.created_by_gid
                        ) AS rn
                    FROM task_projects tp
                    JOIN tasks t
                        ON t.gid = tp.task_gid
                    JOIN user_atlassian_accounts ua
                        ON ua.user_gid = t.created_by_gid
                    LEFT JOIN users u
                        ON u.gid = t.created_by_gid
                    WHERE TRIM(COALESCE(t.created_by_gid, '')) != ''
                        AND LOWER(TRIM(COALESCE(ua.atlassian_status, ''))) = 'active'
                    GROUP BY tp.project_gid, t.created_by_gid
                )
                WHERE rn = 1
            ),
            top_active_project_members AS (
                SELECT
                    project_gid,
                    member_name,
                    member_email,
                    member_atlassian_id,
                    member_atlassian_status
                FROM (
                    SELECT
                        pm.project_gid AS project_gid,
                        COALESCE(u.name, pm.user_name, '') AS member_name,
                        COALESCE(u.email, '') AS member_email,
                        COALESCE(ua.atlassian_account_id, '') AS member_atlassian_id,
                        COALESCE(ua.atlassian_status, '') AS member_atlassian_status,
                        ROW_NUMBER() OVER (
                            PARTITION BY pm.project_gid
                            ORDER BY
                                COALESCE(ua.atlassian_email, u.email, u.name, pm.user_name, pm.user_gid) COLLATE NOCASE,
                                pm.user_gid
                        ) AS rn
                    FROM project_members pm
                    JOIN user_atlassian_accounts ua
                        ON ua.user_gid = pm.user_gid
                    LEFT JOIN users u
                        ON u.gid = pm.user_gid
                    WHERE TRIM(COALESCE(pm.user_gid, '')) != ''
                        AND TRIM(COALESCE(ua.atlassian_account_id, '')) != ''
                        AND LOWER(TRIM(COALESCE(ua.atlassian_status, ''))) = 'active'
                )
                WHERE rn = 1
            )
            SELECT
                p.gid,
                p.name,
                p.archived,
                p.owner_gid,
                p.owner_name,
                p.team_gid,
                COALESCE(t.name, p.team_name, p.team_gid, '') AS team_name,
                p.privacy_setting,
                p.created_at,
                p.completed,
                p.completed_at,
                p.is_personal_project,
                p.num_tasks,
                p.num_incomplete_tasks,
                p.num_completed_tasks,
                atc.actual_task_count,
                atc.last_task_created_at,
                atc.last_task_resolved_at,
                atc.last_task_updated_at,
                u.email AS owner_email,
                u.name AS owner_display_name,
                u.user_task_list_gid AS owner_user_task_list_gid,
                ua.atlassian_account_id AS owner_atlassian_id,
                ua.atlassian_email AS owner_atlassian_email,
                ua.atlassian_status AS owner_atlassian_status,
                tatc.creator_name AS top_task_creator_name,
                tatc.creator_email AS top_task_creator_email,
                tatc.creator_atlassian_id AS top_task_creator_atlassian_id,
                tatc.creator_atlassian_status AS top_task_creator_atlassian_status,
                tatc.task_count AS top_task_creator_task_count,
                tapm.member_name AS top_member_name,
                tapm.member_email AS top_member_email,
                tapm.member_atlassian_id AS top_member_atlassian_id,
                tapm.member_atlassian_status AS top_member_atlassian_status
            FROM projects p
            LEFT JOIN teams t
                ON t.gid = p.team_gid
            LEFT JOIN actual_task_counts atc
                ON atc.project_gid = p.gid
            LEFT JOIN top_active_task_creators tatc
                ON tatc.project_gid = p.gid
            LEFT JOIN top_active_project_members tapm
                ON tapm.project_gid = p.gid
            LEFT JOIN users u
                ON u.gid = p.owner_gid
            LEFT JOIN user_atlassian_accounts ua
                ON ua.user_gid = p.owner_gid
            {where_clause}
            ORDER BY COALESCE(t.name, p.team_name, p.team_gid, '') COLLATE NOCASE,
                p.name COLLATE NOCASE,
                p.gid
            """
        , query_params
    ).fetchall()

    project_rows = [dict(row) for row in project_rows]
    team_analysis = build_team_analysis(connection)

    # Discover custom fields only for projects being exported (not all 67 fields)
    project_gids_to_export = [row["gid"] for row in project_rows]
    all_custom_field_names = _discover_project_custom_fields_for_projects(connection, project_gids_to_export)

    team_groups: dict[str, list[dict]] = defaultdict(list)
    no_team_projects: list[dict] = []
    for row in project_rows:
        team_gid = _clean_text(row["team_gid"])
        if team_gid:
            team_groups[team_gid].append(row)
        else:
            if include_empty_projects or row["actual_task_count"] is not None:
                no_team_projects.append(row)

    team_aggregates: dict[str, dict[str, object]] = {}
    for team_gid, rows in team_groups.items():
        project_count_total = len(rows)
        project_count_active = sum(1 for row in rows if not bool(row["archived"]))
        project_count_archived = sum(1 for row in rows if bool(row["archived"]))
        project_task_count_total = sum(int(row["num_tasks"] or 0) for row in rows)
        actual_task_count_values = [int(row["actual_task_count"]) for row in rows if row["actual_task_count"] is not None]
        actual_task_count_total = sum(actual_task_count_values) if actual_task_count_values else None
        team_name = next((row["team_name"] for row in rows if _clean_text(row["team_name"])), team_gid)
        team_aggregates[team_gid] = {
            "team_gid": team_gid,
            "team_name": team_name,
            "project_count_total": project_count_total,
            "project_count_active": project_count_active,
            "project_count_archived": project_count_archived,
            "project_task_count_total": project_task_count_total,
            "actual_task_count_total": actual_task_count_total,
            "rows": rows,
        }

    epic_rows: list[dict[str, str]] = []
    epic_task_ids: dict[str, str] = {}
    task_id_counter = 1

    sorted_team_ids = sorted(
        team_aggregates.keys(),
        key=lambda gid: (
            (team_aggregates[gid]["team_name"] or "").casefold(),
            gid,
        ),
    )
    for team_gid in sorted_team_ids:
        aggregate = team_aggregates[team_gid]
        analysis = team_analysis.get(team_gid)
        owner = analysis.effective_admin_name if analysis else ""

        epic_task_id = str(task_id_counter)
        task_id_counter += 1
        epic_task_ids[team_gid] = epic_task_id
        team_description_blocks = _build_team_description_blocks(
            {
                "team_gid": team_gid,
                "project_count_total": aggregate["project_count_total"],
                "project_count_active": aggregate["project_count_active"],
                "project_count_archived": aggregate["project_count_archived"],
                "project_task_count_total": aggregate["project_task_count_total"],
                "actual_task_count_total": aggregate["actual_task_count_total"],
            },
            analysis,
        )
        epic_rows.append(
            {
                "Issue Type": "Epic",
                "Task ID": epic_task_id,
                "Parent Id": "",
                "Summary": aggregate["team_name"] or "",
                "Team URL": build_team_url(team_gid),
                "Project URL": "",
                "Owner": owner,
                "Owner Email": analysis.effective_admin_email if analysis else "",
                "Owner Atlassian ID": analysis.effective_admin_atlassian_id if analysis else "",
                "Owner Atlassian Status": analysis.effective_admin_atlassian_status if analysis else "",
                "Owner Source": analysis.effective_admin_source if analysis else "",
                "Project Type": "",
                "Project Status": "",
                "Project Count (Asana)": str(aggregate["project_count_total"]),
                "Task Count (Asana)": (
                    ""
                    if aggregate["actual_task_count_total"] is None
                    else str(aggregate["actual_task_count_total"])
                ),
                "Labels": "",
                "Instructions": EPIC_INSTRUCTIONS,
                "Description": render_blocks_text(team_description_blocks),
                "_kind": "team",
                "_asana_gid": team_gid,
                "_adf_overrides": {
                    "Instructions": EPIC_INSTRUCTIONS_ADF,
                    "Description": render_blocks_adf(team_description_blocks),
                },
            }
        )

    no_team_task_id: Optional[str] = None
    if no_team_projects:
        no_team_task_id = str(task_id_counter)
        task_id_counter += 1
        # When include_empty_projects is set the bucket can contain projects with a
        # NULL actual_task_count, so coalesce to 0 before summing.
        no_team_project_count = len(no_team_projects)
        no_team_task_total = sum(int(project["actual_task_count"] or 0) for project in no_team_projects)
        epic_rows.append(
            {
                "Issue Type": "Epic",
                "Task ID": no_team_task_id,
                "Parent Id": "",
                "Summary": "No Team",
                "Team URL": "",
                "Project URL": "",
                "Owner": "",
                "Owner Email": "",
                "Owner Atlassian ID": "",
                "Owner Atlassian Status": "",
                "Owner Source": "",
                "Project Type": "",
                "Project Status": "",
                "Project Count (Asana)": str(no_team_project_count),
                "Task Count (Asana)": str(no_team_task_total),
                "Labels": "",
                "Instructions": EPIC_INSTRUCTIONS,
                "Description": "",
                "_kind": "no_team",
                "_asana_gid": "",
                "_adf_overrides": {"Instructions": EPIC_INSTRUCTIONS_ADF},
            }
        )

    task_rows: list[dict[str, str]] = []
    project_row_sort_key = {
        team_gid: index
        for index, team_gid in enumerate(sorted_team_ids)
    }
    no_team_order = len(project_row_sort_key)

    def _project_parent_sort_value(row: dict) -> int:
        team_gid = _clean_text(row["team_gid"])
        if not team_gid:
            return no_team_order
        return project_row_sort_key.get(team_gid, no_team_order)

    for row in sorted(
        [
            project_row
            for project_row in project_rows
            if include_empty_projects or project_row["actual_task_count"] is not None
        ],
        key=lambda row: (
            _project_parent_sort_value(row),
            (row["name"] or "").casefold(),
            row["gid"],
        ),
    ):
        team_gid = _clean_text(row["team_gid"])
        parent_id = epic_task_ids.get(team_gid, no_team_task_id or "")
        task_id = str(task_id_counter)
        task_id_counter += 1
        is_personal = bool(row["is_personal_project"])
        if is_personal:
            # Personal projects use a synthetic `personal_<user_gid>` project gid
            # that has no real Asana project behind it, so a project deep link
            # would 404. Point at the owner's "My Tasks" list instead, or leave
            # blank when the list gid is unknown — the dead synthetic project URL
            # must never be emitted.
            project_url = build_user_task_list_url(
                _clean_text(row["owner_user_task_list_gid"])
            )
        else:
            project_url = build_project_url(resolved_workspace_gid, row["gid"])
        instructions = PERSONAL_TASK_INSTRUCTIONS if is_personal else TASK_INSTRUCTIONS
        instructions_adf = (
            PERSONAL_TASK_INSTRUCTIONS_ADF if is_personal else TASK_INSTRUCTIONS_ADF
        )
        # Fetch project-level custom fields
        project_custom_fields = _fetch_project_custom_fields(connection, row["gid"])

        description_blocks = _build_project_description_blocks(row, project_custom_fields)
        # Mirror the Epic behavior: the Task owner/assignee is the effective owner
        # (project owner if their Atlassian account is active, else the top active
        # task creator). The raw project owner is still recorded in the description.
        effective_owner = _effective_owner_from_project(row)
        owner_email = (
            _row_owner_email_from_project(row)
            if effective_owner.source == "project_owner"
            else effective_owner.email
        )
        task_row = {
            "Issue Type": "Task",
            "Task ID": task_id,
            "Parent Id": parent_id or "",
            "Summary": row["name"] or "",
            "Team URL": build_team_url(team_gid),
            "Project URL": project_url,
            "Owner": effective_owner.name,
            "Owner Email": owner_email,
            "Owner Atlassian ID": effective_owner.atlassian_id,
            "Owner Atlassian Status": effective_owner.atlassian_status,
            "Owner Source": effective_owner.source,
            "Project Type": "personal_project" if is_personal else "team_project",
            "Project Status": "Archived" if bool(row["archived"]) else "Active",
            "Project Count (Asana)": "",
            "Task Count (Asana)": str(int(row["actual_task_count"] or 0)),
            # The project's most recent task update (MAX task modified_at),
            # formatted YYYY-MM-DD for the Jira "Last working day" date field.
            # Kept in sync with scripts.atlassian.set_project_last_updated_jira_field.
            "Last Working Day": format_report_date(row["last_task_updated_at"]),
            "Labels": _label_for_last_active(row["last_task_updated_at"]),
            "Instructions": instructions,
            "Description": render_blocks_text(description_blocks),
            "_kind": "project",
            "_asana_gid": _clean_text(row["gid"]),
            "_parent_kind": "team" if team_gid else "no_team",
            "_parent_asana_gid": team_gid,
            "_adf_overrides": {
                "Instructions": instructions_adf,
                "Description": render_blocks_adf(description_blocks),
            },
        }

        # Add custom field values as columns
        for field_name in all_custom_field_names:
            task_row[f"Project Field: {field_name}"] = project_custom_fields.get(field_name, "")

        task_rows.append(task_row)

    return epic_rows, task_rows


# Each entry pairs the CSV header with the internal row key. Several keys are emitted
# under a repeated "Labels" header because Atlassian merges repeated columns of the
# same name into multiple label values: the owner source, the last-active year, and
# the project type (personal_project / team_project).
CSV_COLUMNS = [
    ("Issue Type", "Issue Type"),
    ("Task ID", "Task ID"),
    ("Parent Id", "Parent Id"),
    ("Summary", "Summary"),
    ("Team URL", "Team URL"),
    ("Project URL", "Project URL"),
    ("Owner", "Owner"),
    ("Owner Email", "Owner Email"),
    ("Owner Atlassian ID", "Owner Atlassian ID"),
    ("Owner Atlassian Status", "Owner Atlassian Status"),
    ("Project Status", "Project Status"),
    ("Project Count (Asana)", "Project Count (Asana)"),
    ("Task Count (Asana)", "Task Count (Asana)"),
    ("Labels", "Owner Source"),
    ("Labels", "Labels"),
    ("Labels", "Project Type"),
    ("Last Working Day", "Last Working Day"),
    ("Instructions", "Instructions"),
    ("Description", "Description"),
]


def export_team_project_jira_csv(
    database_path: str,
    output_dir: Path,
    filename: Optional[str] = None,
    workspace_gid: Optional[str] = None,
    include_empty_projects: bool = False,
    project_gids: Optional[str] = None,
    project_gids_file: Optional[str] = None,
    team_gids: Optional[str] = None,
) -> Path:
    database = Database(db_path=database_path)
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    if not filename:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        filename = f"team_project_jira_import_{timestamp}.csv"

    output_path = output_dir / filename
    logger.debug("Writing Jira team/project import export to %s", output_path)

    # Parse project scope arguments
    project_gids_filter = None
    team_gid_filter = None
    if project_gids_file:
        project_gids_filter = read_project_gids_file(project_gids_file)
    elif project_gids:
        project_gids_filter = parse_gid_list(project_gids)
    elif team_gids:
        team_gid_filter = team_gids

    with database.connect() as connection:
        database.initialize(connection)
        epic_rows, task_rows = build_team_project_rows(
            connection,
            workspace_gid,
            include_empty_projects=include_empty_projects,
            project_gids_filter=project_gids_filter,
            team_gid_filter=team_gid_filter,
        )

        # Discover custom field names to build dynamic columns
        all_custom_field_names = _discover_all_project_custom_fields(connection)

    # Build dynamic CSV columns with custom fields
    csv_columns = list(CSV_COLUMNS)
    for field_name in all_custom_field_names:
        csv_column_name = f"Project Field: {field_name}"
        csv_columns.append((csv_column_name, csv_column_name))

    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow([header for header, _ in csv_columns])
        for row in epic_rows + task_rows:
            writer.writerow([row.get(key, "") for _, key in csv_columns])

    logger.info("Exported %s epic row(s) and %s task row(s) to %s.", len(epic_rows), len(task_rows), output_path)
    return output_path


def main(argv: Optional[Iterable[str]] = None) -> None:
    args = parse_args(argv)
    try:
        log_level = resolve_log_level(args.log_level)
    except ValueError as exc:  # noqa: BLE001
        raise SystemExit(str(exc)) from exc
    configure_logging(log_level)

    output_path = export_team_project_jira_csv(
        database_path=args.database,
        output_dir=Path(args.output_dir),
        filename=args.filename,
        workspace_gid=args.workspace_gid,
        include_empty_projects=args.include_empty_projects,
        project_gids=args.project_gids,
        project_gids_file=args.project_gids_file,
        team_gids=args.team_gids,
    )
    logger.info("Jira team/project import export completed: %s", output_path)


if __name__ == "__main__":
    main()
