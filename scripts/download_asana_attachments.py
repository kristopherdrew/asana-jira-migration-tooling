#!/usr/bin/env python3
"""Create or verify a durable archive of Asana-hosted task attachments."""

from __future__ import annotations

import argparse
import logging
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional

from src.lib.attachment_archive import create_archive, verify_archive
from src.lib.config import load_credentials
from src.lib.database import resolve_default_db_path
from src.lib.logging_utils import configure_logging, resolve_log_level
from src.lib.project_scope import read_project_gids_file


DEFAULT_ARCHIVE_ROOT = "attachment_archives"


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default=resolve_default_db_path(), help="SQLite database path.")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--project-gid", help="Archive attachments for one Asana project.")
    scope.add_argument("--project-gids", help="Comma-separated Asana project GIDs.")
    scope.add_argument("--project-gids-file", help="Newline/comma-separated project GID file.")
    scope.add_argument("--all", action="store_true", help="Archive every active Asana-hosted attachment.")
    parser.add_argument("--task-gid", help="Optionally limit the selected scope to one task.")
    parser.add_argument("--archive-root", default=DEFAULT_ARCHIVE_ROOT, help="Parent directory for archives.")
    parser.add_argument("--archive-id", help="Stable wave ID (default: attachment-wave-YYYYMMDD-HHMMSS).")
    parser.add_argument("--workers", type=int, default=4, help="Concurrent download workers (default: 4).")
    parser.add_argument("--verify-manifest", help="Verify an existing manifest instead of downloading.")
    parser.add_argument("--log-level", default="INFO", help="Logging verbosity (default: INFO).")
    return parser.parse_args(argv)


def resolve_project_gids(args: argparse.Namespace) -> Optional[List[str]]:
    value = args.project_gids
    if args.project_gids_file:
        value = read_project_gids_file(args.project_gids_file)
    if args.project_gid:
        value = args.project_gid
    if value:
        seen = set()
        result = []
        for gid in (part.strip() for part in value.split(",")):
            if gid and gid not in seen:
                seen.add(gid)
                result.append(gid)
        return result
    return None


def main(argv: Optional[Iterable[str]] = None) -> None:
    args = parse_args(argv)
    configure_logging(resolve_log_level(args.log_level))
    if args.verify_manifest:
        errors = verify_archive(Path(args.verify_manifest))
        if errors:
            for row in errors:
                logging.error("Attachment %s: %s", row["attachment_gid"], row["error"])
            raise SystemExit(f"Archive verification failed for {len(errors)} attachment(s).")
        logging.info("Archive verification passed: %s", args.verify_manifest)
        return
    if not any((args.project_gid, args.project_gids, args.project_gids_file, args.all)):
        raise SystemExit("Choose a project scope or pass --all.")
    archive_id = args.archive_id or datetime.now().strftime("attachment-wave-%Y%m%d-%H%M%S")
    logging.info(
        "Starting attachment archive %s (up to %d concurrent downloads).",
        archive_id,
        max(1, args.workers),
    )
    manifest = create_archive(
        database_path=args.database,
        archive_root=Path(args.archive_root),
        archive_id=archive_id,
        credentials=load_credentials(),
        project_gids=resolve_project_gids(args),
        task_gid=args.task_gid,
        workers=max(1, args.workers),
    )
    logging.info("Attachment archive manifest written to %s", manifest)
    errors = verify_archive(manifest)
    if errors:
        raise SystemExit(f"Archive is partial: {len(errors)} attachment(s) require retry.")


if __name__ == "__main__":
    main()
