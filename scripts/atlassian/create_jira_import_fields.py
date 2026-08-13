#!/usr/bin/env python3
"""Create one standard Jira field set for Asana import or migration tracking.

The command is idempotent: correctly typed existing fields are retained, missing
fields are created, and conflicting same-name fields stop the operation. Creating
site-wide Jira fields requires Jira administrator permission.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Iterable, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.atlassian.create_team_project_jira_issues import ensure_atlassian_credentials
from src.lib.config import load_credentials, resolve_jira_base_url
from src.lib.jira_client import JiraClient
from src.lib.jira_import_fields import FIELD_SETS, ensure_standard_import_fields
from src.lib.logging_utils import configure_logging, resolve_log_level


logger = logging.getLogger(__name__)


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prod", action="store_true", help="Target the production Jira site. Default: sandbox.")
    parser.add_argument("--jira-site", help="Named Jira site key from the active profile.")
    parser.add_argument(
        "--field-set",
        choices=sorted(FIELD_SETS),
        default="import",
        help="Field set to create: import mapping fields or team/project tracking fields (default: import).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Show missing fields without creating them.")
    parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt.")
    parser.add_argument("--log-level", default="INFO", help="Logging verbosity (default: INFO).")
    return parser.parse_args(argv)


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = parse_args(argv)
    configure_logging(resolve_log_level(args.log_level))
    credentials = load_credentials()
    username, token = ensure_atlassian_credentials(credentials)
    base_url = resolve_jira_base_url(credentials, prod=args.prod, site_key=args.jira_site)

    if not args.dry_run and not args.yes:
        answer = input(f"Create missing {args.field_set} fields on {base_url}? [y/N]: ").strip().casefold()
        if answer not in {"y", "yes"}:
            logger.info("No fields were created.")
            return 0

    client = JiraClient(
        base_url,
        username,
        token,
        skip_ssl_verification=credentials.skip_ssl_verification,
    )
    results = ensure_standard_import_fields(
        client,
        dry_run=args.dry_run,
        definitions=FIELD_SETS[args.field_set],
    )
    for result in results:
        field_id = f" ({result['id']})" if result["id"] else ""
        logger.info("%-8s %s%s", result["status"].upper(), result["name"], field_id)
        if result.get("options"):
            logger.info("         Added options: %s", result["options"])
    logger.info(
        "Field setup complete: %s existing, %s created, %s planned.",
        sum(row["status"] == "existing" for row in results),
        sum(row["status"] == "created" for row in results),
        sum(row["status"] == "planned" for row in results),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
