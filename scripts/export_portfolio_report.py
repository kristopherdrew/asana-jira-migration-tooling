#!/usr/bin/env python3
"""Export portfolio-level details and the portfolio→item mapping from SQLite to CSV.

Produces two CSVs:

* **Summary** (``portfolio_details_<ts>.csv``) — one row per portfolio with
  Atlassian-aware owner/creator resolution, item/member/custom-field rollups,
  latest status snapshot, and hierarchy placement (top-level flag, parents,
  nesting depth).
* **Items companion** (``portfolio_items_<ts>.csv``) — one row per portfolio item
  (project or nested portfolio) with the contained entity's metadata and an
  ``in_scope`` flag indicating whether it was captured in the local snapshot.

Use ``--summary-only`` to skip the companion.
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.lib.config import load_credentials
from src.lib.database import Database
from src.lib.date_formatting import format_report_date
from src.lib.asana_urls import build_portfolio_url, build_project_url, infer_workspace_gid
from src.lib.logging_utils import configure_logging, resolve_log_level


DEFAULT_DB_PATH = "data/asana.sqlite"
DEFAULT_OUTPUT_DIR = "exports"
DEFAULT_CONFIG_ROOT = "."
logger = logging.getLogger(__name__)


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        default=DEFAULT_DB_PATH,
        help="Path to the SQLite database (default: data/asana.sqlite).",
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help="Directory to store the CSV exports (default: exports/).",
    )
    parser.add_argument(
        "--filename",
        help="Optional filename for the summary export. Defaults to portfolio_details_<timestamp>.csv.",
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Write only the per-portfolio summary; skip the portfolio_items companion.",
    )
    parser.add_argument(
        "--workspace-gid",
        help="Optional Asana workspace GID used to build portfolio/project URLs.",
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


def _int_or_blank(value) -> object:
    return "" if value is None else value


def _compute_nesting_depths(connection) -> Dict[str, int]:
    """Return portfolio_gid → nesting depth (0 = top-level), cycle-safe.

    Depth is the longest chain of ancestor portfolios above a node, derived from
    ``portfolio_items`` rows where ``resource_type='portfolio'`` (parent = the
    containing portfolio, child = the nested item).
    """
    parents: Dict[str, List[str]] = {}
    all_gids = {row["gid"] for row in connection.execute("SELECT gid FROM portfolios")}
    for parent_gid, child_gid in connection.execute(
        "SELECT portfolio_gid, item_gid FROM portfolio_items WHERE resource_type = 'portfolio'"
    ):
        if child_gid in all_gids:
            parents.setdefault(child_gid, []).append(parent_gid)

    cache: Dict[str, int] = {}
    in_progress: set = set()

    def depth(node: str) -> int:
        if node in cache:
            return cache[node]
        if node in in_progress:  # cycle guard
            return 0
        in_progress.add(node)
        node_parents = parents.get(node, [])
        result = 0 if not node_parents else 1 + max(depth(p) for p in node_parents)
        in_progress.discard(node)
        cache[node] = result
        return result

    return {gid: depth(gid) for gid in all_gids}


SUMMARY_FIELDS = [
    "portfolio_gid",
    "portfolio_name",
    "portfolio_url",
    "color",
    "public",
    "owner_name",
    "owner_email",
    "owner_atlassian_status",
    "created_by_name",
    "created_by_email",
    "created_by_atlassian_status",
    "created_at",
    "start_on",
    "due_on",
    "current_status_type",
    "current_status_title",
    "status_update_count",
    "last_status_update_at",
    "num_items",
    "num_projects",
    "num_active_projects",
    "num_archived_projects",
    "num_projects_not_synced",
    "num_nested_portfolios",
    "num_members",
    "num_custom_field_settings",
    "custom_fields",
    "is_top_level",
    "nesting_depth",
    "parent_portfolio_names",
    "synced_at",
]


def export_portfolio_details(
    connection,
    output_path: Path,
    workspace_gid: Optional[str],
) -> int:
    depths = _compute_nesting_depths(connection)
    query = """
        WITH item_rollup AS (
            SELECT
                pi.portfolio_gid,
                COUNT(*) AS num_items,
                SUM(CASE WHEN pi.resource_type = 'project' THEN 1 ELSE 0 END) AS num_projects,
                SUM(CASE WHEN pi.resource_type = 'portfolio' THEN 1 ELSE 0 END) AS num_nested_portfolios,
                SUM(CASE WHEN pi.resource_type = 'project' AND pr.gid IS NOT NULL
                          AND (pr.archived IS NULL OR pr.archived = 0) THEN 1 ELSE 0 END) AS num_active_projects,
                SUM(CASE WHEN pi.resource_type = 'project' AND pr.archived = 1 THEN 1 ELSE 0 END) AS num_archived_projects,
                SUM(CASE WHEN pi.resource_type = 'project' AND pr.gid IS NULL THEN 1 ELSE 0 END) AS num_projects_not_synced
            FROM portfolio_items pi
            LEFT JOIN projects pr ON pr.gid = pi.item_gid
            GROUP BY pi.portfolio_gid
        ),
        member_rollup AS (
            SELECT portfolio_gid, COUNT(*) AS num_members
            FROM portfolio_memberships GROUP BY portfolio_gid
        ),
        cfs_rollup AS (
            SELECT portfolio_gid,
                   COUNT(*) AS num_custom_field_settings,
                   GROUP_CONCAT(custom_field_name, '; ') AS custom_fields
            FROM portfolio_custom_field_settings GROUP BY portfolio_gid
        ),
        update_rollup AS (
            SELECT portfolio_gid,
                   COUNT(*) AS status_update_count,
                   MAX(created_at) AS last_status_update_at
            FROM portfolio_updates WHERE is_deleted = 0 GROUP BY portfolio_gid
        ),
        parent_rollup AS (
            SELECT pi.item_gid AS portfolio_gid,
                   COUNT(*) AS parent_count,
                   GROUP_CONCAT(parent.name, '; ') AS parent_portfolio_names
            FROM portfolio_items pi
            JOIN portfolios parent ON parent.gid = pi.portfolio_gid
            WHERE pi.resource_type = 'portfolio'
            GROUP BY pi.item_gid
        )
        SELECT
            p.gid,
            p.name,
            p.color,
            p.public,
            p.owner_name,
            uo.email AS owner_email,
            uoa.atlassian_status AS owner_atlassian_status,
            p.created_by_name,
            ucb.email AS created_by_email,
            ucba.atlassian_status AS created_by_atlassian_status,
            p.created_at,
            p.start_on,
            p.due_on,
            p.current_status_type,
            p.current_status_title,
            p.synced_at,
            ir.num_items,
            ir.num_projects,
            ir.num_nested_portfolios,
            ir.num_active_projects,
            ir.num_archived_projects,
            ir.num_projects_not_synced,
            mr.num_members,
            cr.num_custom_field_settings,
            cr.custom_fields,
            ur.status_update_count,
            ur.last_status_update_at,
            par.parent_count,
            par.parent_portfolio_names
        FROM portfolios p
        LEFT JOIN users uo ON uo.gid = p.owner_gid
        LEFT JOIN user_atlassian_accounts uoa ON uoa.user_gid = p.owner_gid
        LEFT JOIN users ucb ON ucb.gid = p.created_by_gid
        LEFT JOIN user_atlassian_accounts ucba ON ucba.user_gid = p.created_by_gid
        LEFT JOIN item_rollup ir ON ir.portfolio_gid = p.gid
        LEFT JOIN member_rollup mr ON mr.portfolio_gid = p.gid
        LEFT JOIN cfs_rollup cr ON cr.portfolio_gid = p.gid
        LEFT JOIN update_rollup ur ON ur.portfolio_gid = p.gid
        LEFT JOIN parent_rollup par ON par.portfolio_gid = p.gid
        ORDER BY p.name COLLATE NOCASE, p.gid
    """
    rows = connection.execute(query).fetchall()

    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SUMMARY_FIELDS)
        writer.writeheader()
        for row in rows:
            parent_count = row["parent_count"] or 0
            writer.writerow(
                {
                    "portfolio_gid": row["gid"],
                    "portfolio_name": row["name"],
                    "portfolio_url": build_portfolio_url(workspace_gid, row["gid"]),
                    "color": row["color"] or "",
                    "public": _bool_to_text(row["public"]),
                    "owner_name": row["owner_name"] or "",
                    "owner_email": row["owner_email"] or "",
                    "owner_atlassian_status": row["owner_atlassian_status"] or "",
                    "created_by_name": row["created_by_name"] or "",
                    "created_by_email": row["created_by_email"] or "",
                    "created_by_atlassian_status": row["created_by_atlassian_status"] or "",
                    "created_at": format_report_date(row["created_at"]),
                    "start_on": format_report_date(row["start_on"]),
                    "due_on": format_report_date(row["due_on"]),
                    "current_status_type": row["current_status_type"] or "",
                    "current_status_title": row["current_status_title"] or "",
                    "status_update_count": _int_or_blank(row["status_update_count"]) or 0,
                    "last_status_update_at": format_report_date(row["last_status_update_at"]),
                    "num_items": _int_or_blank(row["num_items"]) or 0,
                    "num_projects": _int_or_blank(row["num_projects"]) or 0,
                    "num_active_projects": _int_or_blank(row["num_active_projects"]) or 0,
                    "num_archived_projects": _int_or_blank(row["num_archived_projects"]) or 0,
                    "num_projects_not_synced": _int_or_blank(row["num_projects_not_synced"]) or 0,
                    "num_nested_portfolios": _int_or_blank(row["num_nested_portfolios"]) or 0,
                    "num_members": _int_or_blank(row["num_members"]) or 0,
                    "num_custom_field_settings": _int_or_blank(row["num_custom_field_settings"]) or 0,
                    "custom_fields": row["custom_fields"] or "",
                    "is_top_level": _bool_to_text(parent_count == 0),
                    "nesting_depth": depths.get(row["gid"], 0),
                    "parent_portfolio_names": row["parent_portfolio_names"] or "",
                    "synced_at": format_report_date(row["synced_at"]),
                }
            )
    logger.info("Exported %s portfolio(s) to %s.", len(rows), output_path)
    return len(rows)


ITEM_FIELDS = [
    "portfolio_gid",
    "portfolio_name",
    "portfolio_url",
    "position",
    "item_type",
    "item_gid",
    "item_name",
    "item_url",
    "project_team_name",
    "project_archived",
    "project_num_tasks",
    "in_scope",
]


def export_portfolio_items(
    connection,
    output_path: Path,
    workspace_gid: Optional[str],
) -> int:
    query = """
        SELECT
            pi.portfolio_gid,
            p.name AS portfolio_name,
            pi.position,
            pi.resource_type,
            pi.item_gid,
            COALESCE(pi.item_name, pr.name, np.name) AS item_name,
            pr.archived AS project_archived,
            pr.num_tasks AS project_num_tasks,
            COALESCE(tm.name, pr.team_name) AS project_team_name,
            CASE
                WHEN pi.resource_type = 'project' THEN (CASE WHEN pr.gid IS NULL THEN 0 ELSE 1 END)
                WHEN pi.resource_type = 'portfolio' THEN (CASE WHEN np.gid IS NULL THEN 0 ELSE 1 END)
                ELSE NULL
            END AS in_scope
        FROM portfolio_items pi
        JOIN portfolios p ON p.gid = pi.portfolio_gid
        LEFT JOIN projects pr ON pr.gid = pi.item_gid AND pi.resource_type = 'project'
        LEFT JOIN teams tm ON tm.gid = pr.team_gid
        LEFT JOIN portfolios np ON np.gid = pi.item_gid AND pi.resource_type = 'portfolio'
        ORDER BY p.name COLLATE NOCASE, p.gid, pi.position
    """
    rows = connection.execute(query).fetchall()

    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=ITEM_FIELDS)
        writer.writeheader()
        for row in rows:
            resource_type = row["resource_type"]
            if resource_type == "portfolio":
                item_url = build_portfolio_url(workspace_gid, row["item_gid"])
            else:
                item_url = build_project_url(workspace_gid, row["item_gid"])
            writer.writerow(
                {
                    "portfolio_gid": row["portfolio_gid"],
                    "portfolio_name": row["portfolio_name"],
                    "portfolio_url": build_portfolio_url(workspace_gid, row["portfolio_gid"]),
                    "position": _int_or_blank(row["position"]),
                    "item_type": resource_type or "",
                    "item_gid": row["item_gid"],
                    "item_name": row["item_name"] or "",
                    "item_url": item_url,
                    "project_team_name": row["project_team_name"] or "",
                    "project_archived": _bool_to_text(row["project_archived"])
                    if resource_type == "project"
                    else "",
                    "project_num_tasks": _int_or_blank(row["project_num_tasks"])
                    if resource_type == "project"
                    else "",
                    "in_scope": _bool_to_text(row["in_scope"]),
                }
            )
    logger.info("Exported %s portfolio item(s) to %s.", len(rows), output_path)
    return len(rows)


def export_portfolio_report(
    database_path: str,
    output_dir: Path,
    filename: Optional[str] = None,
    summary_only: bool = False,
    workspace_gid: Optional[str] = None,
) -> Tuple[Path, Optional[Path]]:
    database = Database(db_path=database_path)
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    summary_name = filename or f"portfolio_details_{timestamp}.csv"
    summary_path = output_dir / summary_name

    items_path: Optional[Path] = None
    if not summary_only:
        stem = summary_path.stem
        items_path = output_dir / f"{stem}_items{summary_path.suffix}"

    with database.connect() as connection:
        database.initialize(connection)
        resolved_workspace_gid = workspace_gid or infer_workspace_gid(connection)
        count = export_portfolio_details(connection, summary_path, resolved_workspace_gid)
        if count == 0:
            logger.info("No portfolios found in %s; generated an empty export.", database_path)
        if items_path is not None:
            export_portfolio_items(connection, items_path, resolved_workspace_gid)

    return summary_path, items_path


def main(argv: Optional[Iterable[str]] = None) -> None:
    args = parse_args(argv)
    try:
        log_level = resolve_log_level(args.log_level)
    except ValueError as exc:  # noqa: BLE001
        raise SystemExit(str(exc)) from exc
    configure_logging(log_level)

    summary_path, items_path = export_portfolio_report(
        database_path=args.database,
        output_dir=Path(args.output_dir),
        filename=args.filename,
        summary_only=args.summary_only,
        workspace_gid=args.workspace_gid or load_credentials(DEFAULT_CONFIG_ROOT).workspace_gid,
    )
    logger.info("Portfolio summary export completed: %s", summary_path)
    if items_path is not None:
        logger.info("Portfolio items export completed: %s", items_path)


if __name__ == "__main__":
    main()
