#!/usr/bin/env python3
"""Export a Sections + Custom Fields migration-review workbook (.xlsx).

Built for the migration-scoping conversations with Team Admins and Project
Owners: it lays out, per project, how each project actually uses **Sections**
and **Custom Fields** (down to individual enum options and task counts), plus
two cross-project "catalog" tabs that surface differently-named-but-same-purpose
fields and sections side-by-side so the team can decide a sensible Jira model.

"Status" is intentionally not a separate dimension — in Asana it is an outcome
of how Sections and (enum) Custom Fields are used, which this report exposes.

Produces a single standalone multi-tab ``.xlsx`` (no CSV intermediate), written
with the dependency-free :mod:`src.lib.xlsx_writer`:

* **Overview** — generated-at, source DB, scope applied, and a Tab→Rows index.
* **Projects** — one row per in-scope project (team, Atlassian-aware owner +
  effective team admin, task/section/custom-field rollups, archived flag, and
  the project's migrated Jira Task key).
* **Project Members** — one row per (project, member) membership record with an
  Atlassian-aware member column (from the ``project_members`` table).
* **Member Summary** — one row per member: project count + a ``;``-joined list
  of the projects they belong to.
* **Sections** — one row per (project, section) with task and completed counts.
* **Custom Fields** — one row per (project, field) with usage % and distinct
  values used.
* **Custom Field Values** — value-level breakdown: one row per (project, field,
  enum option) with task counts for ``enum`` / ``multi_enum`` fields. Non-enum
  fields (text/number/date) get one summary row (distinct-value count + sample
  values) since their values are not a fixed option set.
* **Field Catalog** — one row per distinct custom field across the scope:
  option set, how many projects use it, and which ones.
* **Section Catalog** — one row per distinct section name (case-insensitive)
  across the scope: how many projects/teams use it and total tasks.

Scope with ``--team-gid`` and/or ``--owner-gid`` to focus on one admin's set of
projects, or ``--jira-keys`` / ``--jira-keys-file`` to pick out just the projects
behind a list of migrated Jira Task keys; omit all for the whole workspace. The workbook is named
after the scope — ``<Team> Migration Review.xlsx`` (or the owner name, else
``Selected Projects Migration Review.xlsx`` for a Jira-key scope, else
``Workspace Migration Review.xlsx``) unless ``--filename`` overrides it.
"""

from __future__ import annotations

import argparse
import logging
import re
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
from src.lib.project_scope import parse_gid_list, read_project_gids_file
from src.lib.team_analysis import build_team_analysis
from src.lib.xlsx_writer import write_workbook
from scripts.export_project_members_report import (
    _member_summary_sheet,
    _membership_rows,
    _project_members_sheet,
)


DEFAULT_DB_PATH = "data/asana.sqlite"
DEFAULT_OUTPUT_DIR = "exports"
DEFAULT_CONFIG_ROOT = "."
NON_ENUM_SAMPLE_LIMIT = 5
logger = logging.getLogger(__name__)

# A custom field value is considered "populated" when any value column is set.
_POPULATED_SINGLE_PREDICATE = (
    "COALESCE(tcf.enum_option_gid, '') != '' "
    "OR COALESCE(tcf.text_value, '') != '' "
    "OR tcf.number_value IS NOT NULL "
    "OR COALESCE(tcf.date_value, '') != '' "
    "OR COALESCE(tcf.display_value, '') != ''"
)


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
        help="Optional workbook filename. Defaults to field_section_review_<timestamp>.xlsx.",
    )
    parser.add_argument(
        "--team-gid",
        help="Optional team GID to limit the report to one team's projects.",
    )
    parser.add_argument(
        "--owner-gid",
        help="Optional project-owner user GID to limit the report to one owner's projects.",
    )
    parser.add_argument("--project-gids", action="append", default=None,
                        help="Comma-separated project GIDs to include (may be repeated).")
    parser.add_argument("--project-gids-file",
                        help="UTF-8 file containing comma- or newline-separated project GIDs.")
    parser.add_argument(
        "--jira-keys",
        action="append",
        default=None,
        help=(
            "Optional list of migrated Jira Task keys (comma/space separated, or "
            "repeat the flag) to limit the report to just the projects those keys "
            "map to."
        ),
    )
    parser.add_argument(
        "--jira-keys-file",
        help="Optional UTF-8 file containing comma-, space-, or newline-separated Jira Task keys.",
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


def _normalize_jira_keys(raw: Optional[Iterable[str]]) -> List[str]:
    """Flatten comma/space-separated, possibly-repeated key inputs into a list.

    Keys are stripped, uppercased (Jira keys are always uppercase), de-duplicated
    while preserving order, and blanks dropped.
    """
    if not raw:
        return []
    keys: List[str] = []
    seen = set()
    for chunk in raw:
        for token in re.split(r"[,\s]+", chunk or ""):
            key = token.strip().upper()
            if key and key not in seen:
                seen.add(key)
                keys.append(key)
    return keys


def _read_jira_keys_file(path_value: str) -> str:
    path = Path(path_value).expanduser()
    if not path.is_file():
        raise SystemExit(f"Jira keys file not found: {path_value}")
    try:
        contents = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SystemExit(f"Could not read Jira keys file {path_value}: {exc}") from exc
    if not _normalize_jira_keys([contents]):
        raise SystemExit(f"No valid Jira keys found in file: {path_value}")
    return contents


def _resolve_project_gids_from_jira_keys(
    connection, keys: List[str]
) -> Tuple[List[str], List[str]]:
    """Map migrated Jira Task keys to project GIDs via team_project_jira_mappings.

    Returns ``(matched_project_gids, unmatched_keys)``. Matching is
    case-insensitive; keys with no ``kind='project'`` mapping are reported back.
    """
    if not keys:
        return [], []
    placeholders = ", ".join("?" for _ in keys)
    query = (
        "SELECT asana_gid AS project_gid, UPPER(jira_key) AS jira_key "
        "FROM team_project_jira_mappings "
        f"WHERE kind = 'project' AND UPPER(jira_key) IN ({placeholders})"
    )
    found: Dict[str, str] = {}
    for row in connection.execute(query, keys).fetchall():
        found[row["jira_key"]] = row["project_gid"]
    matched = [found[key] for key in keys if key in found]
    unmatched = [key for key in keys if key not in found]
    return matched, unmatched


def _scope_clause(
    team_gid: Optional[str],
    owner_gid: Optional[str],
    project_gids: Optional[List[str]] = None,
    alias: str = "p",
) -> Tuple[str, List[str]]:
    """Return a `` AND ...`` SQL fragment + params to scope on projects ``alias``."""
    clauses: List[str] = []
    params: List[str] = []
    if team_gid:
        clauses.append(f"{alias}.team_gid = ?")
        params.append(team_gid)
    if owner_gid:
        clauses.append(f"{alias}.owner_gid = ?")
        params.append(owner_gid)
    if project_gids is not None:
        # An explicit (possibly empty) list scopes to exactly those projects; an
        # empty list means "no project matched" and must match nothing rather
        # than falling through to the whole workspace.
        if project_gids:
            placeholders = ", ".join("?" for _ in project_gids)
            clauses.append(f"{alias}.gid IN ({placeholders})")
            params.extend(project_gids)
        else:
            clauses.append("1 = 0")
    fragment = ("" if not clauses else " AND " + " AND ".join(clauses))
    return fragment, params


def _resolve_scope_name(
    connection,
    team_gid: Optional[str],
    owner_gid: Optional[str],
    jira_keys: Optional[List[str]] = None,
) -> str:
    """Human name for the scope: team, else owner, else selected projects, else Workspace."""
    if team_gid:
        row = connection.execute(
            "SELECT name FROM teams WHERE gid = ?", (team_gid,)
        ).fetchone()
        if row and row["name"]:
            return row["name"]
        return f"Team {team_gid}"
    if owner_gid:
        row = connection.execute(
            "SELECT name FROM users WHERE gid = ?", (owner_gid,)
        ).fetchone()
        if row and row["name"]:
            return row["name"]
        return f"Owner {owner_gid}"
    if jira_keys:
        return "Selected Projects"
    return "Workspace"


def _safe_filename(name: str) -> str:
    """Strip characters that are unsafe in a filename, collapsing whitespace."""
    cleaned = re.sub(r'[\\/:*?"<>|]+', " ", name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or "Migration Review"


def _projects_sheet(
    connection, workspace_gid: Optional[str], scope: str, params: List[str]
) -> Tuple[str, List[list]]:
    team_analysis = build_team_analysis(connection)
    query = f"""
        WITH section_counts AS (
            SELECT project_gid, COUNT(DISTINCT section_gid) AS section_count
            FROM task_projects
            WHERE COALESCE(section_gid, '') != ''
            GROUP BY project_gid
        ),
        task_counts AS (
            SELECT project_gid, COUNT(DISTINCT task_gid) AS task_count
            FROM task_projects
            GROUP BY project_gid
        ),
        field_counts AS (
            SELECT project_gid, COUNT(DISTINCT custom_field_gid) AS field_count FROM (
                SELECT tp.project_gid, tcf.custom_field_gid
                FROM task_projects tp
                JOIN task_custom_fields tcf ON tcf.task_gid = tp.task_gid
                WHERE {_POPULATED_SINGLE_PREDICATE}
                UNION
                SELECT tp.project_gid, tcfm.custom_field_gid
                FROM task_projects tp
                JOIN task_custom_field_multi_enum tcfm ON tcfm.task_gid = tp.task_gid
            )
            GROUP BY project_gid
        )
        SELECT
            p.gid AS project_gid,
            p.name AS project_name,
            p.archived,
            p.team_gid,
            COALESCE(t.name, p.team_name) AS team_name,
            p.owner_name,
            uo.email AS owner_email,
            uoa.atlassian_status AS owner_atlassian_status,
            pjd.decision AS jira_migration_decision,
            tpm.jira_key AS jira_key,
            COALESCE(tc.task_count, 0) AS task_count,
            COALESCE(sc.section_count, 0) AS section_count,
            COALESCE(fc.field_count, 0) AS field_count
        FROM projects p
        LEFT JOIN teams t ON t.gid = p.team_gid
        LEFT JOIN users uo ON uo.gid = p.owner_gid
        LEFT JOIN user_atlassian_accounts uoa ON uoa.user_gid = p.owner_gid
        LEFT JOIN project_jira_decisions pjd ON pjd.project_gid = p.gid
        LEFT JOIN team_project_jira_mappings tpm
            ON tpm.asana_gid = p.gid AND tpm.kind = 'project'
        LEFT JOIN section_counts sc ON sc.project_gid = p.gid
        LEFT JOIN task_counts tc ON tc.project_gid = p.gid
        LEFT JOIN field_counts fc ON fc.project_gid = p.gid
        WHERE 1 = 1{scope}
        ORDER BY team_name COLLATE NOCASE, p.name COLLATE NOCASE
    """
    header = [
        "team_name",
        "project_name",
        "project_url",
        "jira_migration_decision",
        "jira_key",
        "archived",
        "owner_name",
        "owner_email",
        "owner_atlassian_status",
        "team_admin",
        "team_admin_email",
        "team_admin_atlassian_status",
        "team_admin_source",
        "task_count",
        "section_count",
        "custom_field_count",
        "project_gid",
    ]
    rows: List[list] = [header]
    for row in connection.execute(query, params).fetchall():
        analysis = team_analysis.get(row["team_gid"]) if row["team_gid"] else None
        rows.append(
            [
                row["team_name"] or "",
                row["project_name"] or "",
                build_project_url(workspace_gid, row["project_gid"]),
                row["jira_migration_decision"] or "",
                row["jira_key"] or "",
                _bool_to_text(row["archived"]),
                row["owner_name"] or "",
                row["owner_email"] or "",
                row["owner_atlassian_status"] or "",
                analysis.effective_admin if analysis else "",
                analysis.effective_admin_email if analysis else "",
                analysis.effective_admin_atlassian_status if analysis else "",
                analysis.effective_admin_source if analysis else "",
                row["task_count"],
                row["section_count"],
                row["field_count"],
                row["project_gid"],
            ]
        )
    return "Projects", rows


def _sections_sheet(
    connection, workspace_gid: Optional[str], scope: str, params: List[str]
) -> Tuple[str, List[list]]:
    query = f"""
        SELECT
            p.gid AS project_gid,
            p.name AS project_name,
            COALESCE(t.name, p.team_name) AS team_name,
            tp.section_gid,
            tp.section_name,
            COUNT(DISTINCT tp.task_gid) AS task_count,
            COUNT(DISTINCT CASE WHEN tk.completed = 1 THEN tp.task_gid END) AS completed_task_count
        FROM task_projects tp
        JOIN projects p ON p.gid = tp.project_gid
        LEFT JOIN teams t ON t.gid = p.team_gid
        LEFT JOIN tasks tk ON tk.gid = tp.task_gid
        WHERE COALESCE(tp.section_gid, '') != ''{scope}
        GROUP BY p.gid, tp.section_gid
        ORDER BY team_name COLLATE NOCASE, p.name COLLATE NOCASE,
            task_count DESC, tp.section_name COLLATE NOCASE
    """
    header = [
        "team_name",
        "project_name",
        "project_url",
        "section_name",
        "task_count",
        "completed_task_count",
        "project_gid",
        "section_gid",
    ]
    rows: List[list] = [header]
    for row in connection.execute(query, params).fetchall():
        rows.append(
            [
                row["team_name"] or "",
                row["project_name"] or "",
                build_project_url(workspace_gid, row["project_gid"]),
                row["section_name"] or "",
                row["task_count"],
                row["completed_task_count"],
                row["project_gid"],
                row["section_gid"],
            ]
        )
    return "Sections", rows


def _custom_fields_sheet(
    connection, workspace_gid: Optional[str], scope: str, params: List[str]
) -> Tuple[str, List[list]]:
    query = f"""
        WITH populated AS (
            SELECT tp.project_gid, tcf.custom_field_gid, tcf.task_gid,
                   tcf.enum_option_gid AS value_key, tcf.display_value AS value_text
            FROM task_projects tp
            JOIN task_custom_fields tcf ON tcf.task_gid = tp.task_gid
            WHERE {_POPULATED_SINGLE_PREDICATE}
            UNION ALL
            SELECT tp.project_gid, tcfm.custom_field_gid, tcfm.task_gid,
                   tcfm.enum_option_gid AS value_key, NULL AS value_text
            FROM task_projects tp
            JOIN task_custom_field_multi_enum tcfm ON tcfm.task_gid = tp.task_gid
        ),
        project_task_counts AS (
            SELECT project_gid, COUNT(DISTINCT task_gid) AS project_task_count
            FROM task_projects
            GROUP BY project_gid
        )
        SELECT
            p.gid AS project_gid,
            p.name AS project_name,
            COALESCE(t.name, p.team_name) AS team_name,
            cf.gid AS custom_field_gid,
            cf.name AS custom_field_name,
            cf.resource_subtype AS field_type,
            cf.is_global_to_workspace,
            pcfs.is_important,
            COUNT(DISTINCT pop.task_gid) AS tasks_with_values,
            COUNT(DISTINCT COALESCE(pop.value_key, pop.value_text)) AS distinct_values,
            ptc.project_task_count
        FROM populated pop
        JOIN projects p ON p.gid = pop.project_gid
        JOIN custom_fields cf ON cf.gid = pop.custom_field_gid
        LEFT JOIN teams t ON t.gid = p.team_gid
        LEFT JOIN project_custom_field_settings pcfs
            ON pcfs.project_gid = p.gid AND pcfs.custom_field_gid = cf.gid
        LEFT JOIN project_task_counts ptc ON ptc.project_gid = p.gid
        WHERE 1 = 1{scope}
        GROUP BY p.gid, cf.gid
        ORDER BY team_name COLLATE NOCASE, p.name COLLATE NOCASE,
            tasks_with_values DESC, cf.name COLLATE NOCASE
    """
    header = [
        "team_name",
        "project_name",
        "project_url",
        "custom_field_name",
        "field_type",
        "is_important",
        "is_global_to_workspace",
        "tasks_with_values",
        "project_task_count",
        "usage_pct",
        "distinct_values",
        "custom_field_gid",
    ]
    rows: List[list] = [header]
    for row in connection.execute(query, params).fetchall():
        project_task_count = row["project_task_count"]
        if project_task_count:
            usage_pct: object = round(row["tasks_with_values"] / project_task_count * 100, 1)
        else:
            usage_pct = ""
        rows.append(
            [
                row["team_name"] or "",
                row["project_name"] or "",
                build_project_url(workspace_gid, row["project_gid"]),
                row["custom_field_name"] or "",
                row["field_type"] or "",
                _bool_to_text(row["is_important"]),
                _bool_to_text(row["is_global_to_workspace"]),
                row["tasks_with_values"],
                "" if project_task_count is None else project_task_count,
                usage_pct,
                row["distinct_values"],
                row["custom_field_gid"],
            ]
        )
    return "Custom Fields", rows


def _custom_field_values_sheet(
    connection, workspace_gid: Optional[str], scope: str, params: List[str]
) -> Tuple[str, List[list]]:
    # Enum / multi_enum: one row per option actually selected on a task.
    enum_query = f"""
        WITH opts AS (
            SELECT tp.project_gid, tcf.custom_field_gid,
                   tcf.enum_option_gid AS option_gid, tcf.task_gid
            FROM task_projects tp
            JOIN task_custom_fields tcf ON tcf.task_gid = tp.task_gid
            WHERE COALESCE(tcf.enum_option_gid, '') != ''
            UNION ALL
            SELECT tp.project_gid, tcfm.custom_field_gid,
                   tcfm.enum_option_gid AS option_gid, tcfm.task_gid
            FROM task_projects tp
            JOIN task_custom_field_multi_enum tcfm ON tcfm.task_gid = tp.task_gid
        )
        SELECT
            p.gid AS project_gid,
            p.name AS project_name,
            COALESCE(t.name, p.team_name) AS team_name,
            cf.name AS custom_field_name,
            cf.resource_subtype AS field_type,
            o.custom_field_gid,
            COALESCE(eo.name, '(unknown option)') AS option_name,
            COUNT(DISTINCT o.task_gid) AS task_count
        FROM opts o
        JOIN projects p ON p.gid = o.project_gid
        JOIN custom_fields cf ON cf.gid = o.custom_field_gid
        LEFT JOIN teams t ON t.gid = p.team_gid
        LEFT JOIN custom_field_enum_options eo
            ON eo.custom_field_gid = o.custom_field_gid
            AND eo.enum_option_gid = o.option_gid
        WHERE 1 = 1{scope}
        GROUP BY p.gid, o.custom_field_gid, o.option_gid
    """

    # Non-enum fields: one row per distinct display value, aggregated in Python
    # into a single summary row per (project, field).
    non_enum_query = f"""
        SELECT
            p.gid AS project_gid,
            p.name AS project_name,
            COALESCE(t.name, p.team_name) AS team_name,
            cf.gid AS custom_field_gid,
            cf.name AS custom_field_name,
            cf.resource_subtype AS field_type,
            tcf.display_value,
            COUNT(DISTINCT tcf.task_gid) AS task_count
        FROM task_projects tp
        JOIN task_custom_fields tcf ON tcf.task_gid = tp.task_gid
        JOIN projects p ON p.gid = tp.project_gid
        JOIN custom_fields cf ON cf.gid = tcf.custom_field_gid
        LEFT JOIN teams t ON t.gid = p.team_gid
        WHERE COALESCE(cf.resource_subtype, '') NOT IN ('enum', 'multi_enum')
            AND COALESCE(tcf.display_value, '') != ''
            {scope}
        GROUP BY p.gid, cf.gid, tcf.display_value
    """

    header = [
        "team_name",
        "project_name",
        "project_url",
        "custom_field_name",
        "field_type",
        "option_or_value",
        "task_count",
        "sample_values",
        "custom_field_gid",
    ]
    entries: List[dict] = []

    for row in connection.execute(enum_query, params).fetchall():
        entries.append(
            {
                "team_name": row["team_name"] or "",
                "project_name": row["project_name"] or "",
                "project_gid": row["project_gid"],
                "custom_field_name": row["custom_field_name"] or "",
                "field_type": row["field_type"] or "",
                "option_or_value": row["option_name"],
                "task_count": row["task_count"],
                "sample_values": "",
            }
        )

    # Fold non-enum value rows into one summary entry per (project, field).
    summaries: Dict[Tuple[str, str], dict] = {}
    for row in connection.execute(non_enum_query, params).fetchall():
        key = (row["project_gid"], row["custom_field_gid"])
        summary = summaries.get(key)
        if summary is None:
            summary = {
                "team_name": row["team_name"] or "",
                "project_name": row["project_name"] or "",
                "project_gid": row["project_gid"],
                "custom_field_name": row["custom_field_name"] or "",
                "field_type": row["field_type"] or "",
                "task_count": 0,
                "values": [],  # (display_value, task_count)
            }
            summaries[key] = summary
        summary["task_count"] += row["task_count"]
        summary["values"].append((row["display_value"], row["task_count"]))

    for summary in summaries.values():
        distinct = len(summary["values"])
        top = sorted(summary["values"], key=lambda item: (-item[1], item[0]))[:NON_ENUM_SAMPLE_LIMIT]
        sample = "; ".join(value for value, _ in top)
        entries.append(
            {
                "team_name": summary["team_name"],
                "project_name": summary["project_name"],
                "project_gid": summary["project_gid"],
                "custom_field_name": summary["custom_field_name"],
                "field_type": summary["field_type"],
                "option_or_value": f"({distinct} distinct value{'s' if distinct != 1 else ''})",
                "task_count": summary["task_count"],
                "sample_values": sample,
            }
        )

    entries.sort(
        key=lambda e: (
            e["team_name"].casefold(),
            e["project_name"].casefold(),
            e["custom_field_name"].casefold(),
            -e["task_count"],
            str(e["option_or_value"]).casefold(),
        )
    )

    rows: List[list] = [header]
    for entry in entries:
        rows.append(
            [
                entry["team_name"],
                entry["project_name"],
                build_project_url(workspace_gid, entry["project_gid"]),
                entry["custom_field_name"],
                entry["field_type"],
                entry["option_or_value"],
                entry["task_count"],
                entry["sample_values"],
                entry["custom_field_gid"] if "custom_field_gid" in entry else "",
            ]
        )
    return "Custom Field Values", rows


def _enum_option_sets(connection) -> Dict[str, str]:
    """Return custom_field_gid → "; "-joined enum option names (in position order)."""
    options: Dict[str, List[str]] = {}
    for row in connection.execute(
        """
        SELECT custom_field_gid, name
        FROM custom_field_enum_options
        ORDER BY custom_field_gid, position, name COLLATE NOCASE
        """
    ).fetchall():
        options.setdefault(row["custom_field_gid"], []).append(row["name"] or "")
    return {gid: "; ".join(name for name in names if name) for gid, names in options.items()}


def _field_catalog_sheet(
    connection, scope: str, params: List[str]
) -> Tuple[str, List[list]]:
    option_sets = _enum_option_sets(connection)
    # One flat row per (field, project) the field is actually populated in; the
    # project list / count is aggregated in Python to keep a clean separator.
    query = f"""
        WITH field_projects AS (
            SELECT DISTINCT tp.project_gid, tcf.custom_field_gid
            FROM task_projects tp
            JOIN task_custom_fields tcf ON tcf.task_gid = tp.task_gid
            WHERE {_POPULATED_SINGLE_PREDICATE}
            UNION
            SELECT DISTINCT tp.project_gid, tcfm.custom_field_gid
            FROM task_projects tp
            JOIN task_custom_field_multi_enum tcfm ON tcfm.task_gid = tp.task_gid
        )
        SELECT
            cf.gid AS custom_field_gid,
            cf.name AS custom_field_name,
            cf.resource_subtype AS field_type,
            cf.is_global_to_workspace,
            p.name AS project_name
        FROM field_projects fp
        JOIN projects p ON p.gid = fp.project_gid
        JOIN custom_fields cf ON cf.gid = fp.custom_field_gid
        WHERE 1 = 1{scope}
        ORDER BY cf.name COLLATE NOCASE, p.name COLLATE NOCASE
    """
    catalog: Dict[str, dict] = {}
    for row in connection.execute(query, params).fetchall():
        gid = row["custom_field_gid"]
        entry = catalog.get(gid)
        if entry is None:
            entry = {
                "custom_field_name": row["custom_field_name"] or "",
                "field_type": row["field_type"] or "",
                "is_global_to_workspace": row["is_global_to_workspace"],
                "projects": [],
            }
            catalog[gid] = entry
        entry["projects"].append(row["project_name"] or "")

    header = [
        "custom_field_name",
        "field_type",
        "is_global_to_workspace",
        "project_count",
        "distinct_options",
        "option_set",
        "project_names",
        "custom_field_gid",
    ]
    rows: List[list] = [header]
    ordered = sorted(
        catalog.items(),
        key=lambda item: (-len(item[1]["projects"]), item[1]["custom_field_name"].casefold()),
    )
    for gid, entry in ordered:
        option_set = option_sets.get(gid, "")
        distinct_options = len([o for o in option_set.split("; ") if o]) if option_set else 0
        project_names = sorted(set(entry["projects"]), key=str.casefold)
        rows.append(
            [
                entry["custom_field_name"],
                entry["field_type"],
                _bool_to_text(entry["is_global_to_workspace"]),
                len(set(entry["projects"])),
                distinct_options,
                option_set,
                "; ".join(project_names),
                gid,
            ]
        )
    return "Field Catalog", rows


def _section_catalog_sheet(
    connection, scope: str, params: List[str]
) -> Tuple[str, List[list]]:
    query = f"""
        SELECT
            tp.section_name,
            tp.project_gid,
            p.name AS project_name,
            COALESCE(t.name, p.team_name) AS team_name,
            COUNT(DISTINCT tp.task_gid) AS task_count
        FROM task_projects tp
        JOIN projects p ON p.gid = tp.project_gid
        LEFT JOIN teams t ON t.gid = p.team_gid
        WHERE COALESCE(tp.section_gid, '') != ''{scope}
        GROUP BY LOWER(TRIM(tp.section_name)), tp.project_gid
        ORDER BY tp.section_name COLLATE NOCASE
    """
    catalog: Dict[str, dict] = {}
    for row in connection.execute(query, params).fetchall():
        name = row["section_name"] or ""
        key = name.strip().casefold()
        entry = catalog.get(key)
        if entry is None:
            entry = {
                "section_name": name,
                "projects": set(),
                "teams": set(),
                "task_count": 0,
            }
            catalog[key] = entry
        entry["projects"].add(row["project_name"] or "")
        if row["team_name"]:
            entry["teams"].add(row["team_name"])
        entry["task_count"] += row["task_count"]

    header = [
        "section_name",
        "project_count",
        "task_count",
        "team_names",
        "project_names",
    ]
    rows: List[list] = [header]
    ordered = sorted(
        catalog.values(),
        key=lambda e: (-len(e["projects"]), -e["task_count"], e["section_name"].casefold()),
    )
    for entry in ordered:
        rows.append(
            [
                entry["section_name"],
                len(entry["projects"]),
                entry["task_count"],
                "; ".join(sorted(entry["teams"], key=str.casefold)),
                "; ".join(sorted(entry["projects"], key=str.casefold)),
            ]
        )
    return "Section Catalog", rows


def build_review_sheets(
    connection,
    workspace_gid: Optional[str] = None,
    team_gid: Optional[str] = None,
    owner_gid: Optional[str] = None,
    project_gids: Optional[List[str]] = None,
) -> List[Tuple[str, List[list]]]:
    """Build the data tabs (each a ``(title, rows)`` with a header row first)."""
    scope, params = _scope_clause(team_gid, owner_gid, project_gids)
    membership_rows = _membership_rows(connection, scope, params)
    return [
        _projects_sheet(connection, workspace_gid, scope, params),
        _project_members_sheet(membership_rows, workspace_gid),
        _member_summary_sheet(membership_rows),
        _sections_sheet(connection, workspace_gid, scope, params),
        _custom_fields_sheet(connection, workspace_gid, scope, params),
        _custom_field_values_sheet(connection, workspace_gid, scope, params),
        _field_catalog_sheet(connection, scope, params),
        _section_catalog_sheet(connection, scope, params),
    ]


def _build_overview(
    data_sheets: List[Tuple[str, List[list]]],
    report_title: str,
    database: str,
    team_gid: Optional[str],
    owner_gid: Optional[str],
    generated_at: str,
    jira_keys: Optional[List[str]] = None,
) -> Tuple[str, List[list]]:
    scope_label = ", ".join(
        part
        for part in (
            f"team_gid={team_gid}" if team_gid else "",
            f"owner_gid={owner_gid}" if owner_gid else "",
            f"jira_keys={len(jira_keys)}" if jira_keys else "",
        )
        if part
    ) or "whole workspace"
    overview: List[list] = [
        [report_title, None],
        ["Generated (UTC)", generated_at],
        ["Source database", database],
        ["Scope", scope_label],
        [None, None],
        ["Tab", "Rows"],
    ]
    for title, rows in data_sheets:
        overview.append([title, max(len(rows) - 1, 0)])
    return "Overview", overview


def export_field_section_review_report(
    database_path: str,
    output_dir: Path,
    filename: Optional[str] = None,
    team_gid: Optional[str] = None,
    owner_gid: Optional[str] = None,
    explicit_project_gids: Optional[List[str]] = None,
    jira_keys: Optional[List[str]] = None,
    workspace_gid: Optional[str] = None,
) -> Path:
    database = Database(db_path=database_path)
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    jira_keys = _normalize_jira_keys(jira_keys)

    with database.connect() as connection:
        database.initialize(connection)
        resolved_workspace_gid = workspace_gid or infer_workspace_gid(connection)
        project_gids = explicit_project_gids
        if jira_keys:
            jira_project_gids, unmatched = _resolve_project_gids_from_jira_keys(
                connection, jira_keys
            )
            if unmatched:
                logger.warning(
                    "No project-level Jira mapping for %s key(s): %s",
                    len(unmatched),
                    ", ".join(unmatched),
                )
            if not jira_project_gids:
                logger.warning(
                    "None of the supplied Jira keys mapped to a project; "
                    "the report will be empty."
                )
            project_gids = jira_project_gids
        report_title = (
            f"{_resolve_scope_name(connection, team_gid, owner_gid, jira_keys)}"
            " Migration Review"
        )
        data_sheets = build_review_sheets(
            connection, resolved_workspace_gid, team_gid, owner_gid, project_gids
        )

    output_path = output_dir / (filename or f"{_safe_filename(report_title)}.xlsx")
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    overview = _build_overview(
        data_sheets,
        report_title,
        database_path,
        team_gid,
        owner_gid,
        generated_at,
        jira_keys,
    )
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
    jira_keys = list(args.jira_keys or [])
    if args.jira_keys_file:
        jira_keys.append(_read_jira_keys_file(args.jira_keys_file))
    raw_project_gids = list(args.project_gids or [])
    if args.project_gids_file:
        raw_project_gids.append(read_project_gids_file(args.project_gids_file))
    try:
        log_level = resolve_log_level(args.log_level)
    except ValueError as exc:  # noqa: BLE001
        raise SystemExit(str(exc)) from exc
    configure_logging(log_level)

    output_path = export_field_section_review_report(
        database_path=args.database,
        output_dir=Path(args.output_dir),
        filename=args.filename,
        team_gid=args.team_gid,
        owner_gid=args.owner_gid,
        explicit_project_gids=(
            parse_gid_list(",".join(raw_project_gids)) if raw_project_gids else None
        ),
        jira_keys=jira_keys,
        workspace_gid=args.workspace_gid or load_credentials(DEFAULT_CONFIG_ROOT).workspace_gid,
    )
    logger.info("Field & section review report completed: %s", output_path)


if __name__ == "__main__":
    main()
