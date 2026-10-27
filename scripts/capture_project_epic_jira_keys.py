#!/usr/bin/env python3
"""Query Jira for Phase 1 imported project issues and capture their Jira keys."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Iterable, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.lib.config import load_credentials
from src.lib.database import resolve_default_db_path
from src.lib.jira_client import JiraClient
from src.lib.logging_utils import configure_logging, resolve_log_level
from src.teams.export_orchestrator import run_phase_1_capture_keys

DEFAULT_DB_PATH = "data/asana.sqlite"
logger = logging.getLogger(__name__)


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Query Jira for Phase 1 imported projects and capture their keys."
    )
    parser.add_argument(
        "--database",
        default=resolve_default_db_path(DEFAULT_DB_PATH),
        help=f"Path to the SQLite database (default: {DEFAULT_DB_PATH}).",
    )
    parser.add_argument(
        "--jira-site",
        help="Jira site key (e.g., 'prod', 'sandbox'). Required for multi-site environments.",
    )
    parser.add_argument(
        "--jira-project-key",
        required=True,
        help="Jira project key where Phase 1 projects were imported (e.g., 'EST').",
    )
    parser.add_argument(
        "--project-gids",
        help="Comma-separated list of Asana project GIDs to capture. If omitted, queries all projects.",
    )
    parser.add_argument(
        "--project-gids-file",
        help="Path to file with newline-separated project GIDs.",
    )
    parser.add_argument(
        "--path",
        choices=["simple", "hierarchical"],
        default="hierarchical",
        help="Hierarchy path used in Phase 1 export (default: hierarchical).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging level (default: INFO).",
    )
    parser.add_argument(
        "--env",
        help="Path to environment credentials file (overrides default lookup).",
    )
    return parser.parse_args(argv)


def _load_project_gids(
    gids_str: Optional[str],
    gids_file: Optional[str],
    database_path: str,
) -> list[str]:
    """Load project GIDs from argument or file, or default to all in database."""
    if gids_str:
        return [g.strip() for g in gids_str.split(",") if g.strip()]

    if gids_file:
        with open(gids_file) as f:
            return [line.strip() for line in f if line.strip()]

    # Default: get all project GIDs from database
    from src.lib.database import Database

    db = Database(db_path=database_path)
    with db.connect() as conn:
        rows = conn.execute("SELECT gid FROM projects ORDER BY name").fetchall()
        return [row["gid"] for row in rows]


def main(argv: Optional[Iterable[str]] = None) -> None:
    args = parse_args(argv)

    try:
        log_level = resolve_log_level(args.log_level)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    configure_logging(log_level)

    # Load credentials
    try:
        creds = load_credentials(path=args.env) if args.env else load_credentials()
    except Exception as exc:
        logger.error("Failed to load credentials: %s", exc)
        raise SystemExit(1) from exc

    # Resolve Jira client
    try:
        jira_client = JiraClient(
            base_url=creds.get("jira_base_url"),
            token=creds.get("atlassian_token"),
            skip_ssl_verification=creds.get("skip_ssl_verification", False),
        )
    except Exception as exc:
        logger.error("Failed to initialize Jira client: %s", exc)
        raise SystemExit(1) from exc

    # Load project GIDs
    project_gids = _load_project_gids(args.project_gids, args.project_gids_file, args.database)
    if not project_gids:
        logger.error("No project GIDs found")
        raise SystemExit(1)

    logger.info("Capturing keys for %d project(s)", len(project_gids))

    # Capture keys from Jira
    try:
        captured = run_phase_1_capture_keys(
            database_path=args.database,
            jira_client=jira_client,
            jira_project_key=args.jira_project_key,
            project_gids=project_gids,
            path=args.path,
            jira_site_key=args.jira_site,
        )

        if captured:
            logger.info("Successfully captured %d key(s)", len(captured))
            for gid, key in sorted(captured.items())[:10]:  # Show first 10
                logger.info("  %s → %s", gid, key)
            if len(captured) > 10:
                logger.info("  ... and %d more", len(captured) - 10)
        else:
            logger.warning("No keys captured")
            raise SystemExit(1)

    except Exception as exc:
        logger.error("Key capture failed: %s", exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
