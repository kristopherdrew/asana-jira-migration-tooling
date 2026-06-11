#!/usr/bin/env python3
"""Diagnostic helper: compare a task's raw stories against the filtered comment list."""

from __future__ import annotations

import argparse
import sys
import textwrap
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.lib.config import load_credentials
from src.lib.http_client import AsanaHttpClient
from src.tasks.task_extras import COMMENT_FIELDS, fetch_task_comments


def _print_story(story: dict, prefix: str, preview: int) -> None:
    text_value = story.get("text") or story.get("html_text") or ""
    snippet = textwrap.shorten(text_value, width=preview, placeholder=" …")
    created_by = story.get("created_by", {}).get("name", "unknown")
    created_at = story.get("created_at", "unknown")
    print(f"{prefix} {story['gid']} | {story.get('resource_subtype')} | {created_at} | {created_by} | {snippet}")


def _iterate_raw_stories(
    client: AsanaHttpClient, task_gid: str, preview: int, page_size: int
) -> Iterable[dict]:
    params = {
        "opt_fields": ",".join(COMMENT_FIELDS),
        "limit": str(page_size),
    }
    endpoint = f"/tasks/{task_gid}/stories"
    for story in client.get_paginated(endpoint, params=params):
        yield story


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-gid", default="1210826389244773", help="Task GID to inspect.")
    parser.add_argument("--page-size", type=int, default=100, help="Pagination limit for the stories endpoint.")
    parser.add_argument("--preview-length", type=int, default=200, help="Characters to print per story text.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    credentials = load_credentials()
    client = AsanaHttpClient(
        token=credentials.token,
        skip_ssl_verification=credentials.skip_ssl_verification,
    )

    print(f"Fetching raw stories for task {args.task_gid} (limit {args.page_size})...")
    for story in _iterate_raw_stories(client, args.task_gid, args.preview_length, args.page_size):
        _print_story(story, "RAW", args.preview_length)

    print("\nFiltered comments (resource_subtype == comment_added):")
    comments = fetch_task_comments(client, args.task_gid, page_size=args.page_size)
    for comment in comments:
        _print_story(comment, "FILTERED", args.preview_length)
    if not comments:
        print("No filtered comments returned.")


if __name__ == "__main__":
    main()
