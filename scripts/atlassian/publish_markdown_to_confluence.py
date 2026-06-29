#!/usr/bin/env python3
"""Publish Markdown files to Confluence as pages, converting bodies locally.

This is the fast, deterministic alternative to having an agent re-type each page:
every ``.md`` file is converted to Confluence storage format by
``src/lib/markdown_to_confluence.py`` (no language-model output tokens), then
created or updated via the Confluence Cloud REST v2 API. Publishing N pages costs
N REST calls, not N full-document regenerations.

Pages are upserted by title: a page whose title already exists in the target
space is updated in place (new version), otherwise it is created. Titles default
to each file's first ``# `` heading (falling back to the filename); that H1 is
stripped from the body so it doesn't duplicate Confluence's page title. Files are
processed in sorted filename order so numbered docs (``01-…``, ``02-…``) land in
order.

Requires ``ATLASSIAN_USERNAME``/``ATLASSIAN_TOKEN`` (same credentials as the Jira
scripts). Pass the Atlassian *site* base URL (``https://example.atlassian.net``);
the client appends ``/wiki`` itself.
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, Iterable, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.lib.config import load_credentials
from src.lib.confluence_client import ConfluenceApiError, ConfluenceClient
from src.lib.logging_utils import configure_logging, resolve_log_level
from src.lib.markdown_to_confluence import extract_title, markdown_to_storage

logger = logging.getLogger(__name__)

DEFAULT_WORKERS = 4
DEFAULT_OUTPUT_DIR = "exports"
DEFAULT_GLOB = "*.md"
REPORT_FIELDNAMES = ["source_file", "title", "page_id", "status", "message"]


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="Atlassian site base URL (e.g., https://example.atlassian.net).")
    parser.add_argument("--space-key", required=True, help="Confluence space key the pages live in (e.g., DOCS).")
    parser.add_argument("--source-dir", help="Directory of Markdown files to publish (uses --glob within it).")
    parser.add_argument("--glob", default=DEFAULT_GLOB, help=f"Glob applied within --source-dir (default: {DEFAULT_GLOB}).")
    parser.add_argument("files", nargs="*", help="Explicit Markdown files to publish (alternative to --source-dir).")
    parser.add_argument("--parent-id", help="Parent page id to nest the published pages under.")
    parser.add_argument("--parent-title", help="Parent page title to resolve to an id (alternative to --parent-id).")
    parser.add_argument("--title-prefix", default="", help="Optional prefix prepended to every derived page title.")
    parser.add_argument("--keep-h1", action="store_true", help="Keep the leading H1 in the body instead of stripping it after using it as the title.")
    parser.add_argument("--exclude", action="append", default=[], help="Filename(s) to skip (repeatable), matched against the file name.")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help=f"Concurrent publish workers (default: {DEFAULT_WORKERS}).")
    parser.add_argument("--limit", type=int, help="Cap the number of pages published this run.")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help=f"Directory for the run report CSV (default: {DEFAULT_OUTPUT_DIR}/).")
    parser.add_argument("--dry-run", action="store_true", help="Convert and resolve targets without writing to Confluence.")
    parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt before publishing.")
    parser.add_argument("--log-level", default="INFO", help="Logging verbosity (default: INFO).")
    return parser.parse_args(argv)


def ensure_atlassian_credentials(credentials) -> tuple:
    username = credentials.atlassian_username
    token = credentials.atlassian_token
    if not username or not token:
        raise SystemExit(
            "Confluence publishing requires ATLASSIAN_USERNAME and ATLASSIAN_TOKEN to be set."
        )
    return username, token


def collect_files(args: argparse.Namespace) -> List[Path]:
    paths: List[Path] = []
    if args.source_dir:
        source = Path(args.source_dir)
        if not source.is_dir():
            raise SystemExit(f"--source-dir is not a directory: {source}")
        paths.extend(sorted(source.glob(args.glob)))
    paths.extend(Path(f) for f in args.files)
    excluded = set(args.exclude)
    deduped: List[Path] = []
    seen = set()
    for path in paths:
        if path.name in excluded:
            continue
        resolved = path.resolve()
        if resolved in seen:
            continue
        if not path.is_file():
            raise SystemExit(f"Not a file: {path}")
        seen.add(resolved)
        deduped.append(path)
    return sorted(deduped, key=lambda p: p.name)


def _result(source: str, title: str, page_id: str, status: str, message: str) -> Dict[str, str]:
    return {"source_file": source, "title": title, "page_id": page_id, "status": status, "message": message}


def build_page(path: Path, title_prefix: str, keep_h1: bool) -> tuple:
    """Return ``(title, storage_body)`` for a Markdown file."""
    markdown = path.read_text(encoding="utf-8")
    title, body = extract_title(markdown)
    if not title:
        title = path.stem
        body = markdown
    if keep_h1:
        body = markdown
    storage = markdown_to_storage(body)
    return f"{title_prefix}{title}", storage


def write_report(rows: List[Dict[str, str]], output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=REPORT_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)
    return output_path


def publish_markdown_to_confluence(
    base_url: str,
    space_key: str,
    files: List[Path],
    output_dir: Path,
    *,
    parent_id: Optional[str] = None,
    parent_title: Optional[str] = None,
    title_prefix: str = "",
    keep_h1: bool = False,
    workers: int = DEFAULT_WORKERS,
    limit: Optional[int] = None,
    dry_run: bool = False,
    assume_yes: bool = False,
    client: Optional[ConfluenceClient] = None,
) -> Path:
    if limit is not None and len(files) > limit:
        files = files[:limit]

    if not files:
        raise SystemExit("No Markdown files matched. Pass files or --source-dir.")

    # Convert every body up front so a malformed file fails before any REST call.
    prepared = []  # (path, title, storage)
    for path in files:
        title, storage = build_page(path, title_prefix, keep_h1)
        prepared.append((path, title, storage))
        logger.info("Prepared %s -> %r (%d chars storage)", path.name, title, len(storage))

    # A dry run is a pure offline preview: convert + report, no credentials/network.
    if dry_run:
        results = [
            _result(str(path), title, "", "dry-run", f"{len(storage)} chars")
            for path, title, storage in prepared
        ]
        return write_report(results, output_dir / "confluence_publish_report.csv")

    credentials = load_credentials()
    username, token = ensure_atlassian_credentials(credentials)
    if client is None:
        client = ConfluenceClient(
            base_url=base_url,
            username=username,
            token=token,
            skip_ssl_verification=credentials.skip_ssl_verification,
        )

    space_id = client.get_space_id(space_key)
    resolved_parent_id = parent_id
    if not resolved_parent_id and parent_title:
        parent = client.find_page(space_id, parent_title)
        if not parent:
            raise SystemExit(f"Parent page titled {parent_title!r} not found in space {space_key}.")
        resolved_parent_id = str(parent["id"])

    if not assume_yes:
        answer = input(
            f"About to publish {len(prepared)} page(s) to space {space_key}"
            f"{f' under parent {resolved_parent_id}' if resolved_parent_id else ''}. Continue? [y/N] "
        ).strip().lower()
        if answer not in {"y", "yes"}:
            raise SystemExit("Aborted by user.")

    def worker(entry: tuple) -> Dict[str, str]:
        path, title, storage = entry
        try:
            existing = client.find_page(space_id, title)
            if existing:
                version = int((existing.get("version") or {}).get("number", 1))
                page = client.update_page(str(existing["id"]), title, storage, version, resolved_parent_id)
                return _result(str(path), title, str(page.get("id", existing["id"])), "updated", f"v{version + 1}")
            page = client.create_page(space_id, title, storage, resolved_parent_id)
            return _result(str(path), title, str(page.get("id", "")), "created", "")
        except ConfluenceApiError as exc:
            logger.error("Failed to publish %s: %s", title, exc)
            return _result(str(path), title, "", "error", str(exc))

    results: List[Dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        future_map = {executor.submit(worker, entry): entry for entry in prepared}
        for future in as_completed(future_map):
            row = future.result()
            results.append(row)
            logger.info("%s: %s (%s)", row["status"], row["title"], row["page_id"] or row["message"])

    # Report in the original (sorted) order rather than completion order.
    order = {str(path): idx for idx, (path, _t, _s) in enumerate(prepared)}
    results.sort(key=lambda r: order.get(r["source_file"], 0))
    return write_report(results, output_dir / "confluence_publish_report.csv")


def main(argv: Optional[Iterable[str]] = None) -> None:
    args = parse_args(argv)
    try:
        log_level = resolve_log_level(args.log_level)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    configure_logging(log_level)

    files = collect_files(args)

    try:
        report_path = publish_markdown_to_confluence(
            base_url=args.base_url,
            space_key=args.space_key,
            files=files,
            output_dir=Path(args.output_dir),
            parent_id=args.parent_id,
            parent_title=args.parent_title,
            title_prefix=args.title_prefix,
            keep_h1=args.keep_h1,
            workers=max(args.workers, 1),
            limit=args.limit,
            dry_run=args.dry_run,
            assume_yes=args.yes,
        )
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(str(exc)) from exc

    logger.info("Confluence publish finished: %s", report_path)


if __name__ == "__main__":
    main()
