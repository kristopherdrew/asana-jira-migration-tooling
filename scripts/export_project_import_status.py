#!/usr/bin/env python3
"""Export project import status as a reconciliation workbook: Projects, Multi-Site, Unmigrated."""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.lib.database import Database, resolve_default_db_path
from src.lib.logging_utils import configure_logging, resolve_log_level
from src.lib.xlsx_writer import write_workbook

DEFAULT_DB_PATH = "data/asana.sqlite"
DEFAULT_OUTPUT_DIR = "exports"
MULTI_SITE_FLAG = "Yes - see Multi-Site tab"
logger = logging.getLogger(__name__)


@dataclass
class SiteMapping:
    project_keys: set[str] = field(default_factory=set)
    jira_keys: set[str] = field(default_factory=set)
    mapped_task_gids: set[str] = field(default_factory=set)


@dataclass
class ProjectRecord:
    gid: str
    name: str
    team: str
    archived: bool
    task_gids: set[str] = field(default_factory=set)
    atjm_ticket: str = ""
    decision: str = ""
    site_mappings: dict[str, SiteMapping] = field(default_factory=dict)

    @property
    def task_count(self) -> int:
        return len(self.task_gids)


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
        help="Optional filename for the workbook. Defaults to project_import_status_<timestamp>.xlsx.",
    )
    parser.add_argument(
        "--jira-site",
        action="append",
        dest="jira_sites",
        help="Jira site key to include (can be repeated for multi-select). Default: all sites.",
    )
    parser.add_argument(
        "--project-gids",
        help="Comma-separated project GIDs to scope the report.",
    )
    parser.add_argument(
        "--project-gids-file",
        help="File containing comma- or newline-separated project GIDs.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging verbosity (default: INFO).",
    )
    return parser.parse_args(argv)


def _load_project_gids(gids_arg: Optional[str], gids_file: Optional[str]) -> Optional[list[str]]:
    """Load project GIDs from argument and/or file. Returns None if not scoped."""
    gids = []
    if gids_arg:
        gids.extend([g.strip() for g in gids_arg.split(",") if g.strip()])
    if gids_file:
        with Path(gids_file).open("r", encoding="utf-8") as f:
            content = f.read()
            gids.extend([g.strip() for g in content.replace(",", "\n").split("\n") if g.strip()])
    return gids if gids else None


def _get_all_jira_sites(connection: sqlite3.Connection) -> list[str]:
    """Get all distinct jira_site_key values from mapping tables."""
    query = """
    SELECT DISTINCT jira_site_key FROM (
        SELECT DISTINCT jira_site_key FROM jira_project_mappings
        UNION
        SELECT DISTINCT jira_site_key FROM task_jira_mappings
    )
    ORDER BY jira_site_key
    """
    return [row[0] for row in connection.execute(query).fetchall()]


def _placeholders(values: list) -> str:
    return ",".join("?" * len(values))


def _project_key(jira_key: str) -> str:
    return jira_key.split("-", 1)[0]


def _load_projects(connection: sqlite3.Connection, project_gids: Optional[list[str]]) -> dict[str, ProjectRecord]:
    rows = connection.execute(
        """
        SELECT p.gid, p.name, COALESCE(t.name, p.team_name, ''), COALESCE(p.archived, 0)
        FROM projects p
        LEFT JOIN teams t ON t.gid = p.team_gid
        """
    ).fetchall()
    scope = set(project_gids) if project_gids else None
    return {
        gid: ProjectRecord(gid=gid, name=name or "", team=team or "", archived=bool(archived))
        for gid, name, team, archived in rows
        if scope is None or gid in scope
    }


def _load_project_tasks(connection: sqlite3.Connection, projects: dict[str, ProjectRecord]) -> None:
    rows = connection.execute(
        """
        SELECT tp.project_gid, tp.task_gid
        FROM task_projects tp
        JOIN tasks t ON t.gid = tp.task_gid
        WHERE t.is_deleted = 0
        """
    ).fetchall()
    for project_gid, task_gid in rows:
        if project_gid in projects:
            projects[project_gid].task_gids.add(task_gid)


def _count_unsynced_project_refs(connection: sqlite3.Connection) -> int:
    row = connection.execute(
        """
        SELECT COUNT(DISTINCT tp.project_gid)
        FROM task_projects tp
        JOIN tasks t ON t.gid = tp.task_gid AND t.is_deleted = 0
        WHERE tp.project_gid NOT IN (SELECT gid FROM projects)
        """
    ).fetchone()
    return row[0]


def _load_site_mappings(
    connection: sqlite3.Connection, projects: dict[str, ProjectRecord], sites: list[str]
) -> None:
    rows = connection.execute(
        f"""
        SELECT DISTINCT tjm.jira_site_key, tp.project_gid, tjm.task_gid, tjm.jira_key
        FROM task_jira_mappings tjm
        JOIN tasks t ON t.gid = tjm.task_gid AND t.is_deleted = 0
        JOIN task_projects tp ON tp.task_gid = t.gid
        WHERE tjm.jira_site_key IN ({_placeholders(sites)})
        """,
        sites,
    ).fetchall()
    orphan_gids: set[str] = set()
    for site, project_gid, task_gid, jira_key in rows:
        project = projects.get(project_gid)
        if project is None:
            orphan_gids.add(project_gid)
            continue
        mapping = project.site_mappings.setdefault(site, SiteMapping())
        mapping.mapped_task_gids.add(task_gid)
        mapping.jira_keys.add(jira_key)
        mapping.project_keys.add(_project_key(jira_key))
    if orphan_gids:
        logger.warning(
            "%d mapped project GID(s) have no row in the projects table and are omitted: %s",
            len(orphan_gids),
            ", ".join(sorted(orphan_gids)),
        )


def _load_atjm_tickets(connection: sqlite3.Connection, projects: dict[str, ProjectRecord], sites: list[str]) -> None:
    rows = connection.execute(
        f"""
        SELECT asana_gid, jira_key FROM team_project_jira_mappings
        WHERE kind = 'project' AND jira_site_key IN ({_placeholders(sites)})
        """,
        sites,
    ).fetchall()
    tickets: dict[str, set[str]] = defaultdict(set)
    for gid, jira_key in rows:
        if gid in projects:
            tickets[gid].add(jira_key)
    for gid, keys in tickets.items():
        projects[gid].atjm_ticket = ", ".join(sorted(keys))


def _load_decisions(connection: sqlite3.Connection, projects: dict[str, ProjectRecord], sites: list[str]) -> None:
    rows = connection.execute(
        f"""
        SELECT project_gid, decision FROM project_jira_decisions
        WHERE jira_site_key IN ({_placeholders(sites)}) AND TRIM(COALESCE(decision, '')) <> ''
        """,
        sites,
    ).fetchall()
    decisions: dict[str, set[str]] = defaultdict(set)
    for gid, decision in rows:
        if gid in projects:
            decisions[gid].add(decision.strip())
    for gid, values in decisions.items():
        projects[gid].decision = ", ".join(sorted(values))


def _percent(mapped: int, total: int) -> str:
    return f"{100.0 * mapped / total:.1f}%" if total > 0 else ""


def _sort_key(project: ProjectRecord) -> tuple[str, str, str]:
    return (project.team.lower(), project.name.lower(), project.gid)


def _archived_label(project: ProjectRecord) -> str:
    return "Yes" if project.archived else "No"


def _migrated_label(project: ProjectRecord) -> str:
    return "Yes" if project.site_mappings else "No"


def _build_projects_tab(projects: list[ProjectRecord], sites: list[str]) -> list[list]:
    header = [
        "Project GID",
        "Project Name",
        "Team",
        "Archived",
        "Migrated",
        "ATJM Ticket",
        "ATJM Migration Decision",
        "Task Count",
    ]
    for site in sites:
        header.extend([f"{site} Project Key", f"{site} Mapped", f"{site} %"])
    header.append("Multi-Site")

    rows = [header]
    for project in projects:
        row = [
            project.gid,
            project.name,
            project.team,
            _archived_label(project),
            _migrated_label(project),
            project.atjm_ticket,
            project.decision,
            project.task_count,
        ]
        for site in sites:
            mapping = project.site_mappings.get(site)
            mapped = len(mapping.mapped_task_gids) if mapping else 0
            keys = ", ".join(sorted(mapping.project_keys)) if mapping else ""
            row.extend([keys, mapped, _percent(mapped, project.task_count)])
        row.append(MULTI_SITE_FLAG if len(project.site_mappings) >= 2 else "")
        rows.append(row)
    return rows


def _build_multi_site_tab(projects: list[ProjectRecord], sites: list[str]) -> list[list]:
    rows = [
        ["Project GID", "Project Name", "Team", "Archived", "Jira Site", "Project Key(s)", "Mapped", "Task Count", "Mapped %"]
    ]
    for project in projects:
        if len(project.site_mappings) < 2:
            continue
        for site in sites:
            mapping = project.site_mappings.get(site)
            if mapping is None:
                continue
            mapped = len(mapping.mapped_task_gids)
            rows.append(
                [
                    project.gid,
                    project.name,
                    project.team,
                    _archived_label(project),
                    site,
                    ", ".join(sorted(mapping.project_keys)),
                    mapped,
                    project.task_count,
                    _percent(mapped, project.task_count),
                ]
            )
    return rows


def _build_unmigrated_tab(projects: list[ProjectRecord]) -> list[list]:
    rows = [["Project GID", "Project Name", "Archived", "ATJM Ticket", "ATJM Migration Decision", "Task Count"]]
    for project in projects:
        if project.site_mappings:
            continue
        rows.append(
            [
                project.gid,
                project.name,
                _archived_label(project),
                project.atjm_ticket,
                project.decision,
                project.task_count,
            ]
        )
    return rows


def _build_site_migrated_tab(projects: list[ProjectRecord], sites: list[str], site: str) -> list[list]:
    migrated = [project for project in projects if site in project.site_mappings]
    return _build_projects_tab(migrated, [site])


def _build_summary_tab(projects: list[ProjectRecord], sites: list[str], unsynced_refs: int) -> list[list]:
    header = [
        "Jira Site",
        "Asana Projects Migrated",
        "Tasks Migrated",
        "Keys Mapped",
        "Asana Tasks in Migrated Projects",
        "% of Asana Tasks Migrated",
        "Projects with Multiple Keys",
        "Distinct Project Keys",
    ]
    rows = [header]
    decision_counts: dict[str, dict[str, int]] = {}

    for site in sites:
        migrated = [project for project in projects if site in project.site_mappings]
        migrated_tasks: set[str] = set()
        keyed_tasks: set[str] = set()
        jira_keys: set[str] = set()
        project_keys: set[str] = set()
        multi_key_projects = 0
        for project in migrated:
            mapping = project.site_mappings[site]
            migrated_tasks.update(project.task_gids)
            keyed_tasks.update(mapping.mapped_task_gids)
            jira_keys.update(mapping.jira_keys)
            project_keys.update(mapping.project_keys)
            if len(mapping.project_keys) > 1:
                multi_key_projects += 1
            decision = project.decision or "(no decision)"
            decision_counts.setdefault(decision, {}).setdefault(site, 0)
            decision_counts[decision][site] += 1

        rows.append(
            [
                site,
                len(migrated),
                len(keyed_tasks),
                len(jira_keys),
                len(migrated_tasks),
                _percent(len(keyed_tasks), len(migrated_tasks)),
                multi_key_projects,
                len(project_keys),
            ]
        )

    multi_site_count = sum(1 for project in projects if len(project.site_mappings) >= 2)
    rows.append([])
    rows.append(["Projects mapped to 2+ selected sites", multi_site_count])
    rows.append(["Unsynced project GIDs referenced by tasks (workspace-wide)", unsynced_refs])

    rows.append([])
    rows.append(["ATJM Migration Decision (migrated projects)", *sites])
    for decision in sorted(decision_counts):
        rows.append([decision, *[decision_counts[decision].get(site, 0) for site in sites]])
    return rows


def export_project_import_status_workbook(
    database_path: str,
    output_dir: Path,
    filename: Optional[str] = None,
    jira_sites: Optional[list[str]] = None,
    project_gids: Optional[list[str]] = None,
) -> Path:
    """Generate the per-project reconciliation workbook for the selected Jira sites."""
    database = Database(db_path=database_path)
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    if not filename:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        filename = f"project_import_status_{timestamp}.xlsx"

    output_path = output_dir / filename
    logger.debug("Generating project import status workbook: %s", output_path)

    with database.connect() as connection:
        database.initialize(connection)

        if jira_sites:
            sites = list(dict.fromkeys(jira_sites))
        else:
            sites = _get_all_jira_sites(connection) or ["legacy_unknown"]

        logger.info("Generating workbook for sites: %s", ", ".join(sites))

        projects = _load_projects(connection, project_gids)
        _load_project_tasks(connection, projects)
        _load_site_mappings(connection, projects, sites)
        _load_atjm_tickets(connection, projects, sites)
        _load_decisions(connection, projects, sites)
        unsynced_refs = _count_unsynced_project_refs(connection)

        ordered = sorted(projects.values(), key=_sort_key)
        multi_site = [p for p in ordered if len(p.site_mappings) >= 2]
        sheets = [
            ("Summary", _build_summary_tab(ordered, sites, unsynced_refs)),
            ("Projects", _build_projects_tab(ordered, sites)),
            *[(f"{site} Migrated", _build_site_migrated_tab(ordered, sites, site)) for site in sites],
            ("Multi-Site", _build_multi_site_tab(multi_site, sites)),
            ("Unmigrated", _build_unmigrated_tab(ordered)),
        ]

        write_workbook(output_path, sheets)

    logger.info("Project import status workbook completed: %s", output_path)
    return output_path


def main(argv: Optional[Iterable[str]] = None) -> None:
    args = parse_args(argv)
    try:
        log_level = resolve_log_level(args.log_level)
    except ValueError as exc:  # noqa: BLE001
        raise SystemExit(str(exc)) from exc
    configure_logging(log_level)

    project_gids = _load_project_gids(args.project_gids, args.project_gids_file)

    output_path = export_project_import_status_workbook(
        database_path=args.database,
        output_dir=Path(args.output_dir),
        filename=args.filename,
        jira_sites=args.jira_sites,
        project_gids=project_gids,
    )
    logger.info("Export completed: %s", output_path)


if __name__ == "__main__":
    main()
