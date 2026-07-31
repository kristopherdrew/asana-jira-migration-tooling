#!/usr/bin/env python3
"""Add a service user to a list of Asana projects."""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.lib.config import load_credentials
from src.lib.database import Database, resolve_default_db_path
from src.lib.http_client import AsanaHttpClient
from src.lib.logging_utils import configure_logging, resolve_log_level
from src.tasks.users import resolve_service_user_gid


DEFAULT_OUTPUT_DIR = "exports"
DEFAULT_CONFIG_ROOT = "."
PROGRESS_INTERVAL = 10
logger = logging.getLogger(__name__)


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        default=resolve_default_db_path(),
        help="Path to the SQLite database (default: data/asana.sqlite).",
    )
    parser.add_argument(
        "--user-gid",
        help=(
            "Service user GID to add to each project. If omitted, defaults to the token's "
            "own account (via GET /users/me)."
        ),
    )
    parser.add_argument(
        "--project-gid",
        action="append",
        help="Project GID to update. May be repeated.",
    )
    parser.add_argument(
        "--project-gids",
        help="Comma-separated list of project GIDs to update.",
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for failure exports (default: exports/).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging verbosity (default: INFO).",
    )
    return parser.parse_args(argv)


def prompt(text: str, default: Optional[str] = None) -> str:
    suffix = f" [{default}]" if default is not None else ""
    value = input(f"{text}{suffix}: ").strip()
    if value == "" and default is not None:
        return default
    return value


def prompt_required(text: str, default: Optional[str] = None) -> str:
    while True:
        value = prompt(text, default=default)
        if value:
            return value
        print("A value is required.")


def resolve_project_gids(single: Optional[Sequence[str]], multiple: Optional[str]) -> List[str]:
    project_gids: List[str] = []
    if multiple:
        project_gids.extend(gid.strip() for gid in multiple.split(",") if gid.strip())
    if single:
        project_gids.extend(gid.strip() for gid in single if gid.strip())

    seen = set()
    normalized: List[str] = []
    for gid in project_gids:
        if gid in seen:
            continue
        seen.add(gid)
        normalized.append(gid)

    if not normalized:
        raise SystemExit("Provide at least one project GID via --project-gid or --project-gids.")
    return normalized


def build_add_members_payload(user_gid: str) -> dict:
    return {"data": {"members": user_gid}}


def add_service_user_to_projects(
    client: AsanaHttpClient,
    user_gid: str,
    project_gids: Sequence[str],
) -> Tuple[int, List[Tuple[str, str]]]:
    payload = build_add_members_payload(user_gid)
    success_count = 0
    failures: List[Tuple[str, str]] = []

    for index, project_gid in enumerate(project_gids, start=1):
        try:
            client.post_json(f"/projects/{project_gid}/addMembers", payload)
            success_count += 1
            if index == 1 or index % PROGRESS_INTERVAL == 0:
                logger.info("Processed %s project(s)...", index)
        except Exception as exc:  # noqa: BLE001
            error_text = str(exc)
            failures.append((project_gid, error_text))
            logger.warning("Failed to add service user %s to project %s: %s", user_gid, project_gid, error_text)

    return success_count, failures


def write_failure_report(output_dir: Path, failed_projects: Sequence[Tuple[str, str]]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_path = output_dir / f"service_user_project_add_failures_{timestamp}.csv"
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["project_gid", "error"])
        writer.writeheader()
        for project_gid, error in failed_projects:
            writer.writerow({"project_gid": project_gid, "error": error})
    return output_path


def main(argv: Optional[Iterable[str]] = None) -> None:
    args = parse_args(argv)
    try:
        log_level = resolve_log_level(args.log_level)
    except ValueError as exc:  # noqa: BLE001
        raise SystemExit(str(exc)) from exc
    configure_logging(log_level)

    credentials = load_credentials(DEFAULT_CONFIG_ROOT)
    client = AsanaHttpClient(
        token=credentials.token,
        skip_ssl_verification=credentials.skip_ssl_verification,
    )
    project_gids = resolve_project_gids(args.project_gid, args.project_gids)

    if args.user_gid:
        user_gid = args.user_gid
    else:
        database = Database(db_path=args.database)
        with database.connect() as connection:
            database.initialize(connection)
            try:
                user_gid = resolve_service_user_gid(
                    client, database, connection, datetime.now(timezone.utc)
                )
                logger.info("Using token account (gid %s) as the service user.", user_gid)
            except Exception as exc:  # noqa: BLE001 - fall back to the prompt on any failure
                logger.warning(
                    "Could not resolve the token account via /users/me (%s); prompting instead.",
                    exc,
                )
                user_gid = prompt_required("Service user GID")

    success_count, failures = add_service_user_to_projects(client, user_gid, project_gids)

    logger.info("Added service user %s to %s project(s).", user_gid, success_count)
    if failures:
        report_path = write_failure_report(Path(args.output_dir), failures)
        raise SystemExit(f"{len(failures)} project(s) failed to update. See {report_path}.")


if __name__ == "__main__":
    main()
