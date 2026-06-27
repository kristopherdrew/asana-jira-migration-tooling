#!/usr/bin/env python3
"""Load Atlassian account IDs, emails, and status from a CSV into SQLite."""

from __future__ import annotations

import argparse
import csv
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from src.lib.database import Database, resolve_default_db_path
from src.lib.logging_utils import configure_logging, resolve_log_level


DEFAULT_DB_PATH = "data/asana.sqlite"
logger = logging.getLogger(__name__)


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--csv",
        help=(
            "Path to the CSV containing mappings (columns: gid, atlassianAccountId, "
            "and optional atlassianEmail / atlassianStatus). Required unless --clear is used."
        ),
    )
    parser.add_argument(
        "--atlassian-export",
        help=(
            "Path to a raw Atlassian user export CSV (columns: 'User id', 'email', "
            "'User status'). Rows are matched to Asana users by email; the Atlassian "
            "account id and status are stored for each match. Other columns are ignored."
        ),
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help=(
            "Delete all rows from user_atlassian_accounts before loading. Use without "
            "an input file to wipe the mappings entirely and start from scratch."
        ),
    )
    parser.add_argument(
        "--database",
        default=resolve_default_db_path(),
        help=f"Path to the SQLite database (default: {DEFAULT_DB_PATH}).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging verbosity (default: INFO).",
    )
    return parser.parse_args(argv)


def load_csv_rows(csv_path: Path) -> Iterable[Tuple[str, str, str, str]]:
    if not csv_path.exists():
        raise SystemExit(f"Mapping CSV not found: {csv_path}")
    with csv_path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        normalized = {name.lower(): name for name in (reader.fieldnames or [])}
        gid_key = normalized.get("gid")
        account_key = normalized.get("atlassianaccountid")
        email_key = normalized.get("atlassianemail")
        status_key = normalized.get("atlassianstatus") or normalized.get("status")
        if not gid_key or not account_key:
            raise SystemExit("Mapping CSV must include 'gid' and 'atlassianAccountId' columns.")
        for row in reader:
            gid = (row.get(gid_key) or "").strip()
            account_id = (row.get(account_key) or "").strip()
            account_email = (row.get(email_key) or "").strip() if email_key else ""
            account_status = (row.get(status_key) or "").strip() if status_key else ""
            yield (
                gid,
                account_id,
                account_email,
                account_status,
            )


def load_atlassian_export_rows(csv_path: Path) -> Iterable[Tuple[str, str, str]]:
    """Yield (email, account_id, status) tuples from a raw Atlassian user export."""
    if not csv_path.exists():
        raise SystemExit(f"Atlassian export CSV not found: {csv_path}")
    with csv_path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        normalized = {name.lower(): name for name in (reader.fieldnames or [])}
        email_key = normalized.get("email")
        account_key = normalized.get("user id") or normalized.get("userid")
        status_key = normalized.get("user status") or normalized.get("status")
        if not email_key or not account_key:
            raise SystemExit(
                "Atlassian export must include 'email' and 'User id' columns."
            )
        for row in reader:
            email = (row.get(email_key) or "").strip()
            account_id = (row.get(account_key) or "").strip()
            status = (row.get(status_key) or "").strip() if status_key else ""
            yield (email, account_id, status)


def _asana_email_to_gid(connection: sqlite3.Connection) -> Dict[str, str]:
    """Map lowercase Asana email -> user gid (first gid wins on duplicates)."""
    email_to_gid: Dict[str, str] = {}
    for row in connection.execute(
        "SELECT gid, email FROM users WHERE email IS NOT NULL AND TRIM(email) <> ''"
    ):
        email = (row["email"] or "").strip().lower()
        if email and email not in email_to_gid:
            email_to_gid[email] = row["gid"]
    return email_to_gid


def resolve_atlassian_export(
    connection: sqlite3.Connection,
    export_rows: Iterable[Tuple[str, str, str]],
) -> Tuple[List[Tuple[str, str, str, str]], Dict[str, int]]:
    """Match export rows to Asana users by email, returning mapping rows and stats."""
    email_to_gid = _asana_email_to_gid(connection)
    rows: List[Tuple[str, str, str, str]] = []
    seen_gids: set[str] = set()
    scanned = 0
    matched = 0
    for email, account_id, status in export_rows:
        scanned += 1
        gid = email_to_gid.get(email.lower())
        if not gid or not account_id:
            continue
        if gid in seen_gids:
            continue
        seen_gids.add(gid)
        matched += 1
        rows.append((gid, account_id, email, status))
    stats = {
        "scanned": scanned,
        "matched": matched,
        "asana_users": len(email_to_gid),
        "asana_unmatched": len(email_to_gid) - matched,
    }
    return rows, stats


def update_mapping(
    database_path: str,
    csv_path: Optional[Path] = None,
    clear: bool = False,
    atlassian_export_path: Optional[Path] = None,
) -> Tuple[int, int]:
    database = Database(db_path=database_path)
    connection = database.connect()
    try:
        database.initialize(connection)
        cleared = database.clear_user_atlassian_accounts(connection) if clear else 0
        updated = 0
        synced_at = datetime.now(timezone.utc)
        if csv_path is not None:
            rows = list(load_csv_rows(csv_path))
            updated += database.replace_user_atlassian_accounts(connection, rows, synced_at)
        if atlassian_export_path is not None:
            rows, stats = resolve_atlassian_export(
                connection, load_atlassian_export_rows(atlassian_export_path)
            )
            updated += database.replace_user_atlassian_accounts(connection, rows, synced_at)
            logger.info(
                "Atlassian export: scanned %s row(s), matched %s Asana user(s) by email; "
                "%s Asana user(s) with email had no match.",
                stats["scanned"],
                stats["matched"],
                stats["asana_unmatched"],
            )
        return cleared, updated
    finally:
        connection.close()


def main(argv: Optional[Iterable[str]] = None) -> None:
    args = parse_args(argv)
    try:
        log_level = resolve_log_level(args.log_level)
    except ValueError as exc:  # noqa: BLE001
        raise SystemExit(str(exc)) from exc
    configure_logging(log_level)

    if not args.clear and not args.csv and not args.atlassian_export:
        raise SystemExit(
            "Provide --csv or --atlassian-export to load mappings, --clear to wipe them, "
            "or a combination."
        )

    csv_path = Path(args.csv) if args.csv else None
    export_path = Path(args.atlassian_export) if args.atlassian_export else None
    cleared, updated = update_mapping(
        args.database, csv_path, clear=args.clear, atlassian_export_path=export_path
    )
    if args.clear:
        logger.info("Cleared %s Atlassian account mapping(s).", cleared)
    if csv_path is not None or export_path is not None:
        logger.info("Upserted %s Atlassian account mapping(s).", updated)


if __name__ == "__main__":
    main()
