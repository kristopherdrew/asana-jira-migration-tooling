#!/usr/bin/env python3
"""Create an anonymized copy of a local SQLite database for safer troubleshooting."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Iterable, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.lib.database import resolve_default_db_path
from src.lib.database_anonymizer import anonymize_database, default_output_path
from src.lib.logging_utils import configure_logging, resolve_log_level


logger = logging.getLogger(__name__)


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        default=resolve_default_db_path(),
        help="Path to the source SQLite database (default: active profile database).",
    )
    parser.add_argument(
        "--output",
        help="Path for the anonymized copy (default: <database>_anonymized.sqlite).",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace the output database if it already exists.",
    )
    parser.add_argument(
        "--salt",
        help=(
            "Optional salt for repeatable anonymized values. Omit this for a fresh private salt "
            "on each run."
        ),
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="Skip the post-anonymization scan for obvious email, URL, and token-like values.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging verbosity (default: INFO).",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Iterable[str]] = None) -> None:
    args = parse_args(argv)
    try:
        log_level = resolve_log_level(args.log_level)
    except ValueError as exc:  # noqa: BLE001
        raise SystemExit(str(exc)) from exc
    configure_logging(log_level)

    source_path = Path(args.database)
    output_path = Path(args.output) if args.output else default_output_path(source_path)
    try:
        report = anonymize_database(
            source_path,
            output_path,
            overwrite=args.overwrite,
            salt=args.salt,
            verify=not args.no_verify,
        )
    except (FileExistsError, FileNotFoundError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc

    logger.info("Wrote anonymized database: %s", report.output_path)
    logger.info("Salt fingerprint: %s", report.salt_fingerprint)
    logger.info(
        "Updated %s distinct value(s) across %s sensitive column(s).",
        report.updated_values,
        len(report.updated_columns),
    )
    if report.remaining_findings:
        logger.warning("Potential sensitive values remain:")
        for finding in report.remaining_findings:
            logger.warning("  - %s", finding)
    else:
        logger.info("Verification scan found no obvious email, URL, or token-like values.")


if __name__ == "__main__":
    main()
