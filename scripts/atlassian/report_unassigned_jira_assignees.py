#!/usr/bin/env python3
"""One-time report: Jira issues created without an assignee vs. the revised owner logic.

Read-only diagnostic. Queries the live Jira instance for the migrated ATJM issues that
are currently **unassigned** (ground truth, since manual edits and the create-time
assignee ``400`` drop mean the local DB can't be trusted for this), maps each back to its
Asana project/team via ``team_project_jira_mappings``, and recomputes the *revised*
effective owner locally via ``build_team_project_rows`` (which now includes the
``project_member`` fallback rung). Emits a multi-tab ``.xlsx`` showing which unassigned
issues would now get an assignee, and who.

Writes nothing to Jira, Asana, or the database.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections import Counter
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
from scripts.atlassian.export_team_project_jira_csv import build_team_project_rows
from src.lib.config import load_credentials, resolve_jira_base_url
from src.lib.database import ASANA_JIRA_SITE_KEY_ENV, Database, resolve_default_db_path
from src.lib.logging_utils import configure_logging, resolve_log_level
from src.lib.xlsx_writer import write_workbook


logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_DIR = "exports"
PAGE_SIZE = 100  # /search/jql page size; results are paged via nextPageToken.
ISSUE_TYPES = ("Task", "Epic")

# Column order for the Unassigned Issues tab.
REPORT_COLUMNS = [
    "jira_key",
    "issue_type",
    "summary",
    "asana_kind",
    "asana_gid",
    "asana_name",
    "would_get_assignee",
    "revised_owner_source",
    "revised_owner_name",
    "revised_owner_email",
    "revised_owner_atlassian_id",
    "revised_owner_atlassian_status",
]


def derive_project_keys(jira_keys: Iterable[str]) -> List[str]:
    """Return the sorted distinct Jira project keys (prefix before ``-``) from issue keys."""
    keys = set()
    for jira_key in jira_keys:
        text = (jira_key or "").strip()
        if "-" in text:
            prefix = text.split("-", 1)[0].strip()
            if prefix:
                keys.add(prefix)
    return sorted(keys)


def load_mappings(connection) -> Dict[str, Tuple[str, str]]:
    """Return ``jira_key -> (kind, asana_gid)`` from ``team_project_jira_mappings``."""
    jira_site_key = os.environ.get(ASANA_JIRA_SITE_KEY_ENV)
    sql = "SELECT jira_key, kind, asana_gid FROM team_project_jira_mappings"
    rows = connection.execute(
        sql + (" WHERE jira_site_key = ?" if jira_site_key else ""),
        ((jira_site_key,) if jira_site_key else ()),
    ).fetchall()
    mappings: Dict[str, Tuple[str, str]] = {}
    for row in rows:
        jira_key = (row["jira_key"] or "").strip()
        if jira_key:
            mappings[jira_key] = ((row["kind"] or "").strip(), (row["asana_gid"] or "").strip())
    return mappings


def build_revised_lookup(connection) -> Dict[str, dict]:
    """Return ``asana_gid -> row`` for every Team→Epic and Project→Task row.

    ``build_team_project_rows`` already applies the revised owner ladder (owner →
    top active task creator → top active project member). Every project/team gets a
    row (``include_empty_projects=True``); the no-team epic keys on ``""``.
    """
    epic_rows, task_rows = build_team_project_rows(connection, include_empty_projects=True)
    lookup: Dict[str, dict] = {}
    for row in [*epic_rows, *task_rows]:
        lookup[row.get("_asana_gid", "")] = row
    return lookup


def fetch_unassigned_issues(
    client: JiraSearchClient,
    project_keys: Sequence[str],
    issue_types: Sequence[str] = ISSUE_TYPES,
    page_size: int = PAGE_SIZE,
) -> List[dict]:
    """Return the currently-unassigned Jira issues (as ``{key, issue_type, summary}``).

    Filters server-side to ``assignee IS EMPTY`` so only unassigned issues are pulled;
    paginates via ``nextPageToken``.
    """
    projects = ", ".join(f'"{key}"' for key in project_keys)
    types = ", ".join(f'"{name}"' for name in issue_types)
    jql = f"project IN ({projects}) AND assignee IS EMPTY AND issuetype IN ({types}) ORDER BY key ASC"

    issues: List[dict] = []
    next_page_token: Optional[str] = None
    page = 0
    while True:
        page += 1
        payload = client.search(
            jql=jql,
            fields=["issuetype", "summary"],
            max_results=page_size,
            next_page_token=next_page_token,
        )
        batch = payload.get("issues", []) or []
        for issue in batch:
            fields = issue.get("fields") or {}
            issue_type = ((fields.get("issuetype") or {}).get("name") or "").strip()
            issues.append(
                {
                    "key": (issue.get("key") or "").strip(),
                    "issue_type": issue_type,
                    "summary": (fields.get("summary") or "").strip(),
                }
            )
        logger.info("Fetched page %s (%s issues, running total %s)", page, len(batch), len(issues))
        next_page_token = payload.get("nextPageToken")
        if not next_page_token or not batch:
            break
    return issues


def build_report_rows(
    unassigned_issues: Sequence[dict],
    mappings: Dict[str, Tuple[str, str]],
    revised_by_gid: Dict[str, dict],
) -> Tuple[List[dict], int]:
    """Join unassigned Jira issues to their mapped Asana source + revised owner.

    Returns ``(report_rows, skipped_unmapped_count)``. Issues absent from
    ``mappings`` (e.g. manually-created ATJM issues) are skipped and counted.
    Pure function — no network or DB access — so it is unit-testable.
    """
    report_rows: List[dict] = []
    skipped_unmapped = 0
    for issue in unassigned_issues:
        jira_key = issue.get("key", "")
        mapping = mappings.get(jira_key)
        if mapping is None:
            skipped_unmapped += 1
            continue
        asana_kind, asana_gid = mapping
        revised = revised_by_gid.get(asana_gid) or {}
        atlassian_id = (revised.get("Owner Atlassian ID") or "").strip()
        report_rows.append(
            {
                "jira_key": jira_key,
                "issue_type": issue.get("issue_type", ""),
                "summary": issue.get("summary", ""),
                "asana_kind": asana_kind,
                "asana_gid": asana_gid,
                "asana_name": (revised.get("Summary") or "").strip(),
                "would_get_assignee": "Yes" if atlassian_id else "No",
                "revised_owner_source": (revised.get("Owner Source") or "").strip() or "none",
                "revised_owner_name": (revised.get("Owner") or "").strip(),
                "revised_owner_email": (revised.get("Owner Email") or "").strip(),
                "revised_owner_atlassian_id": atlassian_id,
                "revised_owner_atlassian_status": (revised.get("Owner Atlassian Status") or "").strip(),
            }
        )
    # Recovered first (Yes before No), then by issue type, then Asana name.
    report_rows.sort(
        key=lambda r: (
            r["would_get_assignee"] != "Yes",
            r["issue_type"],
            r["asana_name"].casefold(),
            r["jira_key"],
        )
    )
    return report_rows, skipped_unmapped


def _unassigned_sheet(report_rows: Sequence[dict]) -> Tuple[str, List[list]]:
    rows: List[list] = [list(REPORT_COLUMNS)]
    for row in report_rows:
        rows.append([row[col] for col in REPORT_COLUMNS])
    return ("Unassigned Issues", rows)


def _summary_sheet(report_rows: Sequence[dict]) -> Tuple[str, List[list]]:
    counts: Counter = Counter()
    for row in report_rows:
        counts[(row["issue_type"] or "(unknown)", row["revised_owner_source"])] += 1
    rows: List[list] = [["issue_type", "revised_owner_source", "would_get_assignee", "count"]]
    for (issue_type, source), count in sorted(counts.items()):
        would = "No" if source == "none" else "Yes"
        rows.append([issue_type, source, would, count])
    total = len(report_rows)
    would_get = sum(1 for r in report_rows if r["would_get_assignee"] == "Yes")
    rows.append([])
    rows.append(["TOTAL unassigned", "", "", total])
    rows.append(["Would get an assignee (revised)", "", "Yes", would_get])
    rows.append(["Still unassigned (revised)", "", "No", total - would_get])
    return ("Summary", rows)


def _overview_sheet(
    report_rows: Sequence[dict],
    *,
    db_path: str,
    jira_base_url: str,
    project_keys: Sequence[str],
    skipped_unmapped: int,
    generated_at: str,
) -> Tuple[str, List[list]]:
    total = len(report_rows)
    would_get = sum(1 for r in report_rows if r["would_get_assignee"] == "Yes")
    rows: List[list] = [
        ["Unassigned Jira Assignee Report", ""],
        ["Generated (UTC)", generated_at],
        ["Source database", db_path],
        ["Jira base URL", jira_base_url],
        ["Jira projects", ", ".join(project_keys)],
        ["Issue types", ", ".join(ISSUE_TYPES)],
        ["JQL filter", "assignee IS EMPTY"],
        ["Unassigned + mapped issues", total],
        ["  Would get an assignee (revised)", would_get],
        ["  Still unassigned (revised)", total - would_get],
        ["Unassigned issues not in our mappings (skipped)", skipped_unmapped],
        ["", ""],
        ["Tab", "Rows"],
        ["Unassigned Issues", total],
    ]
    return ("Overview", rows)


def build_workbook_sheets(
    report_rows: Sequence[dict],
    *,
    db_path: str,
    jira_base_url: str,
    project_keys: Sequence[str],
    skipped_unmapped: int,
    generated_at: str,
) -> List[Tuple[str, List[list]]]:
    return [
        _overview_sheet(
            report_rows,
            db_path=db_path,
            jira_base_url=jira_base_url,
            project_keys=project_keys,
            skipped_unmapped=skipped_unmapped,
            generated_at=generated_at,
        ),
        _unassigned_sheet(report_rows),
        _summary_sheet(report_rows),
    ]


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
        help="Override the auto-generated unassigned_jira_assignees_<timestamp>.xlsx filename.",
    )
    parser.add_argument(
        "--prod",
        action="store_true",
        help="Target the production Jira site (JIRA_BASE_URL). Default: sandbox (JIRA_SANDBOX_BASE_URL).",
    )
    parser.add_argument("--jira-site", help="Named Jira site key from the active profile.")
    parser.add_argument("--log-level", default="INFO", help="Logging verbosity (default: INFO).")
    return parser.parse_args(argv)


def main(argv: Optional[Iterable[str]] = None) -> None:
    args = parse_args(argv)
    try:
        log_level = resolve_log_level(args.log_level)
    except ValueError as exc:  # noqa: BLE001
        raise SystemExit(str(exc)) from exc
    configure_logging(log_level)

    credentials = load_credentials()
    try:
        username, jira_token = ensure_atlassian_credentials(credentials)
        base_url = resolve_jira_base_url(credentials, prod=args.prod, site_key=args.jira_site)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    database = Database(args.database)
    connection = database.connect()
    try:
        mappings = load_mappings(connection)
        if not mappings:
            raise SystemExit(
                "No rows in team_project_jira_mappings; nothing was created via the "
                "Team→Epic / Project→Task path for this profile."
            )
        project_keys = derive_project_keys(mappings.keys())
        logger.info("Loaded %s mapping(s) across Jira project(s): %s", len(mappings), ", ".join(project_keys))
        revised_by_gid = build_revised_lookup(connection)
    finally:
        connection.close()

    client = JiraSearchClient(
        base_url=base_url,
        username=username,
        token=jira_token,
        skip_ssl_verification=credentials.skip_ssl_verification,
    )
    logger.info("Querying %s for unassigned issues (%s)...", base_url, ", ".join(ISSUE_TYPES))
    unassigned_issues = fetch_unassigned_issues(client, project_keys)
    logger.info("Jira returned %s unassigned issue(s).", len(unassigned_issues))

    report_rows, skipped_unmapped = build_report_rows(unassigned_issues, mappings, revised_by_gid)
    would_get = sum(1 for r in report_rows if r["would_get_assignee"] == "Yes")
    logger.info(
        "%s unassigned + mapped issue(s); %s would get an assignee under the revised logic; "
        "%s unmapped issue(s) skipped.",
        len(report_rows),
        would_get,
        skipped_unmapped,
    )

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    output_dir = Path(args.output_dir)
    filename = args.filename or (
        "unassigned_jira_assignees_"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + ".xlsx"
    )
    output_path = output_dir / filename
    sheets = build_workbook_sheets(
        report_rows,
        db_path=str(Path(args.database).resolve()),
        jira_base_url=base_url,
        project_keys=project_keys,
        skipped_unmapped=skipped_unmapped,
        generated_at=generated_at,
    )
    write_workbook(output_path, sheets)
    logger.info("Wrote report to %s", output_path.resolve())


if __name__ == "__main__":
    main()
