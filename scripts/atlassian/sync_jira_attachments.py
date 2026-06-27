#!/usr/bin/env python3
"""Download Asana attachments for a project and upload them to Jira issues."""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import shutil
import ssl
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple
from urllib import error, request

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(PROJECT_ROOT))

from src.lib.config import Credentials, load_credentials, resolve_jira_base_url
from src.lib.database import Database
from src.lib.jira_client import JiraApiError, JiraClient
from src.lib.logging_utils import configure_logging, resolve_log_level
from src.lib.attachments import jira_unique_attachment_name

logger = logging.getLogger(__name__)
DEFAULT_DB_PATH = "data/asana.sqlite"
DEFAULT_OUTPUT_DIR = "exports"
DEFAULT_WORKERS = 2
DEFAULT_THROTTLE = 0.5


def next_attachment_export_dir(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    index = 1
    while True:
        candidate = root / f"attachment-export-{index:02d}"
        if not candidate.exists():
            return candidate
        index += 1


@dataclass(frozen=True)
class AttachmentJob:
    task_gid: str
    attachment_gid: str
    attachment_name: str
    permanent_url: str
    jira_key: str


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default=DEFAULT_DB_PATH, help="Path to SQLite DB (default: data/asana.sqlite).")
    parser.add_argument("--project-gid", help="Asana project GID to sync attachments for.")
    parser.add_argument(
        "--project-gids",
        help="Comma-separated list of Asana project GIDs to sync attachments for (processed sequentially).",
    )
    parser.add_argument(
        "--prod",
        action="store_true",
        help="Target the production Jira site (JIRA_BASE_URL). Default: sandbox (JIRA_SANDBOX_BASE_URL).",
    )
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="Directory for the CSV summary (default: exports/).")
    parser.add_argument("--task-gid", help="Optional Asana task GID to limit the sync to a single task.")
    parser.add_argument("--max-workers", type=int, default=DEFAULT_WORKERS, help="Concurrent upload workers (default: 2).")
    parser.add_argument("--throttle-seconds", type=float, default=DEFAULT_THROTTLE, help="Delay between uploads per worker to respect Jira rate limits (default: 0.5).")
    parser.add_argument("--dry-run", action="store_true", help="List actions without downloading/uploading attachments.")
    parser.add_argument("--keep-downloads", action="store_true", help="Keep downloaded files on disk for inspection.")
    parser.add_argument("--log-level", default="INFO", help="Logging verbosity (default: INFO).")
    return parser.parse_args(argv)


def resolve_project_gids(single: Optional[str], multiple: Optional[str]) -> List[str]:
    project_gids: List[str] = []
    if multiple:
        project_gids.extend([gid.strip() for gid in multiple.split(",") if gid.strip()])
    if single and single.strip():
        project_gids.append(single.strip())

    # Deduplicate while preserving order
    seen: Set[str] = set()
    normalized: List[str] = []
    for gid in project_gids:
        if gid in seen:
            continue
        seen.add(gid)
        normalized.append(gid)

    if not normalized:
        raise SystemExit("Provide at least one project gid via --project-gid or --project-gids.")
    return normalized


def collect_attachment_jobs(connection, project_gid: str, task_gid: Optional[str] = None) -> List[AttachmentJob]:
    query = [
        "SELECT",
        "    ta.gid AS attachment_gid,",
        "    ta.task_gid,",
        "    ta.name,",
        "    ta.permanent_url",
        "FROM task_attachments ta",
        "JOIN task_projects tp ON tp.task_gid = ta.task_gid",
        "WHERE tp.project_gid = ?",
        "  AND (ta.is_deleted IS NULL OR ta.is_deleted = 0)",
        "  AND ta.permanent_url IS NOT NULL",
    ]
    params: List[str] = [project_gid]
    if task_gid:
        query.append("  AND ta.task_gid = ?")
        params.append(task_gid)
    query.append("ORDER BY ta.task_gid, ta.gid")
    attachment_rows = connection.execute("\n".join(query), tuple(params)).fetchall()
    if not attachment_rows:
        return []
    task_gids = {row["task_gid"] for row in attachment_rows}
    mapping_rows = []
    if task_gids:
        placeholders = ",".join("?" for _ in task_gids)
        mapping_rows = connection.execute(
            f"""
            SELECT task_gid, jira_key
            FROM task_jira_mappings
            WHERE task_gid IN ({placeholders})
            ORDER BY task_gid
            """,
            tuple(task_gids),
        ).fetchall()
    key_map: Dict[str, List[str]] = {}
    for row in mapping_rows:
        key_map.setdefault(row["task_gid"], []).append(row["jira_key"])
    jobs: List[AttachmentJob] = []
    missing_mappings = 0
    for row in attachment_rows:
        keys = key_map.get(row["task_gid"])
        if not keys:
            missing_mappings += 1
            continue
        for jira_key in keys:
            unique_name = jira_unique_attachment_name(row["name"], row["attachment_gid"])
            jobs.append(
                AttachmentJob(
                    task_gid=row["task_gid"],
                    attachment_gid=row["attachment_gid"],
                    attachment_name=unique_name,
                    permanent_url=row["permanent_url"],
                    jira_key=jira_key,
                )
            )
    if missing_mappings:
        logger.warning("%s attachment(s) skipped because no Jira mapping was found.", missing_mappings)
    return jobs


def download_attachment(url: str, destination: Path, token: str, skip_ssl: bool, authenticated: bool) -> None:
    headers = {
        "User-Agent": "asana-jira-attachment-sync/1.0",
    }
    if authenticated:
        headers["Authorization"] = f"Bearer {token}"
    context = ssl.create_default_context()
    if skip_ssl:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    opener = request.build_opener(request.HTTPSHandler(context=context))
    req = request.Request(url, headers=headers)
    try:
        with opener.open(req) as resp, destination.open("wb") as handle:
            shutil.copyfileobj(resp, handle)
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8") if exc.fp else exc.reason
        raise RuntimeError(f"Failed to download attachment ({exc.code}): {body}") from exc


def ensure_atlassian_credentials(creds: Credentials) -> Tuple[str, str]:
    if not creds.atlassian_username or not creds.atlassian_token:
        raise ValueError("Resolved credentials must include 'ATLASSIAN_USERNAME' and 'ATLASSIAN_TOKEN' for attachment sync.")
    return creds.atlassian_username, creds.atlassian_token


def write_report(rows: Sequence[Dict[str, str]], output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "project",
        "project_gid",
        "jira_key",
        "task_gid",
        "attachment_gid",
        "attachment_name",
        "status",
        "message",
    ]
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return output_path


def format_project_label(connection, project_gid: str) -> str:
    row = connection.execute("SELECT name FROM projects WHERE gid = ?", (project_gid,)).fetchone()
    return row["name"] if row and row["name"] else project_gid


def fetch_attachment_download_source(
    attachment_gid: str,
    fallback_url: str,
    token: str,
    skip_ssl: bool,
) -> Tuple[str, bool]:
    api_url = f"https://app.asana.com/api/1.0/attachments/{attachment_gid}"
    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": "asana-jira-attachment-sync/1.0",
    }
    context = ssl.create_default_context()
    if skip_ssl:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    opener = request.build_opener(request.HTTPSHandler(context=context))
    req = request.Request(api_url, headers=headers)
    try:
        with opener.open(req) as resp:
            payload = resp.read().decode("utf-8")
            data = json.loads(payload)
            download_url = data.get("data", {}).get("download_url")
            if download_url:
                logger.debug("Resolved download_url for attachment %s", attachment_gid)
                return download_url, False
    except Exception as exc:  # noqa: BLE001
        logger.debug("Unable to resolve download_url for %s: %s", attachment_gid, exc)
    return fallback_url, True


def sync_project_attachments(
    database_path: str,
    project_gid: str,
    jira_base_url: str,
    output_dir: Path,
    max_workers: int,
    throttle_seconds: float,
    dry_run: bool,
    keep_downloads: bool,
    task_gid: Optional[str] = None,
    allow_empty: bool = False,
) -> Optional[Path]:
    credentials = load_credentials()
    username, jira_token = ensure_atlassian_credentials(credentials)
    database = Database(db_path=database_path)
    logger.info("Loading Asana data from %s", database_path)
    with database.connect() as connection:
        database.initialize(connection)
        project_label = format_project_label(connection, project_gid)
        logger.debug("Collecting attachments for project %s", project_label)
        jobs = collect_attachment_jobs(connection, project_gid, task_gid=task_gid)
        logger.info("Found %s attachment/Jira combinations for project %s", len(jobs), project_label)
        if not jobs:
            message = f"No attachments found for project {project_gid}."
            if allow_empty:
                logger.warning(message + " Skipping.")
                return None
            raise SystemExit(message)

    timestamp = time.strftime("%Y%m%d-%H%M")
    csv_path = Path(output_dir) / f"{project_label.replace(' ', '_')}_attachment_sync_{timestamp}.csv"

    jira_client = JiraClient(
        base_url=jira_base_url,
        username=username,
        token=jira_token,
        skip_ssl_verification=credentials.skip_ssl_verification,
    )

    issue_attachment_cache: Dict[str, Set[str]] = {}
    issue_cache_lock = threading.Lock()

    temp_root = Path(output_dir) / "tmp_attachments"
    temp_root.mkdir(parents=True, exist_ok=True)
    download_dir = Path(tempfile.mkdtemp(prefix="attachments_", dir=str(temp_root)))
    logger.info(
        "Downloading attachments into %s (keep_downloads=%s)",
        download_dir,
        keep_downloads,
    )
    download_cache: Dict[str, Path] = {}
    cache_lock = threading.Lock()
    download_source_cache: Dict[str, Tuple[str, bool]] = {}
    download_source_lock = threading.Lock()
    results: List[Dict[str, str]] = []

    def resolve_download_source(job: AttachmentJob) -> Tuple[str, bool]:
        with download_source_lock:
            cached = download_source_cache.get(job.attachment_gid)
        if cached:
            return cached
        url, requires_auth = fetch_attachment_download_source(
            job.attachment_gid,
            job.permanent_url,
            credentials.token,
            credentials.skip_ssl_verification,
        )
        with download_source_lock:
            download_source_cache[job.attachment_gid] = (url, requires_auth)
        return url, requires_auth

    def get_local_path(job: AttachmentJob) -> Path:
        with cache_lock:
            cached = download_cache.get(job.attachment_gid)
            if cached:
                return cached
        safe_name = job.attachment_name or job.attachment_gid
        safe_name = safe_name.replace(os.sep, "_")
        destination = download_dir / f"{job.attachment_gid}_{safe_name}"
        logger.debug(
            "Downloading attachment %s for task %s -> %s",
            job.attachment_gid,
            job.task_gid,
            destination,
        )
        download_url, requires_auth = resolve_download_source(job)
        download_attachment(
            download_url,
            destination,
            credentials.token,
            credentials.skip_ssl_verification,
            requires_auth,
        )
        with cache_lock:
            download_cache[job.attachment_gid] = destination
        return destination


    def get_issue_attachment_names(jira_key: str) -> Set[str]:
        with issue_cache_lock:
            cached = issue_attachment_cache.get(jira_key)
        if cached is not None:
            return cached
        try:
            names = jira_client.list_issue_attachment_names(jira_key)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Unable to fetch attachments for %s: %s", jira_key, exc)
            names = set()
        with issue_cache_lock:
            issue_attachment_cache[jira_key] = names
        return names

    def worker(job: AttachmentJob) -> Dict[str, str]:
        status = "pending"
        message = ""
        try:
            if dry_run:
                status = "dry-run"
            else:
                local_path = get_local_path(job)
                existing_names = get_issue_attachment_names(job.jira_key)
                if job.attachment_name in existing_names:
                    logger.debug(
                        "Skipping upload of %s to %s (filename already present)",
                        job.attachment_name,
                        job.jira_key,
                    )
                    status = "skipped-existing"
                    if not keep_downloads and local_path.exists():
                        try:
                            local_path.unlink()
                        except OSError:
                            pass
                        with cache_lock:
                            download_cache.pop(job.attachment_gid, None)
                else:
                    logger.debug(
                        "Uploading %s (%s) to Jira issue %s",
                        job.attachment_name,
                        job.attachment_gid,
                        job.jira_key,
                    )
                    time.sleep(throttle_seconds)
                    jira_client.upload_attachment(job.jira_key, job.attachment_name, local_path)
                    with issue_cache_lock:
                        existing_names = issue_attachment_cache.setdefault(job.jira_key, existing_names)
                        existing_names.add(job.attachment_name)
                    status = "uploaded"
        except Exception as exc:  # noqa: BLE001
            status = "error"
            message = str(exc)
            logger.error(
                "Failed to process attachment %s for Jira issue %s: %s",
                job.attachment_gid,
                job.jira_key,
                exc,
            )
        return {
            "project": project_label,
            "project_gid": project_gid,
            "jira_key": job.jira_key,
            "task_gid": job.task_gid,
            "attachment_gid": job.attachment_gid,
            "attachment_name": job.attachment_name,
            "status": status,
            "message": message,
        }

    completed = 0
    log_interval = max(1, len(jobs) // 10)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        logger.info(
            "Starting upload pool with %s worker(s); throttling %.2f seconds between uploads",
            max_workers,
            throttle_seconds,
        )
        future_map = {executor.submit(worker, job): job for job in jobs}
        for future in as_completed(future_map):
            results.append(future.result())
            completed += 1
            if completed % log_interval == 0 or completed == len(jobs):
                logger.info("Processed %s/%s attachment uploads", completed, len(jobs))

    if not keep_downloads:
        shutil.rmtree(download_dir, ignore_errors=True)

    results.sort(key=lambda row: (row["project"], row["jira_key"], row["attachment_name"]))
    return write_report(results, csv_path)


def sync_attachments(
    database_path: str,
    project_gids: Sequence[str],
    jira_base_url: str,
    output_dir: Path,
    max_workers: int,
    throttle_seconds: float,
    dry_run: bool,
    keep_downloads: bool,
    task_gid: Optional[str] = None,
) -> List[Path]:
    # Preserve order while removing duplicates/empties
    seen: Set[str] = set()
    normalized: List[str] = []
    for gid in project_gids:
        trimmed = gid.strip()
        if not trimmed or trimmed in seen:
            continue
        seen.add(trimmed)
        normalized.append(trimmed)

    if not normalized:
        raise ValueError("At least one project GID is required.")

    reports: List[Path] = []
    for gid in normalized:
        report_path = sync_project_attachments(
            database_path=database_path,
            project_gid=gid,
            jira_base_url=jira_base_url,
            output_dir=output_dir,
            max_workers=max_workers,
            throttle_seconds=throttle_seconds,
            dry_run=dry_run,
            keep_downloads=keep_downloads,
            task_gid=task_gid,
            allow_empty=len(normalized) > 1,
        )
        if report_path:
            reports.append(report_path)
    return reports


def main(argv: Optional[Iterable[str]] = None) -> None:
    args = parse_args(argv)
    try:
        log_level = resolve_log_level(args.log_level)
    except ValueError as exc:  # noqa: BLE001
        raise SystemExit(str(exc)) from exc
    configure_logging(log_level)

    try:
        jira_base_url = resolve_jira_base_url(load_credentials(), prod=args.prod)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    project_gids = resolve_project_gids(args.project_gid, args.project_gids)
    # Create a dedicated attachment export folder per run when using the default exports/ path.
    output_root = Path(args.output_dir)
    if output_root == Path(DEFAULT_OUTPUT_DIR):
        output_root = next_attachment_export_dir(output_root)
        logger.info("Using attachment export directory %s", output_root)

    try:
        report_paths = sync_attachments(
            database_path=args.database,
            project_gids=project_gids,
            jira_base_url=jira_base_url,
            output_dir=output_root,
            max_workers=max(args.max_workers, 1),
            throttle_seconds=max(args.throttle_seconds, 0.0),
            dry_run=args.dry_run,
            keep_downloads=args.keep_downloads,
            task_gid=args.task_gid,
        )
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(str(exc)) from exc

    consolidated_path: Optional[Path] = None
    if len(report_paths) > 1:
        timestamp = time.strftime("%Y%m%d-%H%M")
        consolidated_path = output_root / f"attachments_consolidated_{timestamp}.csv"
        fieldnames = [
            "project",
            "project_gid",
            "jira_key",
            "task_gid",
            "attachment_gid",
            "attachment_name",
            "status",
            "message",
        ]
        rows: List[Dict[str, str]] = []
        for path in report_paths:
            with path.open("r", newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                for row in reader:
                    rows.append(row)
        rows.sort(key=lambda r: (r.get("project", ""), r.get("jira_key", ""), r.get("attachment_name", "")))
        with consolidated_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    if not report_paths:
        logger.warning("No attachments found for any provided project. Nothing to do.")
    elif len(report_paths) == 1:
        logger.info("Attachment sync complete. Report written to %s", report_paths[0])
    else:
        joined = ", ".join(str(path) for path in report_paths)
        logger.info("Attachment sync complete for %s projects. Reports: %s", len(report_paths), joined)
        if consolidated_path:
            logger.info("Consolidated attachment report written to %s", consolidated_path)


if __name__ == "__main__":
    main()
