"""Durable Asana attachment archives and delayed Jira delivery."""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import os
import shutil
import ssl
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple
from urllib import error, request

from src.lib.attachments import jira_unique_attachment_name
from src.lib.config import Credentials
from src.lib.database import Database
from src.lib.jira_client import JiraClient


MANIFEST_VERSION = 1
DOWNLOAD_TIMEOUT_SECONDS = 60

LOGGER = logging.getLogger(__name__)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class AttachmentSource:
    attachment_gid: str
    task_gid: str
    original_name: str
    jira_name: str
    expected_size: Optional[int]
    permanent_url: str


@dataclass(frozen=True)
class ArchiveFile:
    attachment_gid: str
    task_gid: str
    original_name: str
    jira_name: str
    relative_path: str
    expected_size: Optional[int]
    actual_size: Optional[int]
    sha256: Optional[str]
    status: str
    downloaded_at: Optional[str]
    error: str


def collect_asana_attachment_sources(
    connection,
    project_gids: Optional[Sequence[str]] = None,
    task_gid: Optional[str] = None,
) -> List[AttachmentSource]:
    """Return active, Asana-hosted attachments, deduplicated by attachment GID."""
    query = [
        "SELECT ta.gid, ta.task_gid, ta.name, ta.size, ta.permanent_url",
        "FROM task_attachments ta",
        "WHERE COALESCE(ta.is_deleted, 0) = 0",
        "  AND ta.host = 'asana'",
        "  AND ta.permanent_url IS NOT NULL",
    ]
    params: List[str] = []
    if project_gids:
        placeholders = ",".join("?" for _ in project_gids)
        query.append(
            "  AND EXISTS (SELECT 1 FROM task_projects tp "
            f"WHERE tp.task_gid = ta.task_gid AND tp.project_gid IN ({placeholders}))"
        )
        params.extend(project_gids)
    if task_gid:
        query.append("  AND ta.task_gid = ?")
        params.append(task_gid)
    query.append("ORDER BY ta.task_gid, ta.gid")
    rows = connection.execute("\n".join(query), tuple(params)).fetchall()
    return [
        AttachmentSource(
            attachment_gid=row["gid"],
            task_gid=row["task_gid"],
            original_name=row["name"] or row["gid"],
            jira_name=jira_unique_attachment_name(row["name"], row["gid"]),
            expected_size=row["size"],
            permanent_url=row["permanent_url"],
        )
        for row in rows
    ]


def fetch_download_source(
    attachment_gid: str,
    fallback_url: str,
    token: str,
    skip_ssl: bool,
) -> Tuple[str, bool]:
    api_url = f"https://app.asana.com/api/1.0/attachments/{attachment_gid}"
    context = ssl.create_default_context()
    if skip_ssl:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    req = request.Request(
        api_url,
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": "asana-jira-attachment-archive/1.0",
        },
    )
    try:
        with request.build_opener(request.HTTPSHandler(context=context)).open(
            req, timeout=DOWNLOAD_TIMEOUT_SECONDS
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
        download_url = payload.get("data", {}).get("download_url")
        if download_url:
            return download_url, False
    except Exception:  # noqa: BLE001 - the authenticated permanent URL is the fallback
        pass
    return fallback_url, True


def download_file(
    url: str,
    destination: Path,
    token: str,
    skip_ssl: bool,
    authenticated: bool,
) -> None:
    headers = {"User-Agent": "asana-jira-attachment-archive/1.0"}
    if authenticated:
        headers["Authorization"] = f"Bearer {token}"
    context = ssl.create_default_context()
    if skip_ssl:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    req = request.Request(url, headers=headers)
    try:
        with request.build_opener(request.HTTPSHandler(context=context)).open(
            req, timeout=DOWNLOAD_TIMEOUT_SECONDS
        ) as response:
            with destination.open("wb") as handle:
                shutil.copyfileobj(response, handle)
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if exc.fp else str(exc.reason)
        raise RuntimeError(f"Failed to download attachment ({exc.code}): {body}") from exc


def _safe_gid(value: str) -> str:
    return "".join(char if char.isalnum() or char in "-_" else "_" for char in value)


def _archive_file_path(archive_dir: Path, source: AttachmentSource) -> Tuple[Path, str]:
    relative = Path("files") / _safe_gid(source.attachment_gid) / source.jira_name
    return archive_dir / relative, relative.as_posix()


def _write_manifest(
    archive_dir: Path,
    archive_id: str,
    workspace_gid: Optional[str],
    scope: Dict,
    files: Sequence[ArchiveFile],
    created_at: str,
) -> Tuple[Path, str]:
    payload = {
        "manifest_version": MANIFEST_VERSION,
        "archive_id": archive_id,
        "workspace_gid": workspace_gid,
        "created_at": created_at,
        "scope": scope,
        "attachment_count": len(files),
        "ready_count": sum(item.status == "ready" for item in files),
        "total_bytes": sum(item.actual_size or 0 for item in files if item.status == "ready"),
        "files": [asdict(item) for item in files],
    }
    manifest_path = archive_dir / "manifest.json"
    encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    manifest_path.write_bytes(encoded)
    try:
        manifest_path.chmod(0o600)
    except OSError:
        pass
    manifest_hash = hashlib.sha256(encoded).hexdigest()

    csv_path = archive_dir / "manifest.csv"
    fieldnames = list(asdict(files[0]).keys()) if files else [
        "attachment_gid", "task_gid", "original_name", "jira_name", "relative_path",
        "expected_size", "actual_size", "sha256", "status", "downloaded_at", "error",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(asdict(item) for item in files)
    try:
        csv_path.chmod(0o600)
    except OSError:
        pass

    checksums_path = archive_dir / "checksums.sha256"
    with checksums_path.open("w", encoding="utf-8") as handle:
        for item in files:
            if item.status == "ready" and item.sha256:
                handle.write(f"{item.sha256}  {item.relative_path}\n")
    try:
        checksums_path.chmod(0o600)
    except OSError:
        pass
    return manifest_path, manifest_hash


def _record_archive_file(connection, archive_id: str, item: ArchiveFile) -> None:
    connection.execute(
        """
        INSERT INTO attachment_archive_files (
            archive_id, attachment_gid, task_gid, original_name, jira_name,
            relative_path, expected_size, actual_size, sha256, status,
            downloaded_at, last_verified_at, error
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(archive_id, attachment_gid) DO UPDATE SET
            task_gid=excluded.task_gid, original_name=excluded.original_name,
            jira_name=excluded.jira_name, relative_path=excluded.relative_path,
            expected_size=excluded.expected_size, actual_size=excluded.actual_size,
            sha256=excluded.sha256, status=excluded.status,
            downloaded_at=excluded.downloaded_at,
            last_verified_at=excluded.last_verified_at, error=excluded.error
        """,
        (
            archive_id, item.attachment_gid, item.task_gid, item.original_name,
            item.jira_name, item.relative_path, item.expected_size, item.actual_size,
            item.sha256, item.status, item.downloaded_at,
            item.downloaded_at if item.status == "ready" else None, item.error,
        ),
    )


def create_archive(
    database_path: str,
    archive_root: Path,
    archive_id: str,
    credentials: Credentials,
    project_gids: Optional[Sequence[str]] = None,
    task_gid: Optional[str] = None,
    workers: int = 4,
) -> Path:
    """Download and verify a resumable Asana-only attachment archive."""
    database = Database(database_path)
    archive_dir = archive_root / archive_id
    archive_dir.mkdir(parents=True, exist_ok=True)
    try:
        archive_dir.chmod(0o700)
    except OSError:
        pass
    created_at = utc_now()
    scope = {"project_gids": list(project_gids or []), "all": not project_gids, "task_gid": task_gid}
    with database.connect() as connection:
        database.initialize(connection)
        sources = collect_asana_attachment_sources(connection, project_gids, task_gid)
        existing = connection.execute(
            "SELECT archive_path FROM attachment_archives WHERE archive_id = ?",
            (archive_id,),
        ).fetchone()
        if existing and Path(existing["archive_path"]).resolve() != archive_dir.resolve():
            raise ValueError(f"Archive ID {archive_id!r} is already registered at {existing['archive_path']}")
        connection.execute(
            """
            INSERT INTO attachment_archives (
                archive_id, workspace_gid, archive_path, scope_json, status, created_at
            ) VALUES (?, ?, ?, ?, 'downloading', ?)
            ON CONFLICT(archive_id) DO UPDATE SET
                workspace_gid=excluded.workspace_gid, archive_path=excluded.archive_path,
                scope_json=excluded.scope_json, status='downloading'
            """,
            (archive_id, credentials.workspace_gid, str(archive_dir.resolve()), json.dumps(scope), created_at),
        )
        connection.commit()

    LOGGER.info(
        "Archive %s: %d Asana-hosted attachment(s) selected; downloading to %s",
        archive_id,
        len(sources),
        archive_dir,
    )

    def process(source: AttachmentSource) -> ArchiveFile:
        destination, relative = _archive_file_path(archive_dir, source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            actual_size = destination.stat().st_size
            if source.expected_size is None or actual_size == source.expected_size:
                return ArchiveFile(
                    source.attachment_gid, source.task_gid, source.original_name,
                    source.jira_name, relative, source.expected_size, actual_size,
                    sha256_file(destination), "ready", utc_now(), "",
                )
        part_path = destination.with_name(destination.name + ".part")
        try:
            part_path.unlink(missing_ok=True)
            download_url, authenticated = fetch_download_source(
                source.attachment_gid,
                source.permanent_url,
                credentials.token,
                credentials.skip_ssl_verification,
            )
            download_file(
                download_url, part_path, credentials.token,
                credentials.skip_ssl_verification, authenticated,
            )
            actual_size = part_path.stat().st_size
            if source.expected_size is not None and actual_size != source.expected_size:
                raise ValueError(
                    f"Size mismatch: expected {source.expected_size} bytes, received {actual_size}"
                )
            checksum = sha256_file(part_path)
            os.replace(part_path, destination)
            try:
                destination.chmod(0o600)
            except OSError:
                pass
            return ArchiveFile(
                source.attachment_gid, source.task_gid, source.original_name,
                source.jira_name, relative, source.expected_size, actual_size,
                checksum, "ready", utc_now(), "",
            )
        except Exception as exc:  # noqa: BLE001 - every file failure belongs in the manifest
            part_path.unlink(missing_ok=True)
            return ArchiveFile(
                source.attachment_gid, source.task_gid, source.original_name,
                source.jira_name, relative, source.expected_size, None, None,
                "error", None, str(exc),
            )

    files: List[ArchiveFile] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = [executor.submit(process, source) for source in sources]
        total = len(futures)
        for completed, future in enumerate(as_completed(futures), start=1):
            files.append(future.result())
            if completed == total or completed == 1 or completed % 25 == 0:
                LOGGER.info(
                    "Archive %s: downloaded %d/%d attachment(s)",
                    archive_id,
                    completed,
                    total,
                )
    files.sort(key=lambda item: (item.task_gid, item.attachment_gid))

    manifest_path, manifest_hash = _write_manifest(
        archive_dir, archive_id, credentials.workspace_gid, scope, files, created_at,
    )
    ready = [item for item in files if item.status == "ready"]
    status = "complete" if len(ready) == len(files) else "partial"
    with database.connect() as connection:
        database.initialize(connection)
        for item in files:
            _record_archive_file(connection, archive_id, item)
        connection.execute(
            """
            UPDATE attachment_archives
            SET status = ?, attachment_count = ?, total_bytes = ?,
                manifest_sha256 = ?, completed_at = ?
            WHERE archive_id = ?
            """,
            (
                status, len(files), sum(item.actual_size or 0 for item in ready),
                manifest_hash, utc_now(), archive_id,
            ),
        )
        connection.commit()
    LOGGER.info(
        "Archive %s: %s (%d/%d attachment(s) ready)",
        archive_id,
        status,
        len(ready),
        len(files),
    )
    return manifest_path


def load_manifest(manifest_path: Path) -> Dict:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if payload.get("manifest_version") != MANIFEST_VERSION:
        raise ValueError(f"Unsupported attachment manifest version: {payload.get('manifest_version')}")
    return payload


def verify_archive(manifest_path: Path) -> List[Dict[str, str]]:
    """Return verification rows; an empty list means the archive is intact."""
    payload = load_manifest(manifest_path)
    archive_dir = manifest_path.parent
    errors: List[Dict[str, str]] = []
    for item in payload.get("files", []):
        if item.get("status") != "ready":
            errors.append({"attachment_gid": item.get("attachment_gid", ""), "error": item.get("error") or "not ready"})
            continue
        path = archive_dir / item["relative_path"]
        if not path.is_file():
            errors.append({"attachment_gid": item["attachment_gid"], "error": "file missing"})
        elif path.stat().st_size != item.get("actual_size"):
            errors.append({"attachment_gid": item["attachment_gid"], "error": "size mismatch"})
        elif sha256_file(path) != item.get("sha256"):
            errors.append({"attachment_gid": item["attachment_gid"], "error": "checksum mismatch"})
    return errors


def _record_upload(
    connection,
    archive_id: str,
    attachment_gid: str,
    jira_key: str,
    status: str,
    uploaded_size: Optional[int],
    jira_attachment_id: Optional[str],
    error_message: str,
    jira_site_key: Optional[str] = None,
) -> None:
    jira_site_key = jira_site_key or "legacy_unknown"
    connection.execute(
        """
        INSERT INTO attachment_jira_uploads (
            archive_id, attachment_gid, jira_key, jira_attachment_id,
            uploaded_size, status, uploaded_at, error, jira_site_key
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(archive_id, attachment_gid, jira_site_key, jira_key) DO UPDATE SET
            jira_attachment_id=excluded.jira_attachment_id,
            uploaded_size=excluded.uploaded_size, status=excluded.status,
            uploaded_at=excluded.uploaded_at, error=excluded.error
        """,
        (
            archive_id, attachment_gid, jira_key, jira_attachment_id,
            uploaded_size, status, utc_now() if status == "uploaded" else None,
            error_message, jira_site_key,
        ),
    )


def upload_archive(
    database_path: str,
    manifest_path: Path,
    jira_client: JiraClient,
    dry_run: bool = True,
    workers: int = 2,
    throttle_seconds: float = 0.5,
    jira_site_key: Optional[str] = None,
) -> Path:
    """Resolve current task mappings and upload verified archive files to Jira."""
    payload = load_manifest(manifest_path)
    archive_id = payload["archive_id"]
    archive_dir = manifest_path.parent
    database = Database(database_path)
    ready_files = [item for item in payload.get("files", []) if item.get("status") == "ready"]
    task_gids = sorted({item["task_gid"] for item in ready_files})
    mappings: Dict[str, List[str]] = {}
    with database.connect() as connection:
        database.initialize(connection)
        scope = payload.get("scope", {})
        connection.execute(
            """
            INSERT INTO attachment_archives (
                archive_id, workspace_gid, archive_path, scope_json, status,
                attachment_count, total_bytes, manifest_sha256, created_at, completed_at
            ) VALUES (?, ?, ?, ?, 'imported', ?, ?, ?, ?, ?)
            ON CONFLICT(archive_id) DO UPDATE SET archive_path=excluded.archive_path
            """,
            (
                archive_id, payload.get("workspace_gid"), str(archive_dir.resolve()),
                json.dumps(scope), payload.get("attachment_count", len(ready_files)),
                payload.get("total_bytes", 0), sha256_file(manifest_path),
                payload.get("created_at") or utc_now(), utc_now(),
            ),
        )
        for item in payload.get("files", []):
            _record_archive_file(
                connection,
                archive_id,
                ArchiveFile(
                    attachment_gid=item["attachment_gid"],
                    task_gid=item["task_gid"],
                    original_name=item.get("original_name", ""),
                    jira_name=item["jira_name"],
                    relative_path=item["relative_path"],
                    expected_size=item.get("expected_size"),
                    actual_size=item.get("actual_size"),
                    sha256=item.get("sha256"),
                    status=item.get("status", "error"),
                    downloaded_at=item.get("downloaded_at"),
                    error=item.get("error", ""),
                ),
            )
        if task_gids:
            placeholders = ",".join("?" for _ in task_gids)
            site_clause = "jira_site_key = ? AND " if jira_site_key else ""
            params = ((jira_site_key,) if jira_site_key else ()) + tuple(task_gids)
            rows = connection.execute(
                f"SELECT task_gid, jira_key FROM task_jira_mappings WHERE {site_clause}task_gid IN ({placeholders})",
                params,
            ).fetchall()
            for row in rows:
                mappings.setdefault(row["task_gid"], []).append(row["jira_key"])
        connection.commit()

    jobs = [(item, key) for item in ready_files for key in mappings.get(item["task_gid"], [])]
    mapped_gids = {item["attachment_gid"] for item, _key in jobs}
    results: List[Dict[str, str]] = []
    for item in ready_files:
        if item["attachment_gid"] not in mapped_gids:
            results.append({
                "attachment_gid": item["attachment_gid"], "task_gid": item["task_gid"],
                "jira_key": "", "attachment_name": item["jira_name"],
                "status": "missing-mapping", "message": "No current Jira mapping for task",
            })

    issue_names: Dict[str, Set[str]] = {}
    issue_lock = threading.Lock()

    def existing_names(jira_key: str) -> Set[str]:
        with issue_lock:
            cached = issue_names.get(jira_key)
        if cached is not None:
            return cached
        names = jira_client.list_issue_attachment_names(jira_key)
        with issue_lock:
            issue_names[jira_key] = names
        return names

    def worker(job) -> Tuple[Dict[str, str], Optional[int], Optional[str]]:
        item, jira_key = job
        path = archive_dir / item["relative_path"]
        base = {
            "attachment_gid": item["attachment_gid"], "task_gid": item["task_gid"],
            "jira_key": jira_key, "attachment_name": item["jira_name"],
        }
        try:
            if not path.is_file() or path.stat().st_size != item["actual_size"] or sha256_file(path) != item["sha256"]:
                return ({**base, "status": "archive-error", "message": "Local size/checksum verification failed"}, None, None)
            if dry_run:
                return ({**base, "status": "dry-run", "message": ""}, None, None)
            names = existing_names(jira_key)
            if item["jira_name"] in names:
                return ({**base, "status": "skipped-existing", "message": ""}, item["actual_size"], None)
            time.sleep(max(0.0, throttle_seconds))
            response = jira_client.upload_attachment(jira_key, item["jira_name"], path) or {}
            with issue_lock:
                issue_names.setdefault(jira_key, names).add(item["jira_name"])
            return (
                {**base, "status": "uploaded", "message": ""},
                int(response.get("size", item["actual_size"])),
                str(response["id"]) if response.get("id") is not None else None,
            )
        except Exception as exc:  # noqa: BLE001 - preserve per-file retry information
            return ({**base, "status": "error", "message": str(exc)}, None, None)

    completed: List[Tuple[Dict[str, str], Optional[int], Optional[str]]] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = [executor.submit(worker, job) for job in jobs]
        for future in as_completed(futures):
            completed.append(future.result())
    results.extend(row for row, _size, _jira_id in completed)
    results.sort(key=lambda row: (row["jira_key"], row["task_gid"], row["attachment_gid"]))

    with database.connect() as connection:
        database.initialize(connection)
        for row, size, jira_id in completed:
            _record_upload(
                connection, archive_id, row["attachment_gid"], row["jira_key"],
                row["status"], size, jira_id, row["message"], jira_site_key,
            )
        connection.commit()

    reports_dir = archive_dir / "reports"
    reports_dir.mkdir(exist_ok=True)
    suffix = datetime.now().strftime("%Y%m%d-%H%M%S")
    report_path = reports_dir / f"jira_upload_{suffix}.csv"
    fieldnames = ["attachment_gid", "task_gid", "jira_key", "attachment_name", "status", "message"]
    with report_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)
    return report_path
