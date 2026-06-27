#!/usr/bin/env python3
"""Search Jira issues for external IDs (e.g., Asana task GIDs) via JQL and store mappings."""

from __future__ import annotations

import argparse
import base64
import json
import logging
import ssl
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple
from urllib import error, request

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.lib.config import Credentials, load_credentials, resolve_jira_base_url
from src.lib.database import Database, DEFAULT_DB_PATH, resolve_default_db_path
from src.lib.logging_utils import configure_logging, resolve_log_level


logger = logging.getLogger(__name__)
DEFAULT_MAX_RESULTS = 500
JIRA_MAX_RESULTS_CAP = 5000  # v3 /search/jql cap per RFC-61
DEFAULT_FIELDS = ("created",)


class JiraSearchError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"Jira search error {status}: {message}")
        self.status = status


@dataclass(frozen=True)
class JiraIssue:
    key: str
    created: str
    external_id: Optional[str]
    meta: Dict[str, object] | None = None


class JiraSearchClient:
    """Minimal client for the Jira v3 search endpoint (POST /rest/api/3/search/jql)."""

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
            "User-Agent": "asana-jira-search/1.0",
        }
        return self._request("POST", "/rest/api/3/search/jql", headers=headers, data=body)

    def list_fields(self) -> List[Dict[str, object]]:
        """Return all Jira fields (GET /rest/api/3/field) for name->id resolution.

        The endpoint returns a bare JSON array, so ``_request`` yields a list here
        rather than the dict returned by the search endpoint.
        """
        headers = {
            "Authorization": self._auth_header,
            "Accept": "application/json",
            "User-Agent": "asana-jira-search/1.0",
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
                    retry_after = self._parse_retry_after(exc)
                    time.sleep(retry_after or (backoff**attempt))
                    last_error = JiraSearchError(exc.code, "Rate limited")
                    continue
                if 500 <= exc.code < 600:
                    time.sleep(backoff**attempt)
                    last_error = JiraSearchError(exc.code, "Server error")
                    continue
                body = exc.read().decode("utf-8") if exc.fp else exc.reason
                raise JiraSearchError(exc.code, body or exc.reason) from exc
            except error.URLError as exc:
                time.sleep(backoff**attempt)
                last_error = exc
        if last_error:
            raise last_error
        raise JiraSearchError(0, "Request failed without response")

    @staticmethod
    def _parse_retry_after(exc: error.HTTPError) -> Optional[float]:
        retry_header = exc.headers.get("Retry-After") if exc.headers else None
        if not retry_header:
            return None
        try:
            return float(retry_header)
        except ValueError:
            return None


def ensure_atlassian_credentials(creds: Credentials) -> Tuple[str, str]:
    if not creds.atlassian_username or not creds.atlassian_token:
        raise ValueError(
            "Resolved credentials must include 'ATLASSIAN_USERNAME' and 'ATLASSIAN_TOKEN' for Jira search."
        )
    return creds.atlassian_username, creds.atlassian_token


def clamp_max_results(value: int) -> int:
    if value < 1:
        logger.warning("maxResults must be positive; defaulting to 1 (received %s).", value)
        return 1
    if value > JIRA_MAX_RESULTS_CAP:
        logger.warning(
            "Requested maxResults=%s exceeds Jira cap (%s); clamping to %s.",
            value,
            JIRA_MAX_RESULTS_CAP,
            JIRA_MAX_RESULTS_CAP,
        )
        return JIRA_MAX_RESULTS_CAP
    return value


def build_jql(base_jql: str, cursor: Optional[JiraIssue]) -> str:
    """Return JQL with deterministic ordering and an optional created cursor.

    Cursor clause is inserted before ORDER BY to keep the query valid:
      (<base without order>) AND <created cutoff> ORDER BY created ASC, issuekey ASC
    """
    cleaned = base_jql.strip().rstrip(";")
    lowered = cleaned.lower()
    if "order by" in lowered:
        cleaned = cleaned[: lowered.index("order by")].strip()
    cursor_clause = ""
    if cursor:
        created_value = cursor.created.replace('"', '\\"')
        keys_at_ts = cursor.meta.get("keys_at_ts", []) if hasattr(cursor, "meta") else []
        if keys_at_ts:
            key_list = ",".join(f"\"{k}\"" for k in keys_at_ts)
            exclusion = f" OR (created = \"{created_value}\" AND issuekey NOT IN ({key_list}))"
        else:
            exclusion = ""
        cursor_clause = f" AND (created > \"{created_value}\"{exclusion})"
    filter_clause = f"({cleaned}){cursor_clause}"
    return f"{filter_clause} ORDER BY created ASC, issuekey ASC"


def extract_issues(payload: Dict, external_field: str) -> List[JiraIssue]:
    results: List[JiraIssue] = []
    for issue in payload.get("issues", []):
        fields = issue.get("fields", {}) or {}
        external_raw = fields.get(external_field)
        external_value: Optional[str]
        if external_raw is None:
            external_value = None
        elif isinstance(external_raw, (str, int, float)):
            external_value = str(external_raw).strip()
        else:
            # Some custom fields return objects; keep a concise representation
            external_value = str(external_raw).strip()
        created = fields.get("created")
        if not created:
            logger.debug("Issue %s missing 'created'; skipping.", issue.get("key"))
            continue
        issue_key = issue.get("key")
        if not issue_key:
            logger.debug("Issue missing key; skipping payload entry.")
            continue
        results.append(JiraIssue(key=issue_key, created=created, external_id=external_value))
    return results


def collect_external_ids(
    client: JiraSearchClient,
    base_jql: str,
    external_field: str,
    max_results: int,
) -> List[JiraIssue]:
    collected: List[JiraIssue] = []
    batch = 1
    cursor: Optional[JiraIssue] = None
    while True:
        next_page_token: Optional[str] = None
        jql_with_cursor = build_jql(base_jql, cursor)
        iteration = 1
        batch_issues: List[JiraIssue] = []
        while True:
            logger.debug(
                "Batch %s Page %s JQL (nextPageToken=%s, cursor=%s)",
                batch,
                iteration,
                next_page_token,
                cursor.key if cursor else None,
            )
            payload = client.search(
                jql=jql_with_cursor,
                fields=[*DEFAULT_FIELDS, external_field],
                max_results=max_results,
                next_page_token=next_page_token,
            )
            issues = extract_issues(payload, external_field)
            if not issues:
                logger.info("No issues returned (batch %s, page %s).", batch, iteration)
                break
            batch_issues.extend(issues)
            collected.extend(issues)
            logger.info(
                "Retrieved %s issues in batch %s page %s (total collected: %s).",
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
        # Track all keys with the max created timestamp in this batch so the cursor can exclude them safely.
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


def load_known_task_gids(connection) -> set[str]:
    rows = connection.execute("SELECT gid FROM tasks").fetchall()
    return {row["gid"] for row in rows}


def upsert_mappings(
    database_path: str,
    issues: Sequence[JiraIssue],
    skip_missing_tasks: bool = True,
) -> Dict[str, int]:
    def chunked(items: Sequence[Dict[str, str]], size: int) -> Iterable[Sequence[Dict[str, str]]]:
        for i in range(0, len(items), size):
            yield items[i : i + size]

    database = Database(db_path=database_path)
    with database.connect() as connection:
        database.initialize(connection)
        known_tasks = load_known_task_gids(connection)
        filtered: List[Dict[str, str]] = []
        missing: List[JiraIssue] = []
        for issue in issues:
            if not issue.external_id:
                continue
            if known_tasks and issue.external_id not in known_tasks:
                missing.append(issue)
                if skip_missing_tasks:
                    continue
            filtered.append({"jira_key": issue.key, "task_gid": issue.external_id})
        written = 0
        now = datetime.now(timezone.utc)
        # SQLite parameter limit is ~999; chunk to stay well below that (400 rows per batch).
        for batch in chunked(filtered, 400):
            partial = database.upsert_task_jira_mappings(connection, batch, now)
            written += partial.get("written", 0)
    if missing:
        logger.warning(
            "Skipped %s issue(s) whose external IDs were not found in the tasks table.",
            len(missing),
        )
    return {
        "processed": len(issues),
        "written": written,
        "skipped": len(issues) - written,
    }


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--prod",
        action="store_true",
        help="Target the production Jira site (JIRA_BASE_URL). Default: sandbox (JIRA_SANDBOX_BASE_URL).",
    )
    parser.add_argument(
        "--jql",
        required=True,
        help="JQL filter to select issues that carry external IDs (ORDER BY created ASC is applied automatically).",
    )
    parser.add_argument(
        "--external-id-field",
        required=True,
        help="Field key that stores the external ID (e.g., customfield_12345).",
    )
    parser.add_argument(
        "--max-results",
        type=int,
        default=DEFAULT_MAX_RESULTS,
        help=f"maxResults value per search call (default {DEFAULT_MAX_RESULTS}; capped at {JIRA_MAX_RESULTS_CAP}). Larger sets are fetched via nextPageToken paging plus created-timestamp batching.",
    )
    parser.add_argument(
        "--database",
        default=resolve_default_db_path(),
        help="Path to SQLite DB for writing mappings (default: data/asana.sqlite).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and display mappings without writing them to the database.",
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
        base_url = resolve_jira_base_url(credentials, prod=args.prod)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    max_results = clamp_max_results(args.max_results)
    logger.info(
        "Starting Jira search (base=%s, maxResults=%s, dry_run=%s)",
        base_url,
        max_results,
        args.dry_run,
    )
    client = JiraSearchClient(
        base_url=base_url,
        username=username,
        token=jira_token,
        skip_ssl_verification=credentials.skip_ssl_verification,
    )

    issues = collect_external_ids(
        client=client,
        base_jql=args.jql,
        external_field=args.external_id_field,
        max_results=max_results,
    )
    if not issues:
        logger.warning("No issues matched the provided JQL.")
        return

    if args.dry_run:
        logger.info("Dry run enabled; not writing to DB. Found %s issue(s).", len(issues))
        for issue in issues:
            logger.info("Jira: %s | Created: %s | External ID: %s", issue.key, issue.created, issue.external_id or "<missing>")
        return

    summary = upsert_mappings(args.database, issues)
    logger.info(
        "Import complete: processed=%s written=%s skipped=%s",
        summary["processed"],
        summary["written"],
        summary["skipped"],
    )


if __name__ == "__main__":
    main()
