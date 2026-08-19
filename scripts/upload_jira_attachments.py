#!/usr/bin/env python3
"""Upload a verified local attachment archive to currently mapped Jira work items."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Iterable, Optional

from src.lib.attachment_archive import upload_archive, verify_archive
from src.lib.config import load_credentials, resolve_jira_base_url
from src.lib.database import resolve_default_db_path
from src.lib.jira_client import JiraClient
from src.lib.logging_utils import configure_logging, resolve_log_level
from src.lib.project_scope import read_project_gids_file


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, help="Path to the archive manifest.json.")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--project-gid", help="Upload attachments for one Asana project.")
    scope.add_argument("--project-gids", help="Comma-separated Asana project GIDs to upload.")
    scope.add_argument("--project-gids-file", help="Newline/comma-separated Asana project GID file.")
    parser.add_argument("--database", default=resolve_default_db_path(), help="SQLite database with current Jira mappings.")
    parser.add_argument("--prod", action="store_true", help="Target production Jira; default is sandbox.")
    parser.add_argument("--jira-site", help="Named Jira site key from the active profile.")
    parser.add_argument("--dry-run", action="store_true", help="Resolve destinations and report without Jira calls.")
    parser.add_argument("--workers", type=int, default=2, help="Concurrent upload workers (default: 2).")
    parser.add_argument("--throttle-seconds", type=float, default=0.5, help="Delay before each upload (default: 0.5).")
    parser.add_argument("--log-level", default="INFO", help="Logging verbosity (default: INFO).")
    return parser.parse_args(argv)


def resolve_project_gids(args: argparse.Namespace) -> Optional[list[str]]:
    value = args.project_gids_file and read_project_gids_file(args.project_gids_file)
    value = value or args.project_gids or args.project_gid
    if not value:
        return None
    return list(dict.fromkeys(gid.strip() for gid in value.split(",") if gid.strip()))


def main(argv: Optional[Iterable[str]] = None) -> None:
    args = parse_args(argv)
    configure_logging(resolve_log_level(args.log_level))
    manifest = Path(args.manifest)
    errors = verify_archive(manifest)
    if errors:
        raise SystemExit(f"Archive verification failed for {len(errors)} attachment(s); upload cancelled.")
    credentials = load_credentials()
    if not credentials.atlassian_username or not credentials.atlassian_token:
        raise SystemExit("ATLASSIAN_USERNAME and ATLASSIAN_TOKEN are required.")
    client = JiraClient(
        base_url=resolve_jira_base_url(credentials, prod=args.prod, site_key=args.jira_site),
        username=credentials.atlassian_username,
        token=credentials.atlassian_token,
        skip_ssl_verification=credentials.skip_ssl_verification,
    )
    report = upload_archive(
        database_path=args.database,
        manifest_path=manifest,
        jira_client=client,
        dry_run=args.dry_run,
        workers=max(1, args.workers),
        throttle_seconds=max(0.0, args.throttle_seconds),
        jira_site_key=args.jira_site,
        project_gids=resolve_project_gids(args),
    )
    logging.info("Jira attachment report written to %s", report)


if __name__ == "__main__":
    main()
