#!/usr/bin/env python3
"""Read-only probe of Asana project-level status updates and related resources.

Given a single project GID, this script calls a set of candidate Asana endpoints
(status updates, per-update detail, nested stories, the deprecated project_statuses
endpoint) and reports what each returns. It does NOT write to the database; its only
side effect is a JSON dump under ``exports/`` so the raw response shapes can be
inspected before committing to a storage schema.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from src.lib.config import load_credentials
from src.lib.http_client import AsanaHttpClient, AsanaHttpError
from src.lib.logging_utils import configure_logging, resolve_log_level


DEFAULT_CONFIG_ROOT = "."
DEFAULT_OUTPUT_DIR = "exports"

STATUS_UPDATE_FIELDS = [
    "gid",
    "title",
    "status_type",
    "text",
    "html_text",
    "created_at",
    "created_by.gid",
    "created_by.name",
    "modified_at",
    "resource_subtype",
    "num_likes",
    "hearted",
]

logger = logging.getLogger(__name__)


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-gid",
        required=True,
        help="Asana project GID to probe.",
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help=f"Directory for the JSON dump (default: {DEFAULT_OUTPUT_DIR}).",
    )
    parser.add_argument(
        "--detail-limit",
        type=int,
        default=5,
        help="Max status updates to fetch full detail / nested stories for (default: 5).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging verbosity (default: INFO).",
    )
    return parser.parse_args(argv)


def _probe(
    results: Dict[str, Any],
    label: str,
    fn,
) -> Any:
    """Run a probe callable, recording status/count/body without aborting on error."""
    try:
        payload = fn()
    except AsanaHttpError as exc:
        logger.warning("%s -> HTTP %s: %s", label, exc.status, exc)
        results[label] = {"ok": False, "status": exc.status, "body": exc.body}
        return None
    except Exception as exc:  # noqa: BLE001 - defensive, keep probing
        logger.warning("%s -> error: %s", label, exc)
        results[label] = {"ok": False, "status": None, "error": str(exc)}
        return None

    count = len(payload) if isinstance(payload, list) else None
    results[label] = {"ok": True, "status": 200, "count": count, "data": payload}
    logger.info("%s -> ok (%s record(s))", label, count if count is not None else "single")
    return payload


def run_probes(client: AsanaHttpClient, project_gid: str, detail_limit: int) -> Dict[str, Any]:
    results: Dict[str, Any] = {}

    # 1) Status updates for the project (modern endpoint).
    su_params = {
        "parent": project_gid,
        "opt_fields": ",".join(STATUS_UPDATE_FIELDS),
        "limit": "100",
    }
    status_updates = _probe(
        results,
        "status_updates (GET /status_updates?parent=)",
        lambda: list(client.get_paginated("/status_updates", params=dict(su_params))),
    )

    # 2) Per-update full detail + 3) nested stories on each update.
    su_list: List[Dict] = status_updates or []
    for index, update in enumerate(su_list[: max(0, detail_limit)]):
        su_gid = update.get("gid")
        if not su_gid:
            continue
        _probe(
            results,
            f"status_update detail [{index}] (GET /status_updates/{su_gid})",
            lambda su_gid=su_gid: client.get(f"/status_updates/{su_gid}"),
        )
        _probe(
            results,
            f"status_update stories [{index}] (GET /status_updates/{su_gid}/stories)",
            lambda su_gid=su_gid: list(client.get_paginated(f"/status_updates/{su_gid}/stories")),
        )

    # 4) Project-level stories (may not be a public endpoint).
    _probe(
        results,
        "project stories (GET /projects/{gid}/stories)",
        lambda: list(client.get_paginated(f"/projects/{project_gid}/stories")),
    )

    # 5) Deprecated project_statuses endpoint, for comparison.
    _probe(
        results,
        "project_statuses (GET /projects/{gid}/project_statuses)",
        lambda: list(
            client.get_paginated(
                f"/projects/{project_gid}/project_statuses",
                params={"opt_fields": ",".join(STATUS_UPDATE_FIELDS), "limit": "100"},
            )
        ),
    )

    return results


def _print_summary(project_gid: str, results: Dict[str, Any]) -> None:
    logger.info("=== Probe summary for project %s ===", project_gid)
    for label, outcome in results.items():
        if outcome.get("ok"):
            count = outcome.get("count")
            shape = f"{count} record(s)" if count is not None else "single object"
            logger.info("  [ OK ] %s -> %s", label, shape)
        else:
            status = outcome.get("status")
            detail = outcome.get("error") or outcome.get("body")
            logger.info("  [FAIL] %s -> status=%s detail=%s", label, status, detail)


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

    logger.info("Probing project %s (read-only)...", args.project_gid)
    results = run_probes(client, args.project_gid, args.detail_limit)
    _print_summary(args.project_gid, results)

    os.makedirs(args.output_dir, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out_path = os.path.join(
        args.output_dir, f"project_status_probe_{args.project_gid}_{timestamp}.json"
    )
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(
            {"project_gid": args.project_gid, "probes": results},
            handle,
            indent=2,
            ensure_ascii=False,
        )
    logger.info("Raw probe data written to %s", out_path)


if __name__ == "__main__":
    main()
