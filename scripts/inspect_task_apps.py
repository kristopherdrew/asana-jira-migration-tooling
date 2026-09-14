#!/usr/bin/env python3
"""Read-only probe of task App Components / URL attachments from Asana.

Given a task GID, this script fetches the task's attachment records with fields
that matter for Asana App Components: URLs, host/subtype, and connected_to_app.
It does not write to SQLite. It prints JSON to stdout by default and can also
write the same payload to ``exports/`` for later inspection.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import datetime, timezone
from typing import Iterable, Optional

from src.lib.config import load_credentials
from src.lib.http_client import AsanaHttpClient
from src.lib.logging_utils import configure_logging, resolve_log_level
from src.tasks.task_apps import fetch_task_app_attachment_probe


DEFAULT_CONFIG_ROOT = "."
DEFAULT_OUTPUT_DIR = "exports"
logger = logging.getLogger(__name__)


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-gid", required=True, help="Asana task GID to probe.")
    parser.add_argument(
        "--no-details",
        action="store_true",
        help="Skip per-attachment detail calls; only fetch the task attachment list.",
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help=f"Directory for optional JSON output (default: {DEFAULT_OUTPUT_DIR}).",
    )
    parser.add_argument(
        "--write-file",
        action="store_true",
        help="Also write the JSON payload to output-dir.",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=100,
        help="Asana pagination limit for the attachment list (default: 100).",
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
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    configure_logging(log_level)

    credentials = load_credentials(DEFAULT_CONFIG_ROOT)
    client = AsanaHttpClient(
        token=credentials.token,
        skip_ssl_verification=credentials.skip_ssl_verification,
    )

    logger.info("Probing task %s app-component attachment data (read-only)...", args.task_gid)
    payload = fetch_task_app_attachment_probe(
        client,
        args.task_gid,
        include_details=not args.no_details,
        page_size=args.page_size,
    )
    print(json.dumps(payload, indent=2, ensure_ascii=False))

    if args.write_file:
        os.makedirs(args.output_dir, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        out_path = os.path.join(args.output_dir, f"task_apps_probe_{args.task_gid}_{timestamp}.json")
        with open(out_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
        logger.info("Raw probe data written to %s", out_path)


if __name__ == "__main__":
    main()
