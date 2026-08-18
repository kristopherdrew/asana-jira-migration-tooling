#!/usr/bin/env python3
"""Pull the 'Asana Migration Decision' custom field from project-level Jira issues into SQLite.

Each Asana project was migrated as a Jira Task issue (tracked in
``team_project_jira_mappings`` with ``kind='project'``). Stakeholders set a Jira
custom field named "Asana Migration Decision" on those issues. This script reads
that field back via JQL, maps each ``jira_key`` to its Asana project GID, and
upserts the value into ``project_jira_decisions`` so reports can surface it.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.lib.config import load_credentials, resolve_jira_base_url
from src.lib.database import Database, DEFAULT_DB_PATH, resolve_default_db_path
from src.lib.logging_utils import configure_logging, resolve_log_level
from scripts.atlassian.search_jira_external_ids import (
    DEFAULT_FIELDS,
    JIRA_SEARCH_PAGE_SIZE,
    JiraIssue,
    JiraSearchClient,
    build_jql,
    ensure_atlassian_credentials,
)


logger = logging.getLogger(__name__)
DEFAULT_FIELD_NAME = "Asana Migration Decision"


def resolve_field_id(client: JiraSearchClient, field_name: str) -> str:
    """Resolve a Jira field's ``customfield_*`` id from its display name.

    Raises ``SystemExit`` when no field matches or when the name is ambiguous
    (maps to more than one distinct id).
    """
    target = field_name.strip().casefold()
    matches = {
        str(field.get("id"))
        for field in client.list_fields()
        if str(field.get("name", "")).strip().casefold() == target and field.get("id")
    }
    if not matches:
        raise SystemExit(
            f"No Jira field named {field_name!r} was found. "
            "Check the field name (case-insensitive) or pass --field-name."
        )
    if len(matches) > 1:
        raise SystemExit(
            f"Jira field name {field_name!r} is ambiguous; it maps to multiple ids: "
            f"{', '.join(sorted(matches))}. Resolve the duplicate in Jira."
        )
    field_id = matches.pop()
    logger.info("Resolved field %r to %s.", field_name, field_id)
    return field_id


def _stringify_decision(raw: object) -> Optional[str]:
    """Normalize a Jira field value to a plain string.

    Single-select option fields return ``{"value": ...}``; multi-select fields
    return a list of such objects/strings; free-text returns a string. Returns
    ``None`` for empty values.
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        return raw.strip() or None
    if isinstance(raw, (int, float)):
        return str(raw)
    if isinstance(raw, dict):
        value = raw.get("value") or raw.get("name")
        return (str(value).strip() or None) if value is not None else None
    if isinstance(raw, list):
        parts = [_stringify_decision(item) for item in raw]
        joined = ", ".join(part for part in parts if part)
        return joined or None
    return str(raw).strip() or None


def extract_decisions(payload: Dict, field_id: str) -> List[JiraIssue]:
    """Build JiraIssue rows carrying the decision value in ``external_id``."""
    results: List[JiraIssue] = []
    for issue in payload.get("issues", []):
        fields = issue.get("fields", {}) or {}
        created = fields.get("created")
        issue_key = issue.get("key")
        if not created or not issue_key:
            logger.debug("Issue %s missing key/created; skipping.", issue_key)
            continue
        decision = _stringify_decision(fields.get(field_id))
        results.append(JiraIssue(key=issue_key, created=created, external_id=decision))
    return results


def collect_decisions(
    client: JiraSearchClient,
    base_jql: str,
    field_id: str,
    max_results: int,
) -> List[JiraIssue]:
    """Page through Jira via JQL, harvesting the decision field for each issue.

    Mirrors the deterministic created-timestamp batching used by
    ``search_jira_external_ids`` so issues with identical timestamps are not lost.
    """
    collected: List[JiraIssue] = []
    batch = 1
    cursor: Optional[JiraIssue] = None
    while True:
        next_page_token: Optional[str] = None
        jql_with_cursor = build_jql(base_jql, cursor)
        iteration = 1
        batch_issues: List[JiraIssue] = []
        while True:
            payload = client.search(
                jql=jql_with_cursor,
                fields=[*DEFAULT_FIELDS, field_id],
                max_results=max_results,
                next_page_token=next_page_token,
            )
            issues = extract_decisions(payload, field_id)
            if not issues:
                logger.info("No issues returned (batch %s, page %s).", batch, iteration)
                break
            batch_issues.extend(issues)
            collected.extend(issues)
            logger.info(
                "Retrieved %s issue(s) in batch %s page %s (total: %s).",
                len(issues),
                batch,
                iteration,
                len(collected),
            )
            next_page_token = payload.get("nextPageToken")
            if not next_page_token:
                break
            iteration += 1
        if not batch_issues:
            break
        last_created = batch_issues[-1].created
        keys_at_last_ts = [issue.key for issue in batch_issues if issue.created == last_created]
        cursor = JiraIssue(
            key=batch_issues[-1].key,
            created=last_created,
            external_id=batch_issues[-1].external_id,
            meta={"keys_at_ts": keys_at_last_ts},
        )
        batch += 1
    return collected


def load_project_jira_keys(connection) -> Dict[str, str]:
    """Return ``jira_key -> project_gid`` for project-level Jira mappings."""
    database = Database()
    mappings = database.load_team_project_jira_mappings(connection)
    return {
        jira_key: asana_gid
        for (kind, asana_gid), jira_key in mappings.items()
        if kind == "project" and asana_gid
    }


def derive_project_keys(jira_keys: Iterable[str]) -> List[str]:
    """Extract the distinct Jira project keys from issue keys (e.g. 'MIG-12' -> 'MIG')."""
    keys: List[str] = []
    seen = set()
    for jira_key in jira_keys:
        if "-" not in jira_key:
            continue
        prefix = jira_key.rsplit("-", 1)[0].strip()
        if prefix and prefix not in seen:
            seen.add(prefix)
            keys.append(prefix)
    return sorted(keys)


def build_default_jql(project_keys: Sequence[str], field_name: str) -> str:
    """Build the default JQL: project-scoped Tasks with a non-empty decision field.

    ``project IN (...) AND issuetype = "Task" AND "<field_name>" is not EMPTY``
    """
    field = field_name.replace('"', '\\"')
    clause = f'issuetype = "Task" AND "{field}" is not EMPTY'
    if project_keys:
        quoted = ", ".join(f'"{key}"' for key in project_keys)
        return f"project IN ({quoted}) AND {clause}"
    return clause


def default_jql_from_database(database_path: str, field_name: str) -> str:
    """Derive the default JQL from the project-level Jira keys already in SQLite.

    Reads ``team_project_jira_mappings`` (``kind='project'``), pulls the distinct
    Jira project keys out of the stored issue keys, and scopes the search to them.
    Raises ``SystemExit`` when no project-level mappings exist (nothing to scope to).
    """
    database = Database(db_path=database_path)
    with database.connect() as connection:
        database.initialize(connection)
        key_to_project = load_project_jira_keys(connection)
    project_keys = derive_project_keys(key_to_project.keys())
    if not project_keys:
        raise SystemExit(
            "No project-level Jira mappings found in team_project_jira_mappings, so a default "
            "JQL cannot be derived. Run create_team_project_jira_issues first, or pass --jql explicitly."
        )
    return build_default_jql(project_keys, field_name)


def upsert_decisions(
    database_path: str,
    issues: Sequence[JiraIssue],
    include_empty: bool = False,
) -> Dict[str, int]:
    database = Database(db_path=database_path)
    with database.connect() as connection:
        database.initialize(connection)
        key_to_project = load_project_jira_keys(connection)
        rows: List[Dict[str, str]] = []
        unmapped: List[str] = []
        empty = 0
        for issue in issues:
            project_gid = key_to_project.get(issue.key)
            if not project_gid:
                unmapped.append(issue.key)
                continue
            if not issue.external_id and not include_empty:
                empty += 1
                continue
            rows.append(
                {
                    "project_gid": project_gid,
                    "jira_key": issue.key,
                    "decision": issue.external_id or "",
                }
            )
        now = datetime.now(timezone.utc)
        written = database.upsert_project_jira_decisions(connection, rows, now).get("written", 0)
    if unmapped:
        logger.warning(
            "Skipped %s issue(s) with no project-level mapping in team_project_jira_mappings "
            "(e.g. %s). Run create_team_project_jira_issues first if these should map.",
            len(unmapped),
            ", ".join(unmapped[:10]) + ("..." if len(unmapped) > 10 else ""),
        )
    if empty:
        logger.info("Skipped %s mapped issue(s) with an empty decision (use --include-empty to store).", empty)
    return {
        "processed": len(issues),
        "written": written,
        "unmapped": len(unmapped),
        "empty_skipped": empty,
    }


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--prod",
        action="store_true",
        help="Target the production Jira site (JIRA_BASE_URL). Default: sandbox (JIRA_SANDBOX_BASE_URL).",
    )
    parser.add_argument("--jira-site", help="Named Jira site key from the active profile.")
    parser.add_argument(
        "--jql",
        default=None,
        help=(
            "JQL selecting the issues to read. When omitted, a default is derived from the "
            "project-level Jira keys already in the DB: "
            'project IN (<derived keys>) AND issuetype = "Task" AND "<field-name>" is not EMPTY. '
            "ORDER BY created ASC is applied automatically."
        ),
    )
    parser.add_argument(
        "--field-name",
        default=DEFAULT_FIELD_NAME,
        help=f"Display name of the decision field to resolve (default: {DEFAULT_FIELD_NAME!r}).",
    )
    parser.add_argument(
        "--include-empty",
        action="store_true",
        help="Store mapped issues even when the decision field is blank (default: skip blanks).",
    )
    parser.add_argument(
        "--database",
        default=resolve_default_db_path(),
        help="Path to SQLite DB for writing decisions (default: data/asana.sqlite).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and display decisions without writing them to the database.",
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

    credentials = load_credentials()
    try:
        username, jira_token = ensure_atlassian_credentials(credentials)
        base_url = resolve_jira_base_url(credentials, prod=args.prod, site_key=args.jira_site)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    jql = args.jql
    if not jql:
        jql = default_jql_from_database(args.database, args.field_name)
        logger.info("No --jql provided; using derived default: %s", jql)

    logger.info(
        "Starting Jira decision pull (base=%s, field=%r, maxResults=%s, dry_run=%s)",
        base_url,
        args.field_name,
        JIRA_SEARCH_PAGE_SIZE,
        args.dry_run,
    )
    client = JiraSearchClient(
        base_url=base_url,
        username=username,
        token=jira_token,
        skip_ssl_verification=credentials.skip_ssl_verification,
    )

    field_id = resolve_field_id(client, args.field_name)
    issues = collect_decisions(
        client=client,
        base_jql=jql,
        field_id=field_id,
        max_results=JIRA_SEARCH_PAGE_SIZE,
    )
    if not issues:
        logger.warning("No issues matched the provided JQL.")
        return

    if args.dry_run:
        logger.info("Dry run enabled; not writing to DB. Found %s issue(s).", len(issues))
        for issue in issues:
            logger.info("Jira: %s | Decision: %s", issue.key, issue.external_id or "<empty>")
        return

    summary = upsert_decisions(args.database, issues, include_empty=args.include_empty)
    logger.info(
        "Decision pull complete: processed=%s written=%s unmapped=%s empty_skipped=%s",
        summary["processed"],
        summary["written"],
        summary["unmapped"],
        summary["empty_skipped"],
    )


if __name__ == "__main__":
    main()
