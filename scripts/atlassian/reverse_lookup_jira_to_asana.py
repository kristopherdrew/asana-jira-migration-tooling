#!/usr/bin/env python3
"""Reverse lookup: search Jira for external IDs (Asana task GIDs) and map back to local DB.

Discovers Jira issues that have external IDs set (from prior CSV imports or external migrations),
then establishes task_jira_mappings and jira_project_mappings in the local SQLite database.
Useful for backfilling mappings from collaborators' Jira data.
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import logging
import sqlite3
import ssl
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple
from urllib import error, request

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.lib.config import (
    Credentials,
    load_credentials,
    resolve_credentials_path,
    resolve_jira_base_url,
    resolve_jira_site,
)
from src.lib.database import Database, resolve_default_db_path
from src.lib.jira_csv_import_config import load_archive_import_settings
from src.lib.logging_utils import configure_logging, resolve_log_level

logger = logging.getLogger(__name__)
JIRA_SEARCH_PAGE_SIZE = 100
DEFAULT_EXTERNAL_ID_FIELD = "External issue ID"


@dataclass(frozen=True)
class JiraIssue:
    key: str
    external_id: Optional[str]
    project_key: str


class JiraSearchClient:
    """Minimal client for Jira v3 search endpoint."""

    def __init__(
        self,
        base_url: str,
        username: str,
        token: str,
        skip_ssl_verification: bool = False,
        timeout: int = 30,
    ):
        self._base_url = base_url.rstrip("/")
        auth_bytes = f"{username}:{token}".encode("utf-8")
        self._auth_header = f"Basic {base64.b64encode(auth_bytes).decode('ascii')}"
        context = ssl.create_default_context()
        if skip_ssl_verification:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        self._opener = request.build_opener(request.HTTPSHandler(context=context))
        self._timeout = timeout

    def search(
        self,
        jql: str,
        fields: Sequence[str],
        max_results: int,
        next_page_token: Optional[str] = None,
    ) -> Dict:
        body = json.dumps(
            {
                "jql": jql,
                "maxResults": max_results,
                "nextPageToken": next_page_token,
                "fields": list(fields),
            }
        ).encode("utf-8")
        headers = {
            "Authorization": self._auth_header,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "asana-jira-reverse-lookup/1.0",
        }
        return self._request("POST", "/rest/api/3/search/jql", headers=headers, data=body)

    def get_project(self, project_key: str) -> Optional[Dict]:
        """Fetch project details to get project name."""
        headers = {
            "Authorization": self._auth_header,
            "Accept": "application/json",
            "User-Agent": "asana-jira-reverse-lookup/1.0",
        }
        try:
            return self._request("GET", f"/rest/api/3/project/{project_key}", headers=headers)
        except Exception as exc:
            logger.warning(f"Failed to fetch project {project_key}: {exc}")
            return None

    def list_fields(self) -> List[Dict[str, object]]:
        """Return all Jira fields for name->id resolution."""
        headers = {
            "Authorization": self._auth_header,
            "Accept": "application/json",
            "User-Agent": "asana-jira-reverse-lookup/1.0",
        }
        payload = self._request("GET", "/rest/api/3/field", headers=headers)
        return payload if isinstance(payload, list) else []

    def _request(
        self,
        method: str,
        path: str,
        headers: Optional[Dict[str, str]] = None,
        data: Optional[bytes] = None,
        max_retries: int = 5,
        backoff: float = 1.5,
    ) -> Dict:
        url = f"{self._base_url}{path}"
        last_error: Optional[Exception] = None
        for attempt in range(max_retries):
            req = request.Request(url=url, method=method, data=data)
            if headers:
                for key, value in headers.items():
                    req.add_header(key, value)
            try:
                with self._opener.open(req, timeout=self._timeout) as resp:
                    payload = resp.read().decode("utf-8")
                    return json.loads(payload)
            except error.HTTPError as exc:
                if exc.code == 429:
                    import time
                    time.sleep(backoff**attempt)
                    last_error = Exception(f"Rate limited: {exc}")
                    continue
                if 500 <= exc.code < 600:
                    import time
                    time.sleep(backoff**attempt)
                    last_error = Exception(f"Server error {exc.code}: {exc}")
                    continue
                body = exc.read().decode("utf-8") if exc.fp else exc.reason
                raise Exception(f"Jira error {exc.code}: {body or exc.reason}") from exc
            except error.URLError as exc:
                import time
                time.sleep(backoff**attempt)
                last_error = exc
        if last_error:
            raise last_error
        raise Exception("Jira search failed without response")


def resolve_external_id_field(client: JiraSearchClient, field_name: Optional[str], field_id: Optional[str]) -> str:
    """Resolve external ID field name/id to a valid field ID.

    Priority: explicit field_id > field_name > default.
    """
    if field_id and field_id.startswith("customfield_"):
        return field_id

    if field_name:
        fields = client.list_fields()
        for field in fields:
            if field.get("name") == field_name:
                return field["id"]

    fields = client.list_fields()
    for field in fields:
        if field.get("name") == DEFAULT_EXTERNAL_ID_FIELD:
            return field["id"]

    raise ValueError(
        f"Could not resolve external ID field. "
        f"Provide --external-id-field or configure {DEFAULT_EXTERNAL_ID_FIELD} in Jira."
    )


def collect_issues(payload: Dict, external_field: str) -> List[JiraIssue]:
    """Extract issues with external IDs from Jira search result."""
    results: List[JiraIssue] = []
    for issue in payload.get("issues", []):
        key = issue.get("key")
        if not key:
            continue

        external_id = None
        fields = issue.get("fields", {})
        if external_field in fields:
            val = fields[external_field]
            if isinstance(val, str):
                external_id = val.strip() or None
            elif isinstance(val, dict) and "value" in val:
                external_id = str(val["value"]).strip() or None

        project_key = key.rsplit("-", 1)[0] if "-" in key else key

        if external_id:
            results.append(JiraIssue(key=key, external_id=external_id, project_key=project_key))

    return results


def lookup_task_projects(
    connection: sqlite3.Connection,
    task_gid: str,
) -> List[str]:
    """Return project GIDs for a task."""
    rows = connection.execute(
        "SELECT project_gid FROM task_projects WHERE task_gid = ?",
        (task_gid,),
    ).fetchall()
    return [row["project_gid"] for row in rows]


def ensure_atlassian_credentials(creds: Credentials) -> Tuple[str, str]:
    if not creds.atlassian_username or not creds.atlassian_token:
        raise ValueError(
            "Resolved credentials must include 'ATLASSIAN_USERNAME' and 'ATLASSIAN_TOKEN'."
        )
    return creds.atlassian_username, creds.atlassian_token


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reverse lookup: search Jira for external IDs and map to local DB."
    )
    parser.add_argument("--env-path", help="Path to .env file (default: resolved from profile)")
    parser.add_argument("--database", help="SQLite database path (default: resolved)")
    parser.add_argument(
        "--jira-site",
        required=True,
        help="Jira site key (required)",
    )
    parser.add_argument(
        "--jql",
        required=True,
        help="JQL query to find issues (required)",
    )
    parser.add_argument(
        "--external-id-field",
        help="Jira custom field ID or name for external ID (default: resolved from import settings)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report findings without upserting",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level",
    )

    args = parser.parse_args(argv)
    configure_logging(resolve_log_level(args.log_level))

    try:
        creds = load_credentials(args.env_path) if args.env_path else load_credentials()
        username, token = ensure_atlassian_credentials(creds)
        base_url = resolve_jira_base_url(creds, args.jira_site)
        db_path = args.database or resolve_default_db_path()

        logger.info(f"Jira site: {args.jira_site} ({base_url})")
        logger.info(f"Database: {db_path}")
        logger.info(f"JQL: {args.jql}")

        client = JiraSearchClient(
            base_url=base_url,
            username=username,
            token=token,
            skip_ssl_verification=creds.skip_ssl_verification,
        )

        external_id_field = args.external_id_field
        if not external_id_field:
            settings = load_archive_import_settings(resolve_credentials_path(), args.jira_site)
            if settings.external_id:
                external_id_field = settings.external_id.field_id
        if not external_id_field:
            external_id_field = resolve_external_id_field(client, None, None)

        logger.info(f"External ID field: {external_id_field}")

        db = Database(db_path=db_path)
        with db.connect() as connection:
            timestamp = datetime.now(timezone.utc)

            mapped_tasks = []
            mapped_projects = []
            orphan_tasks = []
            conflicts = []

            next_page_token: Optional[str] = None
            processed = 0

            while True:
                logger.info(f"Fetching page (token: {next_page_token or 'first'})...")
                payload = client.search(
                    jql=args.jql,
                    fields=(external_id_field,),
                    max_results=JIRA_SEARCH_PAGE_SIZE,
                    next_page_token=next_page_token,
                )

                issues = collect_issues(payload, external_id_field)
                logger.info(f"Found {len(issues)} issues with external IDs")

                for issue in issues:
                    processed += 1
                    task_gid = issue.external_id
                    jira_key = issue.key
                    project_key = issue.project_key

                    task_exists = connection.execute(
                        "SELECT 1 FROM tasks WHERE gid = ? LIMIT 1",
                        (task_gid,),
                    ).fetchone()

                    if not task_exists:
                        orphan_tasks.append({
                            "jira_key": jira_key,
                            "external_id": task_gid,
                            "reason": "Task not found in local DB",
                        })
                        continue

                    project_gids = lookup_task_projects(connection, task_gid)
                    if not project_gids:
                        orphan_tasks.append({
                            "jira_key": jira_key,
                            "external_id": task_gid,
                            "reason": "Task has no projects",
                        })
                        continue

                    if not args.dry_run:
                        try:
                            existing = connection.execute(
                                "SELECT jira_key FROM task_jira_mappings WHERE jira_site_key = ? AND task_gid = ?",
                                (args.jira_site, task_gid),
                            ).fetchone()

                            if existing and existing["jira_key"] != jira_key:
                                conflicts.append({
                                    "conflict_type": "task_jira_mappings",
                                    "jira_key": jira_key,
                                    "task_gid": task_gid,
                                    "existing_value": existing["jira_key"],
                                    "attempted_value": jira_key,
                                })
                            else:
                                connection.execute(
                                    """
                                    INSERT INTO task_jira_mappings
                                    (jira_site_key, task_gid, jira_key, synced_at)
                                    VALUES (?, ?, ?, ?)
                                    ON CONFLICT(jira_site_key, task_gid) DO UPDATE SET
                                        jira_key=excluded.jira_key,
                                        synced_at=excluded.synced_at
                                    """,
                                    (args.jira_site, task_gid, jira_key, timestamp.isoformat()),
                                )
                                mapped_tasks.append({
                                    "jira_key": jira_key,
                                    "task_gid": task_gid,
                                })
                        except Exception as exc:
                            logger.error(f"Error mapping task {task_gid}: {exc}")
                            conflicts.append({
                                "conflict_type": "task_mapping_error",
                                "jira_key": jira_key,
                                "task_gid": task_gid,
                                "error": str(exc),
                            })

                        project_details = client.get_project(project_key)
                        project_name = project_details.get("name", "") if project_details else ""

                        for project_gid in project_gids:
                            try:
                                existing_proj = connection.execute(
                                    "SELECT jira_project_key FROM jira_project_mappings WHERE jira_site_key = ? AND project_gid = ?",
                                    (args.jira_site, project_gid),
                                ).fetchone()

                                if existing_proj and existing_proj["jira_project_key"] != project_key:
                                    conflicts.append({
                                        "conflict_type": "jira_project_mappings",
                                        "project_gid": project_gid,
                                        "project_key": project_key,
                                        "existing_value": existing_proj["jira_project_key"],
                                        "attempted_value": project_key,
                                    })
                                else:
                                    connection.execute(
                                        """
                                        INSERT INTO jira_project_mappings
                                        (jira_site_key, project_gid, jira_project_name, jira_project_key, synced_at)
                                        VALUES (?, ?, ?, ?, ?)
                                        ON CONFLICT(jira_site_key, project_gid) DO UPDATE SET
                                            jira_project_name=excluded.jira_project_name,
                                            jira_project_key=excluded.jira_project_key,
                                            synced_at=excluded.synced_at
                                        """,
                                        (args.jira_site, project_gid, project_name, project_key, timestamp.isoformat()),
                                    )
                                    mapped_projects.append({
                                        "project_gid": project_gid,
                                        "project_key": project_key,
                                        "project_name": project_name,
                                    })
                            except Exception as exc:
                                logger.error(f"Error mapping project {project_gid}: {exc}")
                                conflicts.append({
                                    "conflict_type": "project_mapping_error",
                                    "project_gid": project_gid,
                                    "error": str(exc),
                                })

                if not args.dry_run:
                    connection.commit()

                next_page_token = payload.get("nextPageToken")
                if not next_page_token:
                    break

            logger.info(f"Processed {processed} issues total")
            logger.info(f"Mapped {len(mapped_tasks)} tasks")
            logger.info(f"Mapped {len(mapped_projects)} projects")
            logger.info(f"Found {len(orphan_tasks)} orphan tasks")
            logger.info(f"Found {len(conflicts)} conflicts")

            now = datetime.now(timezone.utc)
            timestamp_str = now.strftime("%Y-%m-%dT%H-%M-%S")
            exports_dir = Path("exports")
            exports_dir.mkdir(exist_ok=True)

            if conflicts:
                conflicts_path = exports_dir / f"reverse_lookup_conflicts_{timestamp_str}.csv"
                with open(conflicts_path, "w", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=list(conflicts[0].keys()))
                    writer.writeheader()
                    writer.writerows(conflicts)
                logger.info(f"Wrote conflicts to {conflicts_path}")

            if orphan_tasks:
                orphans_path = exports_dir / f"reverse_lookup_orphans_{timestamp_str}.csv"
                with open(orphans_path, "w", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=list(orphan_tasks[0].keys()))
                    writer.writeheader()
                    writer.writerows(orphan_tasks)
                logger.info(f"Wrote orphans to {orphans_path}")

            summary_path = exports_dir / f"reverse_lookup_summary_{timestamp_str}.csv"
            with open(summary_path, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=["metric", "count"])
                writer.writeheader()
                writer.writerows([
                    {"metric": "Issues processed", "count": processed},
                    {"metric": "Tasks mapped", "count": len(mapped_tasks)},
                    {"metric": "Projects mapped", "count": len(mapped_projects)},
                    {"metric": "Orphan tasks", "count": len(orphan_tasks)},
                    {"metric": "Conflicts", "count": len(conflicts)},
                    {"metric": "Dry run", "count": "Yes" if args.dry_run else "No"},
                ])
            logger.info(f"Wrote summary to {summary_path}")

            if args.dry_run:
                logger.info("DRY RUN: no mappings were written")

        return 0

    except Exception as exc:
        logger.error(f"Fatal error: {exc}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
