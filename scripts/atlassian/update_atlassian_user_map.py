#!/usr/bin/env python3
"""Load Atlassian account IDs from a CSV into the SQLite user mapping table."""

from __future__ import annotations

import argparse
import csv
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional, Tuple

from src.lib.database import Database
from src.lib.logging_utils import configure_logging, resolve_log_level


DEFAULT_DB_PATH = "data/asana.sqlite"
logger = logging.getLogger(__name__)


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--csv",
        required=True,
        help=(
            "Path to the CSV containing mappings (columns: gid, atlassianAccountId, "
            "and optional atlassianEmail)."
        ),
    )
    parser.add_argument(
        "--database",
        default=DEFAULT_DB_PATH,
        help=f"Path to the SQLite database (default: {DEFAULT_DB_PATH}).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging verbosity (default: INFO).",
    )
    return parser.parse_args(argv)


def load_csv_rows(csv_path: Path) -> Iterable[Tuple[str, str, str]]:
    if not csv_path.exists():
        raise SystemExit(f"Mapping CSV not found: {csv_path}")
    with csv_path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        normalized = {name.lower(): name for name in (reader.fieldnames or [])}
        gid_key = normalized.get("gid")
        account_key = normalized.get("atlassianaccountid")
        email_key = normalized.get("atlassianemail")
        if not gid_key or not account_key:
            raise SystemExit("Mapping CSV must include 'gid' and 'atlassianAccountId' columns.")
        fieldnames = reader.fieldnames or []
        for row in reader:
            gid = (row.get(gid_key) or "").strip()
            account_id = (row.get(account_key) or "").strip()
            account_email = (row.get(email_key) or "").strip() if email_key else ""
            yield (
                gid,
                account_id,
                account_email,
            )


def update_mapping(database_path: str, csv_path: Path) -> int:
    database = Database(db_path=database_path)
    connection = database.connect()
    try:
        database.initialize(connection)
        rows = list(load_csv_rows(csv_path))
        synced_at = datetime.now(timezone.utc)
        updated = database.replace_user_atlassian_accounts(connection, rows, synced_at)
        return updated
    finally:
        connection.close()


def main(argv: Optional[Iterable[str]] = None) -> None:
    args = parse_args(argv)
    try:
        log_level = resolve_log_level(args.log_level)
    except ValueError as exc:  # noqa: BLE001
        raise SystemExit(str(exc)) from exc
    configure_logging(log_level)

    csv_path = Path(args.csv)
    updated = update_mapping(args.database, csv_path)
    logger.info("Upserted %s Atlassian account mapping(s) from %s.", updated, csv_path)


if __name__ == "__main__":
    main()
