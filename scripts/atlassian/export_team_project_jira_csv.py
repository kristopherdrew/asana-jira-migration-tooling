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
from typing import Iterable, Optional

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
from src.lib.asana_urls import build_project_url, build_team_url, infer_workspace_gid
from src.lib.database import Database
from src.lib.date_formatting import format_report_date, parse_iso_datetime
from src.lib.logging_utils import configure_logging, resolve_log_level
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
        default=DEFAULT_DB_PATH,
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
        "--log-level",
        default="INFO",
        help="Logging verbosity (default: INFO).",
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


def _description_text(lines: list[tuple[str, str]]) -> str:
    return "\n".join(f"{field}: {value}" for field, value in lines)


def _label_for_last_active(last_task_updated_at: Optional[str]) -> str:
    parsed = parse_iso_datetime(last_task_updated_at)
    if not parsed:
        return ""
    return f"last_active_{parsed.year}"


def _format_team_description(row: dict, analysis) -> str:
    return _description_text(
        [
            ("project_count_total", _count_to_text(row["project_count_total"])),
            ("project_count_active", _count_to_text(row["project_count_active"])),
            ("project_count_archived", _count_to_text(row["project_count_archived"])),
            ("actual_task_count_total", _count_to_text(row["actual_task_count_total"])),
            ("first_admin_email", analysis.first_admin_email if analysis else ""),
            ("additional_admin_emails", analysis.additional_admin_emails if analysis else ""),
            ("effective_admin", analysis.effective_admin_name if analysis else ""),
            ("effective_admin_email", analysis.effective_admin_email if analysis else ""),
            ("effective_admin_source", analysis.effective_admin_source if analysis else ""),
            ("project_owner", analysis.project_owner_name if analysis else ""),
            ("project_owner_email", analysis.project_owner_email if analysis else ""),
            ("project_owner_atlassian_status", analysis.project_owner_atlassian_status if analysis else ""),
            ("project_owner_project_count", _count_to_text(analysis.project_owner_project_count if analysis else 0)),
            ("task_creator", analysis.task_creator_name if analysis else ""),
            ("task_creator_email", analysis.task_creator_email if analysis else ""),
            ("task_creator_atlassian_status", analysis.task_creator_atlassian_status if analysis else ""),
            ("task_creator_task_count", _count_to_text(analysis.task_creator_task_count if analysis else 0)),
        ]
    )


def _is_active_atlassian_status(status: Optional[str]) -> bool:
    return _clean_text(status).casefold() == "active"


def _effective_owner_from_project(row: dict) -> tuple[str, str, str, str]:
    """Resolve a project's effective owner: the project owner when their
    Atlassian account is active, otherwise the project's top active task
    creator. Returns (source, name, email, atlassian_status)."""
    owner_name = _row_owner_from_project(row)
    if owner_name and _is_active_atlassian_status(row["owner_atlassian_status"]):
        return (
            "project_owner",
            owner_name,
            _clean_text(row["owner_email"]),
            _clean_text(row["owner_atlassian_status"]),
        )
    creator_name = _clean_text(row["top_task_creator_name"])
    if creator_name:
        return (
            "task_creator",
            creator_name,
            _clean_text(row["top_task_creator_email"]),
            _clean_text(row["top_task_creator_atlassian_status"]),
        )
    return ("", "", "", "")


def _format_project_description(row: dict, project_url: str) -> str:
    effective_owner_source, effective_owner_name, effective_owner_email, effective_owner_status = (
        _effective_owner_from_project(row)
    )
    return _description_text(
        [
            ("project_url", project_url),
            ("created_at", format_report_date(row["created_at"])),
            ("completed", _bool_to_text(row["completed"])),
            ("completed_at", format_report_date(row["completed_at"])),
            ("num_incomplete_tasks", _count_to_text(row["num_incomplete_tasks"])),
            ("num_completed_tasks", _count_to_text(row["num_completed_tasks"])),
            ("last_task_created_at", format_report_date(row["last_task_created_at"])),
            ("last_task_resolved_at", format_report_date(row["last_task_resolved_at"])),
            ("last_task_updated_at", format_report_date(row["last_task_updated_at"])),
            ("actual_task_count", _count_to_text(row["actual_task_count"])),
            ("owner_name", _row_owner_from_project(row)),
            ("owner_email", _clean_text(row["owner_email"])),
            ("owner_atlassian_status", _clean_text(row["owner_atlassian_status"])),
            ("effective_owner_source", effective_owner_source),
            ("effective_owner_name", effective_owner_name),
            ("effective_owner_email", effective_owner_email),
            ("effective_owner_atlassian_status", effective_owner_status),
            ("top_task_creator_name", _clean_text(row["top_task_creator_name"])),
            ("top_task_creator_email", _clean_text(row["top_task_creator_email"])),
            ("top_task_creator_atlassian_status", _clean_text(row["top_task_creator_atlassian_status"])),
            ("top_task_creator_task_count", _count_to_text(row["top_task_creator_task_count"])),
        ]
    )


def _row_owner_from_project(row: dict) -> str:
    return _clean_text(row["owner_display_name"]) or _clean_text(row["owner_name"]) or _clean_text(row["owner_gid"])


def _row_owner_email_from_project(row: dict) -> str:
    return _clean_text(row["owner_atlassian_email"]) or _clean_text(row["owner_email"])


def _row_owner_atlassian_id_from_project(row: dict) -> str:
    return _clean_text(row["owner_atlassian_id"])


def _row_owner_atlassian_status_from_project(row: dict) -> str:
    return _clean_text(row["owner_atlassian_status"])


def build_team_project_rows(
    connection,
    workspace_gid: Optional[str] = None,
) -> tuple[list[dict], list[dict]]:
    """Build the Team→Epic and Project→Task rows shared by the CSV exporter and
    the direct Jira issue creator.

    Returns ``(epic_rows, task_rows)``. Each row carries the CSV columns plus
    internal ``_kind``/``_asana_gid`` (and, for tasks, ``_parent_kind``/
    ``_parent_asana_gid``) metadata keys used to resolve real Jira parent keys when
    creating issues via the API. The CSV writer ignores the underscore-prefixed
    keys."""
    resolved_workspace_gid = workspace_gid or infer_workspace_gid(connection)
    project_rows = connection.execute(
        """
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
                    creator_atlassian_status,
                    task_count
                FROM (
                    SELECT
                        tp.project_gid AS project_gid,
                        COALESCE(u.name, '') AS creator_name,
                        COALESCE(u.email, '') AS creator_email,
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
                ua.atlassian_account_id AS owner_atlassian_id,
                ua.atlassian_email AS owner_atlassian_email,
                ua.atlassian_status AS owner_atlassian_status,
                tatc.creator_name AS top_task_creator_name,
                tatc.creator_email AS top_task_creator_email,
                tatc.creator_atlassian_status AS top_task_creator_atlassian_status,
                tatc.task_count AS top_task_creator_task_count
            FROM projects p
            LEFT JOIN teams t
                ON t.gid = p.team_gid
            LEFT JOIN actual_task_counts atc
                ON atc.project_gid = p.gid
            LEFT JOIN top_active_task_creators tatc
                ON tatc.project_gid = p.gid
            LEFT JOIN users u
                ON u.gid = p.owner_gid
            LEFT JOIN user_atlassian_accounts ua
                ON ua.user_gid = p.owner_gid
            ORDER BY COALESCE(t.name, p.team_name, p.team_gid, '') COLLATE NOCASE,
                p.name COLLATE NOCASE,
                p.gid
            """
    ).fetchall()

    project_rows = [dict(row) for row in project_rows]
    team_analysis = build_team_analysis(connection)

    team_groups: dict[str, list[dict]] = defaultdict(list)
    no_team_projects: list[dict] = []
    for row in project_rows:
        team_gid = _clean_text(row["team_gid"])
        if team_gid:
            team_groups[team_gid].append(row)
        else:
            if row["actual_task_count"] is not None:
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
                "Labels": "",
                "Instructions": EPIC_INSTRUCTIONS,
                "Description": _format_team_description(
                    {
                        "team_gid": team_gid,
                        "project_count_total": aggregate["project_count_total"],
                        "project_count_active": aggregate["project_count_active"],
                        "project_count_archived": aggregate["project_count_archived"],
                        "project_task_count_total": aggregate["project_task_count_total"],
                        "actual_task_count_total": aggregate["actual_task_count_total"],
                    },
                    analysis,
                ),
                "_kind": "team",
                "_asana_gid": team_gid,
                "_adf_overrides": {"Instructions": EPIC_INSTRUCTIONS_ADF},
            }
        )

    no_team_task_id: Optional[str] = None
    if no_team_projects:
        no_team_task_id = str(task_id_counter)
        task_id_counter += 1
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
        [project_row for project_row in project_rows if project_row["actual_task_count"] is not None],
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
        project_url = build_project_url(resolved_workspace_gid, row["gid"])
        is_personal = bool(row["is_personal_project"])
        instructions = PERSONAL_TASK_INSTRUCTIONS if is_personal else TASK_INSTRUCTIONS
        instructions_adf = (
            PERSONAL_TASK_INSTRUCTIONS_ADF if is_personal else TASK_INSTRUCTIONS_ADF
        )
        task_rows.append(
            {
                "Issue Type": "Task",
                "Task ID": task_id,
                "Parent Id": parent_id or "",
                "Summary": row["name"] or "",
                "Team URL": build_team_url(team_gid),
                "Project URL": project_url,
                "Owner": _row_owner_from_project(row),
                "Owner Email": _row_owner_email_from_project(row),
                "Owner Atlassian ID": _row_owner_atlassian_id_from_project(row),
                "Owner Atlassian Status": _row_owner_atlassian_status_from_project(row),
                "Owner Source": "project_owner",
                "Project Type": "personal_project" if is_personal else "team_project",
                "Project Status": "Archived" if bool(row["archived"]) else "Active",
                "Labels": _label_for_last_active(row["last_task_updated_at"]),
                "Instructions": instructions,
                "Description": _format_project_description(row, project_url),
                "_kind": "project",
                "_asana_gid": _clean_text(row["gid"]),
                "_parent_kind": "team" if team_gid else "no_team",
                "_parent_asana_gid": team_gid,
                "_adf_overrides": {"Instructions": instructions_adf},
            }
        )

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
    ("Labels", "Owner Source"),
    ("Labels", "Labels"),
    ("Labels", "Project Type"),
    ("Instructions", "Instructions"),
    ("Description", "Description"),
]


def export_team_project_jira_csv(
    database_path: str,
    output_dir: Path,
    filename: Optional[str] = None,
    workspace_gid: Optional[str] = None,
) -> Path:
    database = Database(db_path=database_path)
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    if not filename:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        filename = f"team_project_jira_import_{timestamp}.csv"

    output_path = output_dir / filename
    logger.debug("Writing Jira team/project import export to %s", output_path)

    with database.connect() as connection:
        database.initialize(connection)
        epic_rows, task_rows = build_team_project_rows(connection, workspace_gid)

    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow([header for header, _ in CSV_COLUMNS])
        for row in epic_rows + task_rows:
            writer.writerow([row.get(key, "") for _, key in CSV_COLUMNS])

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
    )
    logger.info("Jira team/project import export completed: %s", output_path)


if __name__ == "__main__":
    main()
