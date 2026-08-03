#!/usr/bin/env python3
"""Audit how completely foreign-migrated Asana projects landed in Jira (name-based).

Read-only diagnostic. A batch of Asana projects was migrated into Jira *before* our
involvement, using someone else's tooling — so none of our mapping tables were populated
for them. This reconciles the local Asana snapshot against the live Jira projects by
**matching task name ↔ issue summary** (no reliable Asana reference survives on the Jira
side), and reports, per migration group, what matched, what is missing in Jira, and what
is extra in Jira. ``missing_in_jira`` is broken down by top-level-vs-subtask and
completed-vs-open so the "subtasks were dropped" hypothesis can be measured.

Writes nothing to Jira, Asana, or the database. ``--prod`` targets the production Jira
site (the migrations under review live in production).
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.atlassian.search_jira_external_ids import (
    JiraSearchClient,
    ensure_atlassian_credentials,
)
from src.lib.config import load_credentials, resolve_jira_base_url
from src.lib.database import Database, resolve_default_db_path
from src.lib.logging_utils import configure_logging, resolve_log_level
from src.lib.xlsx_writer import write_workbook


logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_DIR = "exports"
PAGE_SIZE = 100  # /search/jql page size; results are paged via nextPageToken.

# Jira project key -> Asana source projects consolidated into it, identified by GID (an
# all-digit string) or by name. The POPS group is a flat union of 7 projects; PROJ24 is a
# single project. Overridable via --group.
MIGRATION_GROUPS: Dict[str, List[str]] = {
    "POPS": [
        "1212075157088449",  # H2 2025 RoGRO
        "1211809738083139",  # Core: Zendesk -> Jira Transition
        "1212330808660536",  # Data Quality Initiatives [Operate Pillar]
        "1211854289153866",  # Employer of Record (EOR) Evaluation
        "1212918799762179",  # 2026 Process Improvements/Automations + New Work
        "1212914372949689",  # Ongoing New Country Startup Work
        "1212395726672922",  # Performance Managment Documentation Project
    ],
    "PROJ24": ["1205436878144436"],  # Project 24
}

MISSING_COLUMNS = ["group", "source_project", "task_name", "task_type", "completed",
                   "created_at", "modified_at", "parent_task", "assignee_email",
                   "creator_email", "asana_gid"]
EXTRA_COLUMNS = ["group", "jira_key", "summary", "issue_type", "is_subtask", "status"]

_WHITESPACE = re.compile(r"\s+")


def normalize_name(value: Optional[str]) -> str:
    """Return a match key for a task name / issue summary.

    Case-folded, trimmed, and internal whitespace collapsed to a single space, so
    cosmetic differences (leading/trailing spaces, double spaces, casing) do not cause
    false mismatches. Kept deliberately conservative — no punctuation stripping — since
    the migration copied summaries verbatim.
    """
    return _WHITESPACE.sub(" ", (value or "").strip()).casefold()


def reconcile_group(
    asana_tasks: Sequence[dict],
    jira_issues: Sequence[dict],
) -> Tuple[List[Tuple[dict, dict]], List[dict], List[dict]]:
    """Multiset-match Asana tasks to Jira issues on the normalized name.

    Returns ``(matched, missing_in_jira, extra_in_jira)``. For each normalized name,
    ``min(asana_count, jira_count)`` records are paired as matched; the Asana remainder is
    the coverage gap (missing) and the Jira remainder has no Asana twin (extra). Pure —
    no network or DB access — so it is unit-testable.
    """
    asana_by_name: Dict[str, List[dict]] = defaultdict(list)
    for task in asana_tasks:
        asana_by_name[normalize_name(task.get("name"))].append(task)
    jira_by_name: Dict[str, List[dict]] = defaultdict(list)
    for issue in jira_issues:
        jira_by_name[normalize_name(issue.get("summary"))].append(issue)

    matched: List[Tuple[dict, dict]] = []
    missing: List[dict] = []
    extra: List[dict] = []
    for name in set(asana_by_name) | set(jira_by_name):
        a = asana_by_name.get(name, [])
        j = jira_by_name.get(name, [])
        pairs = min(len(a), len(j))
        matched.extend(zip(a[:pairs], j[:pairs]))
        missing.extend(a[pairs:])
        extra.extend(j[pairs:])
    return matched, missing, extra


def summarize_missing(missing: Sequence[dict]) -> Dict[str, int]:
    """Break the missing-in-Jira set down by hierarchy and completion."""
    return {
        "top_level": sum(1 for t in missing if not t.get("is_subtask")),
        "subtask": sum(1 for t in missing if t.get("is_subtask")),
        "completed": sum(1 for t in missing if t.get("completed")),
        "open": sum(1 for t in missing if not t.get("completed")),
    }


def _resolve_projects(connection, identifier: str) -> List[dict]:
    """Return matching ``projects`` rows for an identifier (GID if all-digit, else name)."""
    if identifier.isdigit():
        return connection.execute(
            "SELECT gid, name FROM projects WHERE gid = ?", (identifier,)
        ).fetchall()
    return connection.execute(
        "SELECT gid, name FROM projects WHERE TRIM(name) = ?", (identifier,)
    ).fetchall()


def build_asana_group(
    connection,
    group_key: str,
    project_identifiers: Sequence[str],
) -> Dict[str, object]:
    """Resolve a group's Asana source projects and pull their tasks.

    Each identifier is either an Asana project **GID** (an all-digit string) or a project
    **name** (matched trimmed). Returns ``{"per_project": [...], "tasks": [...],
    "warnings": [...]}``. ``per_project`` holds independent per-project counts; ``tasks`` is
    the group union deduped by task GID (each unique task once) for reconciliation. An
    identifier resolving to 0 (or, for a name, >1) projects produces a warning. The DB's
    actual project name is used as the ``source_project`` label regardless of how it was
    identified.
    """
    per_project: List[dict] = []
    tasks: Dict[str, dict] = {}
    warnings: List[str] = []
    for raw_identifier in project_identifiers:
        identifier = raw_identifier.strip()
        projects = _resolve_projects(connection, identifier)
        if not projects:
            warnings.append(f"[{group_key}] no Asana project matched {identifier!r}")
            continue
        if len(projects) > 1:
            gids = ", ".join(p["gid"] for p in projects)
            warnings.append(f"[{group_key}] {len(projects)} projects matched {identifier!r} (gids: {gids})")
        for project in projects:
            project_gid = project["gid"]
            name = project["name"] or identifier
            rows = connection.execute(
                "SELECT t.gid AS gid, t.name AS name, "
                "COALESCE(t.hierarchy_depth, 0) AS depth, "
                "COALESCE(t.completed, 0) AS completed, "
                "t.created_at AS created_at, t.modified_at AS modified_at, "
                "t.parent_gid AS parent_gid, p.name AS parent_name, "
                "au.email AS assignee_email, cu.email AS creator_email "
                "FROM task_projects tp JOIN tasks t ON t.gid = tp.task_gid "
                "LEFT JOIN tasks p ON p.gid = t.parent_gid "
                "LEFT JOIN users au ON au.gid = t.assignee_gid "
                "LEFT JOIN users cu ON cu.gid = t.created_by_gid "
                "WHERE tp.project_gid = ?",
                (project_gid,),
            ).fetchall()
            stats = {"project": name, "gid": project_gid, "total": 0, "top_level": 0,
                     "subtask": 0, "completed": 0, "open": 0, "names": Counter()}
            for row in rows:
                is_subtask = (row["depth"] or 0) >= 1
                completed = bool(row["completed"])
                stats["total"] += 1
                stats["subtask" if is_subtask else "top_level"] += 1
                stats["completed" if completed else "open"] += 1
                stats["names"][normalize_name(row["name"])] += 1
                gid = row["gid"]
                if gid not in tasks:
                    tasks[gid] = {
                        "group": group_key,
                        "gid": gid,
                        "name": row["name"] or "",
                        "project": name,
                        "is_subtask": is_subtask,
                        "completed": completed,
                        "created_at": row["created_at"] or "",
                        "modified_at": row["modified_at"] or "",
                        "parent_gid": row["parent_gid"] or "",
                        "parent_name": row["parent_name"] or "",
                        "assignee_email": row["assignee_email"] or "",
                        "creator_email": row["creator_email"] or "",
                    }
            per_project.append(stats)
    return {"per_project": per_project, "tasks": list(tasks.values()), "warnings": warnings}


def fetch_group_issues(
    client: JiraSearchClient,
    group_key: str,
    jira_key: str,
    page_size: int = PAGE_SIZE,
) -> List[dict]:
    """Return every live Jira issue in ``jira_key`` (paged via nextPageToken)."""
    jql = f'project = "{jira_key}" ORDER BY key ASC'
    issues: List[dict] = []
    next_page_token: Optional[str] = None
    page = 0
    while True:
        page += 1
        payload = client.search(
            jql=jql,
            fields=["issuetype", "summary", "status", "parent"],
            max_results=page_size,
            next_page_token=next_page_token,
        )
        batch = payload.get("issues", []) or []
        for issue in batch:
            fields = issue.get("fields") or {}
            issue_type = fields.get("issuetype") or {}
            status = fields.get("status") or {}
            parent = fields.get("parent") or {}
            issues.append(
                {
                    "group": group_key,
                    "key": (issue.get("key") or "").strip(),
                    "summary": (fields.get("summary") or "").strip(),
                    "issue_type": (issue_type.get("name") or "").strip(),
                    "is_subtask": bool(issue_type.get("subtask")),
                    "status": (status.get("name") or "").strip(),
                    "parent": (parent.get("key") or "").strip(),
                }
            )
        logger.info(
            "%s: fetched page %s (%s issues, running total %s)",
            jira_key, page, len(batch), len(issues),
        )
        next_page_token = payload.get("nextPageToken")
        if not next_page_token or not batch:
            break
    return issues


# --- Sheet builders (pure) -------------------------------------------------------------

def _overview_sheet(
    group_results: Sequence[dict],
    *,
    db_path: str,
    jira_base_url: str,
    generated_at: str,
) -> Tuple[str, List[list]]:
    rows: List[list] = [
        ["Foreign-Migration Coverage Report", ""],
        ["Generated (UTC)", generated_at],
        ["Source database", db_path],
        ["Jira base URL", jira_base_url],
        ["Match strategy", "Asana task name == Jira issue summary (normalized)"],
        ["", ""],
        ["Group", "Asana tasks", "Jira issues", "Matched", "Missing in Jira",
         "Extra in Jira", "Match rate %"],
    ]
    for result in group_results:
        asana_total = len(result["tasks"])
        matched = len(result["matched"])
        rate = round(100.0 * matched / asana_total, 1) if asana_total else 0.0
        rows.append([
            result["jira_key"],
            asana_total,
            len(result["jira_issues"]),
            matched,
            len(result["missing"]),
            len(result["extra"]),
            rate,
        ])
    return ("Overview", rows)


def _asana_baseline_sheet(group_results: Sequence[dict]) -> Tuple[str, List[list]]:
    rows: List[list] = [
        ["group", "source_project", "project_gid", "total", "top_level",
         "subtask", "completed", "open"],
    ]
    for result in group_results:
        for stats in result["asana"]["per_project"]:
            rows.append([
                result["jira_key"], stats["project"], stats["gid"], stats["total"],
                stats["top_level"], stats["subtask"], stats["completed"], stats["open"],
            ])
        tasks = result["tasks"]
        rows.append([
            f"{result['jira_key']} (unique union)", "", "",
            len(tasks),
            sum(1 for t in tasks if not t["is_subtask"]),
            sum(1 for t in tasks if t["is_subtask"]),
            sum(1 for t in tasks if t["completed"]),
            sum(1 for t in tasks if not t["completed"]),
        ])
    return ("Asana Baseline", rows)


def _jira_inventory_sheet(group_results: Sequence[dict]) -> Tuple[str, List[list]]:
    rows: List[list] = [["group", "issue_type", "is_subtask", "count"]]
    for result in group_results:
        counts: Counter = Counter()
        for issue in result["jira_issues"]:
            counts[(issue["issue_type"] or "(none)", issue["is_subtask"])] += 1
        for (issue_type, is_subtask), count in sorted(counts.items()):
            rows.append([result["jira_key"], issue_type, "Yes" if is_subtask else "No", count])
        rows.append([f"{result['jira_key']} TOTAL", "", "", len(result["jira_issues"])])
    return ("Jira Inventory", rows)


def _reconciliation_summary_sheet(group_results: Sequence[dict]) -> Tuple[str, List[list]]:
    rows: List[list] = [
        ["group", "asana_total", "jira_total", "matched", "missing_total",
         "missing_top_level", "missing_subtask", "missing_completed", "missing_open",
         "extra_total", "match_rate_pct"],
    ]
    for result in group_results:
        asana_total = len(result["tasks"])
        matched = len(result["matched"])
        breakdown = summarize_missing(result["missing"])
        rate = round(100.0 * matched / asana_total, 1) if asana_total else 0.0
        rows.append([
            result["jira_key"], asana_total, len(result["jira_issues"]), matched,
            len(result["missing"]), breakdown["top_level"], breakdown["subtask"],
            breakdown["completed"], breakdown["open"], len(result["extra"]), rate,
        ])
    return ("Reconciliation Summary", rows)


def _per_project_coverage_sheet(group_results: Sequence[dict]) -> Tuple[str, List[list]]:
    """Per source project, match its own task names against the group's Jira pool.

    This sidesteps the flat-group multiset collisions (generic ``Action:``/``Milestone:``
    names competing for the same issue) that deflate the group-level matched count — each
    project is scored independently. Because the pool is shared, one Jira issue can satisfy
    a name in more than one project, so these counts are per-project coverage estimates and
    do not sum to the group total.
    """
    rows: List[list] = [
        ["group", "source_project", "project_gid", "tasks", "top_level", "subtask",
         "completed", "open", "matched", "missing", "match_rate_pct"],
    ]
    for result in group_results:
        pool = Counter(normalize_name(issue["summary"]) for issue in result["jira_issues"])
        for stats in result["asana"]["per_project"]:
            names: Counter = stats.get("names") or Counter()
            matched = sum((names & pool).values())
            total = stats["total"]
            rate = round(100.0 * matched / total, 1) if total else 0.0
            rows.append([
                result["jira_key"], stats["project"], stats["gid"], total,
                stats["top_level"], stats["subtask"], stats["completed"], stats["open"],
                matched, total - matched, rate,
            ])
    return ("Per-Project Coverage", rows)


def _missing_sheet(group_results: Sequence[dict]) -> Tuple[str, List[list]]:
    rows: List[list] = [list(MISSING_COLUMNS)]
    for result in group_results:
        ordered = sorted(
            result["missing"],
            key=lambda t: (t["is_subtask"], t["project"], t["name"].casefold()),
        )
        for task in ordered:
            parent = task.get("parent_name") or task.get("parent_gid") or ""
            rows.append([
                result["jira_key"], task["project"], task["name"],
                "subtask" if task["is_subtask"] else "top-level",
                "Yes" if task["completed"] else "No",
                task.get("created_at") or "", task.get("modified_at") or "", parent,
                task.get("assignee_email") or "", task.get("creator_email") or "",
                task["gid"],
            ])
    return ("Missing in Jira", rows)


def _extra_sheet(group_results: Sequence[dict]) -> Tuple[str, List[list]]:
    rows: List[list] = [list(EXTRA_COLUMNS)]
    for result in group_results:
        ordered = sorted(result["extra"], key=lambda i: (i["is_subtask"], i["summary"].casefold()))
        for issue in ordered:
            rows.append([
                result["jira_key"], issue["key"], issue["summary"], issue["issue_type"],
                "Yes" if issue["is_subtask"] else "No", issue["status"],
            ])
    return ("Extra in Jira", rows)


def build_workbook_sheets(
    group_results: Sequence[dict],
    *,
    db_path: str,
    jira_base_url: str,
    generated_at: str,
) -> List[Tuple[str, List[list]]]:
    return [
        _overview_sheet(
            group_results, db_path=db_path, jira_base_url=jira_base_url, generated_at=generated_at
        ),
        _asana_baseline_sheet(group_results),
        _jira_inventory_sheet(group_results),
        _reconciliation_summary_sheet(group_results),
        _per_project_coverage_sheet(group_results),
        _missing_sheet(group_results),
        _extra_sheet(group_results),
    ]


def parse_group_overrides(values: Optional[Sequence[str]]) -> Dict[str, List[str]]:
    """Parse repeatable ``--group "KEY=Name A||Name B"`` values into a group map."""
    if not values:
        return dict(MIGRATION_GROUPS)
    groups: Dict[str, List[str]] = {}
    for value in values:
        if "=" not in value:
            raise SystemExit(f"--group must be KEY=Name A||Name B (got {value!r})")
        key, _, names = value.partition("=")
        key = key.strip()
        parsed = [name.strip() for name in names.split("||") if name.strip()]
        if not key or not parsed:
            raise SystemExit(f"--group must name a key and at least one project (got {value!r})")
        groups.setdefault(key, []).extend(parsed)
    return groups


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        default=resolve_default_db_path(),
        help="Path to the SQLite database (default: resolved active-profile DB).",
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help="Directory to store the .xlsx report (default: exports/).",
    )
    parser.add_argument(
        "--filename",
        default=None,
        help="Override the auto-generated migration_coverage_<timestamp>.xlsx filename.",
    )
    parser.add_argument(
        "--group",
        action="append",
        default=None,
        help='Override the migration groups: KEY=Name A||Name B (repeatable). '
             "Defaults to the built-in POPS/PROJ24 groups.",
    )
    parser.add_argument(
        "--prod",
        action="store_true",
        help="Target the production Jira site (JIRA_BASE_URL). Default: sandbox (JIRA_SANDBOX_BASE_URL).",
    )
    parser.add_argument("--log-level", default="INFO", help="Logging verbosity (default: INFO).")
    return parser.parse_args(argv)


def main(argv: Optional[Iterable[str]] = None) -> None:
    args = parse_args(argv)
    try:
        log_level = resolve_log_level(args.log_level)
    except ValueError as exc:  # noqa: BLE001
        raise SystemExit(str(exc)) from exc
    configure_logging(log_level)

    groups = parse_group_overrides(args.group)

    credentials = load_credentials()
    try:
        username, jira_token = ensure_atlassian_credentials(credentials)
        base_url = resolve_jira_base_url(credentials, prod=args.prod)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    database = Database(args.database)
    connection = database.connect()
    try:
        asana_by_group = {
            key: build_asana_group(connection, key, names) for key, names in groups.items()
        }
    finally:
        connection.close()

    for group in asana_by_group.values():
        for warning in group["warnings"]:
            logger.warning(warning)

    client = JiraSearchClient(
        base_url=base_url,
        username=username,
        token=jira_token,
        skip_ssl_verification=credentials.skip_ssl_verification,
    )
    logger.info("Querying %s for %s Jira project(s): %s", base_url, len(groups), ", ".join(groups))

    group_results: List[dict] = []
    for jira_key, asana in asana_by_group.items():
        jira_issues = fetch_group_issues(client, jira_key, jira_key)
        matched, missing, extra = reconcile_group(asana["tasks"], jira_issues)
        group_results.append(
            {
                "jira_key": jira_key,
                "asana": asana,
                "tasks": asana["tasks"],
                "jira_issues": jira_issues,
                "matched": matched,
                "missing": missing,
                "extra": extra,
            }
        )
        logger.info(
            "%s: %s Asana task(s), %s Jira issue(s) -> matched=%s missing=%s extra=%s",
            jira_key, len(asana["tasks"]), len(jira_issues), len(matched), len(missing), len(extra),
        )

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    filename = args.filename or (
        "migration_coverage_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".xlsx"
    )
    output_path = Path(args.output_dir) / filename
    sheets = build_workbook_sheets(
        group_results,
        db_path=str(Path(args.database).resolve()),
        jira_base_url=base_url,
        generated_at=generated_at,
    )
    write_workbook(output_path, sheets)
    logger.info("Wrote report to %s", output_path.resolve())


if __name__ == "__main__":
    main()
