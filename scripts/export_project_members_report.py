#!/usr/bin/env python3
"""Export a Project Membership workbook (.xlsx).

Lists project membership captured from Asana (the ``members`` array on each
project, synced by :mod:`scripts.get_projects`). Produces a single standalone
multi-tab ``.xlsx`` (no CSV intermediate), written with the dependency-free
:mod:`src.lib.xlsx_writer`:

* **Overview** — generated-at, source DB, scope applied, and a Tab→Rows index.
* **Project Members** — one row per membership record (one project + one member),
  with the project's team / owner-agnostic membership, an Atlassian-aware member
  column, and the project URL.
* **Member Summary** — one row per member: how many projects they belong to and a
  semicolon-separated list of those project names.

Scope with ``--team-gid`` to focus on one team's projects; omit it for the whole
workspace.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.lib.config import load_credentials
from src.lib.database import Database, resolve_default_db_path
from src.lib.asana_urls import build_project_url, infer_workspace_gid
from src.lib.logging_utils import configure_logging, resolve_log_level
from src.lib.xlsx_writer import write_workbook


DEFAULT_DB_PATH = "data/asana.sqlite"
DEFAULT_OUTPUT_DIR = "exports"
DEFAULT_CONFIG_ROOT = "."
logger = logging.getLogger(__name__)


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        default=resolve_default_db_path(),
        help="Path to the SQLite database (default: data/asana.sqlite).",
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help="Directory to store the workbook (default: exports/).",
    )
    parser.add_argument(
        "--filename",
        help="Optional workbook filename. Defaults to project_members_<timestamp>.xlsx.",
    )
    parser.add_argument(
        "--team-gid",
        help="Optional team GID to limit the report to one team's projects.",
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


def _scope_clause(team_gid: Optional[str], alias: str = "p") -> Tuple[str, List[str]]:
    """Return a `` AND ...`` SQL fragment + params to scope on projects ``alias``."""
    if team_gid:
        return f" AND {alias}.team_gid = ?", [team_gid]
    return "", []


def _membership_rows(connection, scope: str, params: List[str]) -> List[dict]:
    """One dict per (project, member), ordered team → project → member."""
    query = f"""
        SELECT
            p.gid AS project_gid,
            p.name AS project_name,
            p.archived,
            COALESCE(t.name, p.team_name) AS team_name,
            pm.user_gid,
            COALESCE(u.name, pm.user_name) AS member_name,
            u.email AS member_email,
            uoa.atlassian_status AS member_atlassian_status
        FROM project_members pm
        JOIN projects p ON p.gid = pm.project_gid
        LEFT JOIN teams t ON t.gid = p.team_gid
        LEFT JOIN users u ON u.gid = pm.user_gid
        LEFT JOIN user_atlassian_accounts uoa ON uoa.user_gid = pm.user_gid
        WHERE 1 = 1{scope}
        ORDER BY team_name COLLATE NOCASE, p.name COLLATE NOCASE,
            member_name COLLATE NOCASE
    """
    return [dict(row) for row in connection.execute(query, params).fetchall()]


def _project_members_sheet(
    membership_rows: List[dict], workspace_gid: Optional[str]
) -> Tuple[str, List[list]]:
    header = [
        "team_name",
        "project_name",
        "project_url",
        "archived",
        "member_name",
        "member_email",
        "member_atlassian_status",
        "project_gid",
        "user_gid",
    ]
    rows: List[list] = [header]
    for row in membership_rows:
        rows.append(
            [
                row["team_name"] or "",
                row["project_name"] or "",
                build_project_url(workspace_gid, row["project_gid"]),
                _bool_to_text(row["archived"]),
                row["member_name"] or "",
                row["member_email"] or "",
                row["member_atlassian_status"] or "",
                row["project_gid"],
                row["user_gid"],
            ]
        )
    return "Project Members", rows


def _member_summary_sheet(membership_rows: List[dict]) -> Tuple[str, List[list]]:
    header = [
        "member_name",
        "member_email",
        "member_atlassian_status",
        "project_count",
        "project_names",
        "user_gid",
    ]
    members: Dict[str, dict] = {}
    for row in membership_rows:
        gid = row["user_gid"]
        entry = members.get(gid)
        if entry is None:
            entry = {
                "user_gid": gid,
                "member_name": row["member_name"] or "",
                "member_email": row["member_email"] or "",
                "member_atlassian_status": row["member_atlassian_status"] or "",
                "projects": [],
            }
            members[gid] = entry
        if row["project_name"]:
            entry["projects"].append(row["project_name"])

    ordered = sorted(
        members.values(),
        key=lambda e: (-len(e["projects"]), e["member_name"].casefold()),
    )
    rows: List[list] = [header]
    for entry in ordered:
        project_names = sorted(set(entry["projects"]), key=str.casefold)
        rows.append(
            [
                entry["member_name"],
                entry["member_email"],
                entry["member_atlassian_status"],
                len(project_names),
                "; ".join(project_names),
                entry["user_gid"],
            ]
        )
    return "Member Summary", rows


def build_member_sheets(
    connection,
    workspace_gid: Optional[str] = None,
    team_gid: Optional[str] = None,
) -> List[Tuple[str, List[list]]]:
    """Build the data tabs (each a ``(title, rows)`` with a header row first)."""
    scope, params = _scope_clause(team_gid)
    membership_rows = _membership_rows(connection, scope, params)
    return [
        _project_members_sheet(membership_rows, workspace_gid),
        _member_summary_sheet(membership_rows),
    ]


def _build_overview(
    data_sheets: List[Tuple[str, List[list]]],
    database: str,
    team_gid: Optional[str],
    generated_at: str,
) -> Tuple[str, List[list]]:
    scope_label = f"team_gid={team_gid}" if team_gid else "whole workspace"
    overview: List[list] = [
        ["Project membership report", None],
        ["Generated (UTC)", generated_at],
        ["Source database", database],
        ["Scope", scope_label],
        [None, None],
        ["Tab", "Rows"],
    ]
    for title, rows in data_sheets:
        overview.append([title, max(len(rows) - 1, 0)])
    return "Overview", overview


def export_project_members_report(
    database_path: str,
    output_dir: Path,
    filename: Optional[str] = None,
    team_gid: Optional[str] = None,
    workspace_gid: Optional[str] = None,
) -> Path:
    database = Database(db_path=database_path)
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_path = output_dir / (filename or f"project_members_{timestamp}.xlsx")

    with database.connect() as connection:
        database.initialize(connection)
        resolved_workspace_gid = workspace_gid or infer_workspace_gid(connection)
        data_sheets = build_member_sheets(connection, resolved_workspace_gid, team_gid)

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    overview = _build_overview(data_sheets, database_path, team_gid, generated_at)
    write_workbook(output_path, [overview] + data_sheets)

    total_rows = sum(max(len(rows) - 1, 0) for _, rows in data_sheets)
    logger.info(
        "Wrote %s tab(s) (%s data rows) to %s.",
        len(data_sheets) + 1,
        total_rows,
        output_path,
    )
    return output_path


def main(argv: Optional[Iterable[str]] = None) -> None:
    args = parse_args(argv)
    try:
        log_level = resolve_log_level(args.log_level)
    except ValueError as exc:  # noqa: BLE001
        raise SystemExit(str(exc)) from exc
    configure_logging(log_level)

    workspace_gid = args.workspace_gid
    if workspace_gid is None:
        try:
            workspace_gid = load_credentials(DEFAULT_CONFIG_ROOT).workspace_gid
        except Exception:  # pragma: no cover - credentials optional for URL building
            workspace_gid = None

    output_path = export_project_members_report(
        database_path=args.database,
        output_dir=Path(args.output_dir),
        filename=args.filename,
        team_gid=args.team_gid,
        workspace_gid=workspace_gid,
    )
    logger.info("Project membership report completed: %s", output_path)


if __name__ == "__main__":
    main()
