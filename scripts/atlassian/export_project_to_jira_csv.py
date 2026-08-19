#!/usr/bin/env python3
"""Export a project's tasks into a Jira-ready CSV (one row per task)."""

from __future__ import annotations

import argparse
import csv
import html
import logging
import math
import sqlite3
import subprocess
import time
import shutil
from collections import defaultdict, Counter
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
import re
import sys
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import parse_qs, quote, urlparse

from scripts.merge_project_exports import merge_csv_files, merge_field_counts, merge_link_mods
from src.lib.asana_urls import build_project_url, build_task_url, infer_workspace_gid
from src.lib.attachments import jira_unique_attachment_name
from src.lib.database import Database, resolve_default_db_path
from src.lib.config import Credentials, load_credentials, resolve_jira_base_url
from src.lib.jira_client import JiraApiError, JiraClient
from src.lib.logging_utils import configure_logging, resolve_log_level
from src.lib.task_scope import parse_task_gids, resolve_task_projects
from src.lib.project_scope import read_project_gids_file
from src.lib.jira_csv_partition import (
    CsvPart,
    MAX_ISSUES_PER_SYNTHETIC_EPIC,
    pack_csv_parts,
    partition_project_csv,
    write_partition_manifest,
)
from src.lib.jira_csv_import_config import copy_active_profile_import_config


DEFAULT_DB_PATH = "data/asana.sqlite"
DEFAULT_OUTPUT_DIR = "exports"
JIRA_TASK_ID_FIELD_NAME = "External issue ID"
logger = logging.getLogger(__name__)
FILENAME_SAFE_PATTERN = re.compile(r"[^A-Za-z0-9._-]+")
ASANA_URL_PATTERN = re.compile(r"https://app\.asana\.com/[^\s>\])}]+")
TASK_ID_PATTERN = re.compile(r"(?:/task/|/0/\d+/)(\d+)(?:/f)?(?:$|[/?#])")
PROFILE_ID_PATTERN = re.compile(r"/profile/(\d+)")
PROJECT_LIST_PATTERN = re.compile(r"/0/(\d+)/list")


class _ProfileLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.overrides: Dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        if tag != "a":
            return
        attr_map = {key: value for key, value in attrs}
        href = attr_map.get("href")
        gid = attr_map.get("data-asana-gid")
        asana_type = (attr_map.get("data-asana-type") or "").lower()
        if not href or not gid:
            return
        if asana_type and asana_type != "user":
            return
        self.overrides[href] = gid
        path = ""
        try:
            parsed = urlparse(href)
            path = parsed.path or ""
        except ValueError:
            path = ""
        profile_match = PROFILE_ID_PATTERN.search(path)
        if profile_match:
            self.overrides[profile_match.group(1)] = gid
        project_list_match = PROJECT_LIST_PATTERN.search(path)
        if project_list_match:
            self.overrides[project_list_match.group(1)] = gid


class _JiraLinkHTMLParser(HTMLParser):
    """Convert HTML fragments to text while preserving hyperlinks for Jira."""

    def __init__(self) -> None:
        super().__init__()
        self.parts: List[str] = []
        self.anchor_stack: List[Dict[str, Optional[str]]] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        tag_lower = tag.lower()
        if tag_lower == "a":
            href = {key: value for key, value in attrs}.get("href")
            self.anchor_stack.append({"href": href, "text": []})
        elif tag_lower in {"br"}:
            self.parts.append("\n")
        elif tag_lower in {"p", "div", "li"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag_lower = tag.lower()
        if tag_lower == "a" and self.anchor_stack:
            anchor = self.anchor_stack.pop()
            text = "".join(anchor.get("text") or []).strip()
            href = anchor.get("href") or ""
            if href and not text:
                text = href
            if href:
                self.parts.append(f"[{text}|{href}]")
            else:
                self.parts.append(text)
        elif tag_lower in {"p", "div", "li"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.anchor_stack:
            self.anchor_stack[-1]["text"].append(data)
        else:
            self.parts.append(data)

    def handle_entityref(self, name: str) -> None:
        self.parts.append(html.unescape(f"&{name};"))

    def handle_charref(self, name: str) -> None:
        self.parts.append(html.unescape(f"&#{name};"))

    def get_text(self) -> str:
        return "".join(self.parts)


def extract_profile_gid_overrides(html: Optional[str]) -> Dict[str, str]:
    if not html:
        return {}
    parser = _ProfileLinkParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        logger.debug("Unable to parse html content for profile overrides.", exc_info=True)
    return parser.overrides


def resolve_atlassian_identity(
    user_map: Dict[str, Dict[str, Optional[str]]],
    user_gid: Optional[str],
    fallback_account_id: Optional[str] = None,
) -> Tuple[str, str]:
    if not user_gid:
        return "", ""
    entry = user_map.get(user_gid)
    if isinstance(entry, dict):
        account_id = entry.get("account_id") or ""
        email = entry.get("email") or ""
    elif isinstance(entry, str):
        account_id = entry
        email = ""
    else:
        account_id = ""
        email = ""
    if not account_id and fallback_account_id:
        account_id = fallback_account_id
    return account_id, email


def load_user_display_names(connection: sqlite3.Connection) -> Dict[str, str]:
    rows = connection.execute("SELECT gid, name FROM users").fetchall()
    mapping: Dict[str, str] = {}
    for row in rows:
        gid = row["gid"]
        if not gid:
            continue
        mapping[gid] = row["name"] or ""
    return mapping


def load_user_emails(connection: sqlite3.Connection) -> Dict[str, str]:
    rows = connection.execute("SELECT gid, email FROM users").fetchall()
    mapping: Dict[str, str] = {}
    for row in rows:
        gid = row["gid"]
        if not gid:
            continue
        email = (row["email"] or "").strip()
        if email:
            mapping[gid] = email
    return mapping


def fetch_project_latest_modified(connection: sqlite3.Connection, project_gid: str) -> Optional[str]:
    row = connection.execute(
        """
        SELECT MAX(t.modified_at) AS latest_modified
        FROM tasks t
        JOIN task_projects tp ON tp.task_gid = t.gid
        WHERE tp.project_gid = ?
        """,
        (project_gid,),
    ).fetchone()
    if not row:
        return None
    return row["latest_modified"]


def compute_incremental_refresh_window(
    latest_modified: Optional[str],
    now: Optional[datetime] = None,
) -> Tuple[int, datetime]:
    reference_now = now or datetime.now(timezone.utc)
    baseline = parse_iso_datetime(latest_modified) if latest_modified else None
    if baseline:
        start_time = baseline - timedelta(days=1)
    else:
        start_time = reference_now - timedelta(days=7)
    if start_time > reference_now:
        start_time = reference_now - timedelta(days=1)
    delta = reference_now - start_time
    days_back = max(1, math.ceil(delta.total_seconds() / 86400))
    return days_back, start_time


def prompt_and_run_incremental_refresh(
    project_gid: str,
    latest_modified: Optional[str],
    workers: Optional[int],
    decision: Optional[bool] = None,
    project_breakdown: Optional[str] = None,
) -> None:
    if decision is None:
        if not sys.stdin.isatty():
            logger.info("Skipping incremental refresh prompt (non-interactive session).")
            return
        days_back, start_time = compute_incremental_refresh_window(latest_modified)
        date_label = start_time.strftime("%Y-%m-%d")
        prompt = (
            f"Run incremental sync for project {project_gid} starting {date_label} "
            f"(days_back={days_back}) before exporting? [y/N]: "
        )
        try:
            response = input(prompt).strip().lower()
        except EOFError:
            logger.info("Skipping incremental refresh prompt (input unavailable).")
            return
        decision = response in {"y", "yes"}
    else:
        days_back, start_time = compute_incremental_refresh_window(latest_modified)
    if not decision:
        logger.info("Incremental refresh skipped.")
        return
    cmd = [
        sys.executable,
        "-m",
        "scripts.get_incremental_updates",
        "--days-back",
        str(days_back),
        "--project-gid",
        project_gid,
    ]
    if project_breakdown:
        cmd.extend(["--project-breakdown", project_breakdown])
    if workers and workers > 0:
        cmd.extend(["--workers", str(workers)])
    logger.info("Running incremental sync command: %s", " ".join(cmd))
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as exc:
        logger.error("Incremental sync failed (exit code %s).", exc.returncode)
    except FileNotFoundError as exc:
        logger.error("Unable to launch incremental sync command: %s", exc)
    else:
        logger.info("Incremental sync completed successfully.")


def normalize_jira_base_url(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if not normalized.startswith(("http://", "https://")):
        normalized = f"https://{normalized}"
    normalized = normalized.rstrip("/")
    return normalized


def next_export_dir(root: Path, export_id: Optional[str] = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    index = 1
    while True:
        suffix = f"-{export_id}" if export_id else ""
        candidate = root / f"export-{index:02d}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def organize_outputs(output_dir: Path) -> None:
    """Group exported files into subfolders by type to reduce clutter."""
    categories = {
        "skipped_tasks": ["*_skipped_tasks.csv"],
        "tasks": ["*_tasks.csv", "*_tasks_part_*.csv"],
        "tasks_field_counts": ["*_tasks_field_counts.csv"],
        "link_modifications": ["*_tasks_link_modifications.*", "*_link_modifications.*"],
        "section_counts": ["*_section_counts.csv"],
        "user_associations": ["*_user_associations.csv"],
        "manifests": ["*_tasks_manifest.csv"],
        "jira_configurations": ["*_jira_config.txt"],
    }

    for folder, patterns in categories.items():
        target_dir = output_dir / folder
        target_dir.mkdir(exist_ok=True)
        for pattern in patterns:
            for path in output_dir.glob(pattern):
                # Avoid moving skipped-task files into the general tasks folder
                if folder == "tasks" and path.name.endswith("_skipped_tasks.csv"):
                    continue
                if path.is_file() and path.parent != target_dir:
                    dest = target_dir / path.name
                    try:
                        shutil.move(str(path), str(dest))
                    except Exception:
                        # If move fails, leave file in place and continue.
                        logger.warning("Unable to move %s to %s", path, dest, exc_info=True)


def load_project_names(connection: sqlite3.Connection) -> Dict[str, str]:
    rows = connection.execute("SELECT gid, name FROM projects").fetchall()
    mapping: Dict[str, str] = {}
    for row in rows:
        gid = row["gid"]
        if not gid:
            continue
        mapping[gid] = row["name"] or ""
    return mapping


def transform_asana_links(
    text: str,
    user_map: Dict[str, Dict[str, Optional[str]]],
    user_display_names: Dict[str, str],
    project_names: Dict[str, str],
    jira_base_url: str,
    attachment_names: Optional[Dict[str, str]] = None,
    profile_gid_overrides: Optional[Dict[str, str]] = None,
    fallback_account_id: Optional[str] = None,
    record_transformation: Optional[Callable[[str, str], None]] = None,
) -> str:
    if not text:
        return ""
    if "https://app.asana.com" not in text:
        return text
    attachment_lookup = attachment_names or {}
    profile_gid_lookup = profile_gid_overrides or {}

    def _record(url: str, modified: str) -> None:
        if record_transformation:
            record_transformation(url, modified)

    result: List[str] = []
    index = 0
    while True:
        match = ASANA_URL_PATTERN.search(text, index)
        if not match:
            result.append(text[index:])
            break
        start, end = match.span()
        result.append(text[index:start])
        url = match.group(0)
        addition = build_asana_link_addition(
            url,
            user_map,
            user_display_names,
            project_names,
            jira_base_url,
            attachment_lookup,
            profile_gid_lookup,
            fallback_account_id,
        )
        jira_bounds = _find_jira_link_bounds(text, start, end)
        if jira_bounds:
            open_idx, _, close_idx = jira_bounds
            result.append(url)
            trailing = text[end : close_idx + 1]
            result.append(trailing)
            if addition:
                combined = f"{text[open_idx:close_idx + 1]}\n{addition}"
                _record(url, combined)
                result.append(f"\n{addition}")
            index = close_idx + 1
            continue
        if addition:
            combined = f"{url}\n{addition}"
            _record(url, combined)
            result.append(combined)
        else:
            result.append(url)
        index = end
    return "".join(result)


def _find_jira_link_bounds(text: str, url_start: int, url_end: int) -> Optional[Tuple[int, int, int]]:
    """
    Detect whether a matched Asana URL is inside Jira's link syntax [label|url].

    Returns a tuple of (open_bracket_index, pipe_index, close_bracket_index) when found.
    """

    open_idx = text.rfind("[", 0, url_start)
    if open_idx == -1:
        return None
    pipe_idx = text.rfind("|", open_idx, url_start)
    if pipe_idx == -1 or pipe_idx < open_idx:
        return None
    close_idx = text.find("]", url_end)
    if close_idx == -1:
        return None
    return open_idx, pipe_idx, close_idx


def build_jira_task_search_url(jira_base_url: str, task_id: str) -> str:
    base = jira_base_url.rstrip("/")
    jql = f"\"{JIRA_TASK_ID_FIELD_NAME}\" ~ \"{task_id}\""
    return f"{base}/issues?jql={quote(jql)}"


def build_asana_link_addition(
    url: str,
    user_map: Dict[str, Dict[str, Optional[str]]],
    user_display_names: Dict[str, str],
    project_names: Dict[str, str],
    jira_base_url: str,
    attachment_names: Optional[Dict[str, str]] = None,
    profile_gid_overrides: Optional[Dict[str, str]] = None,
    fallback_account_id: Optional[str] = None,
) -> Optional[str]:
    attachment_lookup = attachment_names or {}
    profile_gid_lookup = profile_gid_overrides or {}
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    path = parsed.path or ""
    if "/get_asset" in path:
        query = parse_qs(parsed.query)
        asset_id = None
        if "asset_id" in query and query["asset_id"]:
            asset_id = query["asset_id"][0]
        if asset_id:
            attachment_name = attachment_lookup.get(asset_id) or asset_id
            return f"!{attachment_name}!"
        return None

    task_match = TASK_ID_PATTERN.search(path)
    if task_match:
        task_id = task_match.group(1)
        return build_jira_task_search_url(jira_base_url, task_id)

    profile_match = PROFILE_ID_PATTERN.search(path)
    if profile_match:
        profile_id = profile_match.group(1)
        user_gid = profile_gid_lookup.get(url) or profile_gid_lookup.get(profile_id) or profile_id
        return format_user_reference(user_gid, user_map, user_display_names, fallback_account_id)

    project_match = PROJECT_LIST_PATTERN.search(path)
    if project_match:
        project_gid = project_match.group(1)
        project_name = project_names.get(project_gid)
        if project_name:
            return project_name
        user_gid = profile_gid_lookup.get(url) or profile_gid_lookup.get(project_gid)
        if user_gid:
            return format_user_reference(user_gid, user_map, user_display_names, fallback_account_id)
        return f"Project {project_gid} - not found"

    return None


def convert_html_links_to_jira_format(html_text: str) -> str:
    """Translate HTML anchors to Jira's [text|url] format, preserving line breaks."""

    parser = _JiraLinkHTMLParser()
    try:
        parser.feed(html_text)
        parser.close()
    except Exception:
        logger.debug("Unable to parse html content for link conversion.", exc_info=True)
        return html_text
    text = parser.get_text()
    text = re.sub(r"[\t ]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_html_hrefs(html_text: str) -> List[str]:
    return re.findall(r'href\s*=\s*"([^"]+)"', html_text, flags=re.IGNORECASE)


def prefer_html_text_with_links(plain_text: Optional[str], html_text: Optional[str]) -> str:
    """Prefer HTML-derived content when anchors add information missing from plain text."""

    base = plain_text or ""
    if not html_text or "<a" not in html_text.lower():
        return base
    anchors = _parse_html_anchors(html_text)
    converted = convert_html_links_to_jira_format(html_text)
    if not converted:
        return base
    hrefs = [href for href, _ in anchors if href]
    href_missing_from_text = any(href and href not in base for href in hrefs)
    if href_missing_from_text or not base or "http" not in base:
        return converted
    if any(label and href and label != href for href, label in anchors):
        return _apply_anchor_labels(base, anchors)
    return base


def _parse_html_anchors(html_text: str) -> List[Tuple[str, str]]:
    pattern = re.compile(r"<a[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>", re.IGNORECASE | re.DOTALL)
    anchors: List[Tuple[str, str]] = []
    for match in pattern.finditer(html_text):
        href, text = match.groups()
        anchor_text = html.unescape(re.sub(r"<[^>]+>", "", text or "").strip())
        anchors.append(((href or "").strip(), anchor_text))
    return anchors


def _apply_anchor_labels(base_text: str, anchors: List[Tuple[str, str]]) -> str:
    updated = base_text
    for href, label in anchors:
        if not href or not label or label == href:
            continue
        updated = updated.replace(href, f"[{label}|{href}]")
    return updated


def format_user_reference(
    user_gid: str,
    user_map: Dict[str, Dict[str, Optional[str]]],
    user_display_names: Dict[str, str],
    fallback_account_id: Optional[str] = None,
) -> str:
    account_id, _ = resolve_atlassian_identity(user_map, user_gid, fallback_account_id)
    if account_id:
        return f"[~accountid:{account_id}]"
    display_name = (user_display_names.get(user_gid) or "").strip()
    if not display_name:
        display_name = f"unknown_user {user_gid}"
    return display_name


def format_user_display_name(user_gid: Optional[str], user_name: Optional[str]) -> str:
    display_name = (user_name or "").strip()
    if display_name:
        return display_name
    if user_gid:
        return f"unknown_user {user_gid}"
    return ""


def _is_active_atlassian_user(user_map: Dict[str, Dict[str, Optional[str]]], user_gid: Optional[str]) -> bool:
    if not user_gid:
        return False
    entry = user_map.get(user_gid) or {}
    return (entry.get("status") or "").strip().casefold() == "active"


def _is_explicitly_inactive_atlassian_user(
    user_map: Dict[str, Dict[str, Optional[str]]],
    user_gid: Optional[str],
) -> bool:
    if not user_gid or user_gid not in user_map:
        return False
    entry = user_map.get(user_gid) or {}
    status = (entry.get("status") or "").strip().casefold()
    return bool(status) and status != "active"


def _build_active_creator_fallback(
    tasks: List[dict],
    user_map: Dict[str, Dict[str, Optional[str]]],
    user_display_names: Dict[str, str],
    user_emails: Dict[str, str],
    fallback_atlassian_id: Optional[str],
) -> Optional[Dict[str, str]]:
    creator_counts: Dict[str, int] = defaultdict(int)
    creator_details: Dict[str, Dict[str, str]] = {}
    for task in tasks:
        creator_gid = task.get("created_by_gid")
        if not _is_active_atlassian_user(user_map, creator_gid):
            continue
        creator_counts[creator_gid] += 1
        if creator_gid not in creator_details:
            account_id, account_email = resolve_atlassian_identity(user_map, creator_gid, fallback_atlassian_id)
            creator_details[creator_gid] = {
                "user_gid": creator_gid or "",
                "display_name": format_user_display_name(creator_gid, user_display_names.get(creator_gid) or task.get("creator_name")),
                "email": (user_emails.get(creator_gid) or task.get("creator_email") or "").strip(),
                "atlassian_id": account_id,
                "atlassian_email": account_email,
            }

    if not creator_counts:
        return None

    winner_gid = sorted(
        creator_counts.keys(),
        key=lambda gid: (
            -creator_counts[gid],
            creator_details[gid]["display_name"].casefold(),
            gid,
        ),
    )[0]
    return creator_details[winner_gid]


def log_custom_field_list(
    project_gid: str,
    fields: List[Dict[str, str]],
    heading: str,
) -> None:
    if not fields:
        logger.info("%s for project %s: none", heading, project_gid)
        return
    logger.info("%s for project %s (%s):", heading, project_gid, len(fields))
    for meta in fields:
        count_display = ""
        if "value_count" in meta:
            count_display = f", values={meta['value_count']}"
        logger.info(
            "  - %s (%s, %s%s)",
            meta["name"],
            meta["gid"],
            (meta["type"] or "unknown"),
            count_display,
        )
    names_line = format_custom_field_names_line(fields)
    if names_line:
        logger.info("Copyable custom field list: %s", names_line)


def apply_custom_field_filter(
    fields: List[Dict[str, str]],
    requested: Optional[List[str]],
) -> Tuple[List[Dict[str, str]], List[str]]:
    if not requested:
        return fields, []
    normalized = [value.strip() for value in requested if value and value.strip()]
    if not normalized:
        return fields, []

    requested_exact = set(normalized)
    requested_lower = {value.lower() for value in normalized}
    matched_exact: Set[str] = set()
    matched_lower: Set[str] = set()
    filtered: List[Dict[str, str]] = []

    for meta in fields:
        gid = meta["gid"]
        name_lower = (meta["name"] or "").lower()
        include = False
        if gid in requested_exact:
            include = True
            matched_exact.add(gid)
        if name_lower in requested_lower:
            include = True
            matched_lower.add(name_lower)
        if include:
            filtered.append(meta)

    unmatched = [
        token
        for token in normalized
        if token not in matched_exact and token.lower() not in matched_lower
    ]
    return filtered, unmatched


def resolve_custom_field_value(
    meta: Dict[str, object],
    task_gid: str,
    single_map: Dict[Tuple[str, str], sqlite3.Row],
    multi_map: Dict[Tuple[str, str], List[Tuple[Optional[int], str]]],
    option_map: Dict[Tuple[str, str], str],
) -> str:
    """Return the display string for one task's value of a custom field ('' if unset).

    Multi-enum values are joined with ', ' into a single string. The columnar
    exporter fans multi-enum out into numbered columns via its own path; this
    helper is the single-string form used when folding fields into a description.
    """

    field_gid = meta["gid"]
    key = (task_gid, field_gid)
    if meta.get("type") == "multi_enum" and meta.get("max_multi", 0):
        names: List[str] = []
        for _position, enum_gid in multi_map.get(key, []):
            option_name = option_map.get((field_gid, enum_gid), "")
            if option_name:
                names.append(option_name)
        return ", ".join(names)

    single = single_map.get(key)
    if not single:
        return ""
    if meta.get("type") in {"enum", "multi_enum"} and single["enum_option_gid"]:
        return option_map.get((field_gid, single["enum_option_gid"])) or single["display_value"] or ""
    if single["display_value"]:
        return single["display_value"]
    if single["text_value"]:
        return single["text_value"]
    if single["number_value"] is not None:
        return str(single["number_value"])
    if single["date_value"]:
        return format_export_datetime(single["date_value"])
    return ""


def sanitize_filename_component(value: str) -> str:
    cleaned = (value or "").strip().replace(" ", "_")
    cleaned = FILENAME_SAFE_PATTERN.sub("_", cleaned)
    cleaned = cleaned.strip("._")
    return cleaned or "project"


def current_utc_time() -> datetime:
    return datetime.now(timezone.utc)


def normalize_label_value(value: str) -> str:
    """Replace whitespace with underscores for Jira label compatibility."""

    cleaned = (value or "").strip()
    return re.sub(r"\s+", "_", cleaned) if cleaned else ""


def record_user_association(
    user_gid: Optional[str],
    role: str,
    email: Optional[str],
    associations: Dict[str, Counter],
    email_map: Dict[str, str],
) -> None:
    if not user_gid:
        return
    associations.setdefault(user_gid, Counter())[role] += 1
    if email and not email_map.get(user_gid):
        email_map[user_gid] = email


def load_task_collaborators(
    connection: sqlite3.Connection, task_gids: Iterable[str]
) -> Dict[str, List[str]]:
    """Return the saved Asana collaborators (followers) for each task."""

    collaborators: Dict[str, List[str]] = defaultdict(list)
    gids = [gid for gid in task_gids if gid]
    for start in range(0, len(gids), 500):
        chunk = gids[start : start + 500]
        placeholders = ",".join("?" for _ in chunk)
        rows = connection.execute(
            f"""
            SELECT task_gid, user_gid
            FROM task_followers
            WHERE task_gid IN ({placeholders})
            ORDER BY task_gid, user_gid
            """,
            chunk,
        ).fetchall()
        for row in rows:
            collaborators[row["task_gid"]].append(row["user_gid"])
    return collaborators


def exclude_task_owner_collaborators(
    collaborators_by_task: Dict[str, List[str]], tasks: Iterable[Dict]
) -> Dict[str, List[str]]:
    """Exclude collaborators already represented by a task's creator or assignee."""

    owner_gids_by_task = {
        task["gid"]: {task["created_by_gid"], task["assignee_gid"]} - {None, ""}
        for task in tasks
        if task["gid"]
    }
    return {
        task_gid: [
            collaborator_gid
            for collaborator_gid in collaborator_gids
            if collaborator_gid not in owner_gids_by_task.get(task_gid, set())
        ]
        for task_gid, collaborator_gids in collaborators_by_task.items()
    }


def collaborator_column_headers(max_collaborators: int) -> List[str]:
    """Build repeated name/email/Atlassian-ID columns for task collaborators."""

    headers: List[str] = []
    for _ in range(max_collaborators):
        headers.extend(
            ["Collaborators", "collaborator_email", "collaborator_atlassian_id"]
        )
    return headers


def format_task_collaborator_values(
    collaborator_gids: Iterable[str],
    user_display_names: Dict[str, str],
    user_emails: Dict[str, str],
    user_map: Dict[str, Dict[str, Optional[str]]],
    fallback_atlassian_id: Optional[str],
) -> List[str]:
    """Return aligned display name, email, and Jira account-ID values per collaborator."""

    values: List[str] = []
    for gid in collaborator_gids:
        account_id, _ = resolve_atlassian_identity(user_map, gid, fallback_atlassian_id)
        values.extend(
            [
                format_user_display_name(gid, user_display_names.get(gid)),
                user_emails.get(gid, ""),
                account_id,
            ]
        )
    return values


def filter_missing_account_counts(
    associations: Dict[str, Counter],
    user_map: Dict[str, Dict[str, Optional[str]]],
    email_map: Dict[str, str],
    fallback_atlassian_id: Optional[str],
    missing_accounts: Optional[Dict[str, Set[str]]] = None,
) -> Dict[str, Counter]:
    merged: Dict[str, Counter] = {gid: Counter(counts) for gid, counts in associations.items()}

    if missing_accounts:
        for gid, identifiers in missing_accounts.items():
            counts = merged.setdefault(gid, Counter())
            counts["commenter"] += max(1, len(identifiers))
            if identifiers:
                email_map.setdefault(gid, next(iter(identifiers)))

    missing: Dict[str, Counter] = {}
    for gid, counts in merged.items():
        account_id, _ = resolve_atlassian_identity(user_map, gid, fallback_atlassian_id)
        if account_id:
            continue
        missing[gid] = counts
        email_map.setdefault(gid, "")
    return missing


def write_missing_account_report(
    output_path: Path,
    missing_accounts: Dict[str, Counter],
    email_map: Dict[str, str],
    project_name: str,
) -> Optional[Path]:
    path = output_path.with_name(f"{output_path.stem}_missing_accounts.csv")
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "user_gid",
                "email",
                "creator_count",
                "assignee_count",
                "completed_by_count",
                "commenter_count",
                "collaborator_count",
                "project",
            ]
        )
        for user_gid in sorted(missing_accounts):
            counts = missing_accounts[user_gid]
            writer.writerow(
                [
                    user_gid,
                    email_map.get(user_gid, ""),
                    counts.get("creator", 0),
                    counts.get("assignee", 0),
                    counts.get("completed_by", 0),
                    counts.get("commenter", 0),
                    counts.get("collaborator", 0),
                    project_name,
                ]
            )
    return path


def write_user_association_report(
    output_path: Path,
    associations: Dict[str, Counter],
    user_map: Dict[str, Dict[str, Optional[str]]],
    user_display_names: Dict[str, str],
    email_map: Dict[str, str],
    project_name: str,
    last_active_map: Dict[str, Optional[str]],
) -> Path:
    path = output_path.with_name(f"{output_path.stem}_user_associations.csv")
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "user_gid",
                "display_name",
                "email",
                "atlassian_account_id",
                "atlassian_email",
                "last_active_date",
                "creator_count",
                "assignee_count",
                "completed_by_count",
                "commenter_count",
                "collaborator_count",
                "project",
            ]
        )
        for gid in sorted(associations.keys()):
            counts = associations.get(gid, Counter())
            account_id, atl_email = resolve_atlassian_identity(user_map, gid, None)
            display_name = (user_display_names.get(gid) or "").strip()
            if not display_name:
                display_name = f"unknown_user {gid}"
            writer.writerow(
                [
                    gid,
                    display_name,
                    email_map.get(gid, ""),
                    account_id,
                    atl_email,
                    last_active_map.get(gid, "") or "",
                    counts.get("creator", 0),
                    counts.get("assignee", 0),
                    counts.get("completed_by", 0),
                    counts.get("commenter", 0),
                    counts.get("collaborator", 0),
                    project_name,
                ]
            )
    return path


def preview_project_custom_fields(connection: sqlite3.Connection, project_gid: str) -> List[Tuple[str, str]]:
    query = """
        SELECT DISTINCT cf.gid, cf.name
        FROM custom_fields cf
        JOIN task_custom_fields tcf ON tcf.custom_field_gid = cf.gid
        JOIN task_projects tp ON tp.task_gid = tcf.task_gid
        WHERE tp.project_gid = ?
      UNION
        SELECT DISTINCT cf.gid, cf.name
        FROM custom_fields cf
        JOIN task_custom_field_multi_enum tcfm ON tcfm.custom_field_gid = cf.gid
        JOIN task_projects tp ON tp.task_gid = tcfm.task_gid
        WHERE tp.project_gid = ?
    """
    rows = connection.execute(query, (project_gid, project_gid)).fetchall()
    return [(row["gid"], row["name"] or "") for row in rows]


def build_default_filename(project_name: Optional[str]) -> str:
    timestamp = current_utc_time()
    base = sanitize_filename_component(project_name or "project")
    stamp = timestamp.strftime("%Y%m%d-%H%M")
    return f"{base}-{stamp}_tasks.csv"


def fetch_project_task_activity(connection: sqlite3.Connection, project_gid: str):
    return connection.execute(
        """
        SELECT
            MAX(t.created_at) AS last_task_created_at,
            MAX(t.completed_at) AS last_task_resolved_at,
            MAX(t.modified_at) AS last_task_updated_at
        FROM task_projects tp
        JOIN tasks t ON t.gid = tp.task_gid
        WHERE tp.project_gid = ?
        """,
        (project_gid,),
    ).fetchone()


def ensure_tasks_suffix(filename: str) -> str:
    if filename.lower().endswith("_tasks.csv"):
        return filename
    if filename.lower().endswith(".csv"):
        stem = filename[:-4]
        return f"{stem}_tasks.csv"
    return f"{filename}_tasks.csv"


def format_custom_field_names_line(fields: List[Dict[str, str]]) -> str:
    names = [meta["name"] for meta in fields if meta.get("name")]
    return ", ".join(names)


def prompt_for_custom_field_choice(
    project_name: str,
    project_gid: str,
    fields: List[Dict[str, str]],
) -> List[str]:
    if not sys.stdin.isatty():
        return []
    try:
        print()
        print(f"Custom fields detected for {project_name} ({project_gid}):")
        for meta in fields:
            value_count = meta.get("value_count", 0)
            print(f"  - {meta['name']} ({meta['gid']}) — {value_count} value(s) found")
        names_line = format_custom_field_names_line(fields)
        if names_line:
            print()
            print("Copy this line to edit the custom field list quickly:")
            print(names_line)
        print()
        print("Select export option:")
        print("  [1] Export all custom fields with data (default)")
        print("  [2] Enter a comma-separated list of field names or GIDs to include")
        while True:
            choice = input("Choice [1-2]: ").strip()
            if not choice or choice == "1":
                return []
            if choice == "2":
                raw = input("Enter comma-separated field names or GIDs: ").strip()
                if not raw:
                    print("No fields entered; defaulting to all fields.")
                    return []
                return [value.strip() for value in raw.split(",") if value.strip()]
            print("Please enter 1 or 2.")
    except EOFError:
        return []


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-gid",
        help="Project GID to export (mutually exclusive with --project-gids/--team-gid).",
    )
    parser.add_argument(
        "--project-gids",
        help="Comma-separated list of project GIDs to export (mutually exclusive with --project-gid/--team-gid).",
    )
    parser.add_argument(
        "--project-gids-file",
        help="Path to a file containing project GIDs (newline or comma separated).",
    )
    parser.add_argument(
        "--incremental-project-breakdown",
        choices=("prompt", "yes", "no"),
        help="Pass-through to scripts.get_incremental_updates --project-breakdown during incremental refresh.",
    )
    parser.add_argument(
        "--team-gid",
        help="Export all projects belonging to this team GID (mutually exclusive with --project-gid/--project-gids).",
    )
    parser.add_argument(
        "--task-gids",
        help="Comma-separated task GIDs to export.",
    )
    parser.add_argument(
        "--task-gids-file",
        help="Path to a file containing task GIDs (newline or comma separated).",
    )
    parser.add_argument(
        "--database",
        default=resolve_default_db_path(),
        help=f"Path to the SQLite database (default: {DEFAULT_DB_PATH}).",
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help=f"Directory to store the exported CSV (default: {DEFAULT_OUTPUT_DIR}).",
    )
    parser.add_argument(
        "--filename",
        help="Optional filename for the CSV. Defaults to project_<gid>_tasks.csv.",
    )
    parser.add_argument(
        "--stats-only",
        action="store_true",
        help="Skip task CSV and only emit summary CSV artifacts (sections/fields/missing accounts).",
    )
    parser.add_argument(
        "--custom-fields",
        help=(
            "Comma-separated list of custom field names or GIDs to include. "
            "Defaults to exporting every field with data."
        ),
    )
    parser.add_argument(
        "--no-custom-fields",
        action="store_true",
        help="Skip exporting all custom fields (overrides --custom-fields and prompts).",
    )
    parser.add_argument(
        "--no-custom-field-prompt",
        action="store_true",
        help="Skip the interactive custom field prompt (always export all or use --custom-fields).",
    )
    parser.add_argument(
        "--archived-scope",
        choices=("active", "archived", "all"),
        default="active",
        help=(
            "When selecting projects via --team-gid, choose which archived state(s) to export "
            "(default: active). Explicit --project-gid(s) always include archived projects."
        ),
    )
    parser.add_argument(
        "--consolidated-output",
        action="store_true",
        help="When exporting multiple projects, write a single consolidated CSV instead of one per project.",
    )
    parser.add_argument(
        "--consolidated-filename",
        help="Optional filename for the consolidated CSV (defaults to Consolidated-<timestamp>.csv).",
    )
    parser.add_argument(
        "--also-per-project",
        action="store_true",
        help="When using consolidated output, also write per-project CSVs.",
    )
    parser.add_argument(
        "--no-refresh-prompt",
        action="store_true",
        help="Skip per-project incremental refresh prompts (use current DB state).",
    )
    parser.add_argument(
        "--incremental-refresh",
        choices=("prompt", "yes", "no"),
        default="prompt",
        help="Control incremental refresh behavior across projects: prompt (default), always run (yes), or never run (no).",
    )
    parser.add_argument(
        "--prod",
        action="store_true",
        help=(
            "Use the production Jira base URL (JIRA_BASE_URL) for JQL search links. "
            "Default: sandbox (JIRA_SANDBOX_BASE_URL)."
        ),
    )
    parser.add_argument("--jira-site", help="Named Jira site key from the active profile.")
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging verbosity (default: INFO).",
    )
    parser.add_argument(
        "--export-id",
        help=(
            "Optional identifier to record exported tasks and skip any tasks already associated with "
            "the same export ID in future runs."
        ),
    )
    parser.add_argument(
        "--clear-export-ids",
        action="store_true",
        help="Delete all recorded export_id/task mappings in the database and exit.",
    )
    parser.add_argument(
        "--clear-export-id",
        help="Delete recorded task mappings for a specific export_id and exit.",
    )
    parser.add_argument(
        "--clear-jira-project-map",
        action="store_true",
        help="Delete all stored Jira project associations and exit.",
    )
    parser.add_argument(
        "--map-unmapped-to-jira-project-key",
        help=(
            "Assign this Jira project key to selected projects that do not already "
            "have a Jira project association. Requires --map-unmapped-to-jira-project-name."
        ),
    )
    parser.add_argument(
        "--map-unmapped-to-jira-project-name",
        help=(
            "Assign this Jira project name to selected projects that do not already "
            "have a Jira project association. Requires --map-unmapped-to-jira-project-key."
        ),
    )
    parser.add_argument(
        "--fallback-atlassian-id",
        help="Optional Atlassian account ID to use when a user is missing from the mapping tables.",
    )
    epic_group = parser.add_mutually_exclusive_group()
    epic_group.add_argument(
        "--add-epics",
        action="store_true",
        help="When exporting, add a synthetic project-level epic row and re-parent top-level tasks to it.",
    )
    epic_group.add_argument(
        "--add-section-epics",
        action="store_true",
        help=(
            "When exporting, add a synthetic epic per section and re-parent top-level tasks into their "
            "first section (if present)."
        ),
    )
    return parser.parse_args(argv)


def parse_project_list(
    connection: sqlite3.Connection,
    project_gid: Optional[str] = None,
    project_gids: Optional[str] = None,
    project_gids_file: Optional[str] = None,
    team_gid: Optional[str] = None,
    archived_scope: str = "active",
) -> List[Tuple[str, str]]:
    scopes = [bool(project_gid), bool(project_gids), bool(project_gids_file), bool(team_gid)]
    if sum(scopes) == 0:
        raise SystemExit("Provide one of --project-gid, --project-gids, --project-gids-file, or --team-gid.")
    if sum(scopes) > 1:
        raise SystemExit("Use only one of --project-gid, --project-gids, --project-gids-file, or --team-gid.")

    def _archived_clause(scope: str) -> str:
        if scope == "active":
            return "AND COALESCE(p.archived, 0) = 0"
        if scope == "archived":
            return "AND COALESCE(p.archived, 0) = 1"
        return ""

    if project_gid:
        row = connection.execute("SELECT gid, name FROM projects WHERE gid = ?", (project_gid,)).fetchone()
        if not row:
            raise SystemExit(f"Project with gid {project_gid} not found in database.")
        return [(row["gid"], row["name"])]

    if project_gids_file:
        project_gids = read_project_gids_file(project_gids_file)

    if project_gids:
        gids = [gid.strip() for gid in project_gids.split(",") if gid.strip()]
        if not gids:
            raise SystemExit("No valid project gids provided to --project-gids.")
        placeholders = ",".join("?" for _ in gids)
        rows = connection.execute(
            f"SELECT p.gid, p.name, p.archived FROM projects p WHERE p.gid IN ({placeholders})",
            gids,
        ).fetchall()
        rows_by_gid = {row["gid"]: row for row in rows}
        ordered: List[Tuple[str, str]] = []
        missing: List[str] = []
        for gid in gids:
            row = rows_by_gid.get(gid)
            if row:
                ordered.append((row["gid"], row["name"]))
            else:
                missing.append(gid)
        if missing:
            logger.warning("Project gids not found (or filtered by scope): %s", ", ".join(sorted(missing)))
        if not ordered:
            raise SystemExit("No matching projects found for requested gids.")
        return ordered

    # team path
    rows = connection.execute(
        f"""
        SELECT p.gid, p.name
        FROM projects p
        WHERE p.team_gid = ? {_archived_clause(archived_scope)}
        ORDER BY p.name
        """,
        (team_gid,),
    ).fetchall()
    if not rows:
        raise SystemExit(f"No projects found for team {team_gid} with archived_scope={archived_scope}.")
    return [(row["gid"], row["name"]) for row in rows]


def assign_unmapped_jira_project(
    database: Database,
    projects: List[Tuple[str, str]],
    jira_project_key: str,
    jira_project_name: str,
    jira_site_key: Optional[str] = None,
) -> int:
    """Associate only currently unmapped projects in an export scope with Jira."""
    with database.connect() as connection:
        database.initialize(connection)
        existing = database.load_jira_project_mappings(
            connection, jira_site_key=jira_site_key
        )
        missing = [
            {
                "project_gid": gid,
                "project_name": jira_project_name,
                "project_key": jira_project_key,
            }
            for gid, _asana_name in projects
            if gid not in existing
        ]
        return database.upsert_jira_project_mappings(
            connection,
            missing,
            current_utc_time(),
            jira_site_key=jira_site_key,
        )


def lookup_jira_project_name(
    jira_project_key: str,
    jira_base_url: str,
    credentials: Credentials,
) -> str:
    """Resolve and validate the display name for a Jira project key."""
    project_key = jira_project_key.strip()
    if not project_key:
        raise ValueError("Jira project key is required.")
    if not credentials.atlassian_username or not credentials.atlassian_token:
        raise ValueError(
            "Atlassian credentials are required to look up the Jira project name."
        )
    client = JiraClient(
        base_url=jira_base_url,
        username=credentials.atlassian_username,
        token=credentials.atlassian_token,
        skip_ssl_verification=credentials.skip_ssl_verification,
    )
    project = client.get_project(project_key)
    returned_key = str(project.get("key") or "").strip()
    project_name = str(project.get("name") or "").strip()
    if returned_key.casefold() != project_key.casefold() or not project_name:
        raise ValueError(
            f"Jira did not return a valid project for key {project_key!r}."
        )
    return project_name


def prompt_for_unmapped_jira_project(
    database: Database,
    projects: List[Tuple[str, str]],
    jira_site_key: Optional[str] = None,
    jira_base_url: Optional[str] = None,
    credentials: Optional[Credentials] = None,
) -> int:
    """Offer an interactive batch association when the export scope has gaps."""
    with database.connect() as connection:
        database.initialize(connection)
        existing = database.load_jira_project_mappings(
            connection, jira_site_key=jira_site_key
        )
    missing = [(gid, name) for gid, name in projects if gid not in existing]
    if not missing or not sys.stdin.isatty():
        return 0

    print(f"{len(missing)} selected project(s) have no Jira project association.")
    try:
        response = input(
            "Assign one Jira project key to all of them? [y/N]: "
        ).strip().lower()
        if response not in {"y", "yes"}:
            return 0
        jira_project_key = input("Jira project key (e.g. MIG): ").strip()
    except EOFError:
        logger.info("Skipping Jira project association prompt (input unavailable).")
        return 0
    if not jira_project_key:
        logger.warning("Jira project key is required; no associations were added.")
        return 0
    if not jira_base_url or credentials is None:
        logger.warning(
            "Jira connection details are required to look up the project name; no associations were added."
        )
        return 0
    try:
        jira_project_name = lookup_jira_project_name(
            jira_project_key, jira_base_url, credentials
        )
    except (JiraApiError, ValueError) as exc:
        logger.warning(
            "Could not look up Jira project %s; no associations were added: %s",
            jira_project_key,
            exc,
        )
        return 0
    print(f"Using Jira project {jira_project_key}: {jira_project_name}")
    return assign_unmapped_jira_project(
        database, projects, jira_project_key, jira_project_name, jira_site_key
    )


def build_project_export_data(
    connection: sqlite3.Connection,
    project_gid: str,
    user_map: Dict[str, Dict[str, Optional[str]]],
    user_display_names: Dict[str, str],
    project_names: Dict[str, str],
    jira_base_url: str,
    custom_field_filters: Optional[List[str]],
    prompt_for_custom_fields: bool,
    stats_only: bool,
    user_emails: Optional[Dict[str, str]] = None,
    fallback_atlassian_id: Optional[str] = None,
    no_custom_fields: bool = False,
    add_epics: bool = False,
    selected_custom_field_ids: Optional[List[str]] = None,
    selected_custom_field_names: Optional[List[str]] = None,
) -> Dict:
    user_emails = user_emails if user_emails is not None else {}
    project_task_query = """
        WITH RECURSIVE project_tasks AS (
            SELECT t.*
            FROM tasks t
            JOIN task_projects tp ON tp.task_gid = t.gid
            WHERE tp.project_gid = ?
            UNION
            SELECT child.*
            FROM tasks child
            JOIN project_tasks parent ON parent.gid = child.parent_gid
        )
        SELECT DISTINCT
            pt.gid,
            pt.parent_gid,
            pt.name,
            pt.assignee_gid,
            assignee.name AS assignee_name,
            assignee.email AS assignee_email,
            creator.name AS creator_name,
            creator.email AS creator_email,
            pt.start_on,
            pt.start_at,
            pt.created_at,
            pt.due_on,
            pt.due_at,
            pt.completed,
            pt.completed_at,
            pt.actual_time_minutes,
            pt.notes,
            pt.html_notes,
            thf.flattened_parent_gid AS jira_parent_gid,
            thf.flattened_depth AS jira_hierarchy_depth,
            pt.hierarchy_depth,
            pt.created_by_gid,
            pt.completed_by_gid,
            pt.modified_at,
            pt.permalink_url,
            pt.resource_subtype,
            pt.workspace_gid,
            pt.workspace_name,
            pt.assignee_status,
            pt.assignee_status_updated_at,
            pt.is_subtask
        FROM project_tasks pt
        LEFT JOIN task_hierarchy_flattened thf ON thf.task_gid = pt.gid
        LEFT JOIN users assignee ON assignee.gid = pt.assignee_gid
        LEFT JOIN users creator ON creator.gid = pt.created_by_gid
        ORDER BY pt.hierarchy_depth ASC NULLS LAST, pt.gid
    """
    tasks = connection.execute(project_task_query, (project_gid,)).fetchall()
    task_gids = [row["gid"] for row in tasks if row["gid"]]

    projects_map: Dict[str, List[str]] = {}
    if task_gids:
        chunk_size = 500
        for start in range(0, len(task_gids), chunk_size):
            chunk = task_gids[start : start + chunk_size]
            placeholders = ",".join("?" for _ in chunk)
            rows = connection.execute(
                f"""
                SELECT tp.task_gid, p.name AS project_name, tp.project_gid
                FROM task_projects tp
                LEFT JOIN projects p ON p.gid = tp.project_gid
                WHERE tp.task_gid IN ({placeholders})
                ORDER BY tp.task_gid
                """,
                chunk,
            ).fetchall()
            for row in rows:
                entry = format_project_entry(row["project_name"], row["project_gid"])
                if not entry:
                    continue
                entries = projects_map.setdefault(row["task_gid"], [])
                if entry not in entries:
                    entries.append(entry)

    comment_rows: List[sqlite3.Row] = []
    if task_gids:
        chunk_size = 500
        for start in range(0, len(task_gids), chunk_size):
            chunk = task_gids[start : start + chunk_size]
            placeholders = ",".join("?" for _ in chunk)
            rows = connection.execute(
                f"""
                SELECT
                    tc.task_gid,
                    tc.created_at,
                    tc.created_by_gid,
                    tc.text,
                    tc.html_text,
                    users.email AS author_email
                FROM task_comments tc
                LEFT JOIN users ON users.gid = tc.created_by_gid
                WHERE tc.task_gid IN ({placeholders})
                  AND tc.created_by_gid IS NOT NULL
                  AND (tc.is_deleted IS NULL OR tc.is_deleted = 0)
                ORDER BY tc.task_gid, tc.created_at
                """,
                chunk,
            ).fetchall()
            comment_rows.extend(rows)
        comment_rows.sort(key=lambda row: (row["task_gid"], row["created_at"] or ""))

    section_rows: List[sqlite3.Row] = []
    if task_gids:
        chunk_size = 500
        for start in range(0, len(task_gids), chunk_size):
            chunk = task_gids[start : start + chunk_size]
            placeholders = ",".join("?" for _ in chunk)
            rows = connection.execute(
                f"""
                SELECT task_gid, section_name
                FROM task_projects
                WHERE project_gid = ?
                  AND task_gid IN ({placeholders})
                  AND section_name IS NOT NULL
                  AND section_name != ''
                ORDER BY task_gid, section_name
                """,
                (project_gid, *chunk),
            ).fetchall()
            section_rows.extend(rows)

    section_map: Dict[str, List[str]] = {}
    for row in section_rows:
        bucket = section_map.setdefault(row["task_gid"], [])
        if row["section_name"] not in bucket:
            bucket.append(row["section_name"])

    tags_map: Dict[str, List[str]] = {}
    if task_gids:
        chunk_size = 500
        for start in range(0, len(task_gids), chunk_size):
            chunk = task_gids[start : start + chunk_size]
            placeholders = ",".join("?" for _ in chunk)
            tag_rows = connection.execute(
                f"""
                SELECT task_gid, tag_name
                FROM task_tags
                WHERE task_gid IN ({placeholders})
                ORDER BY task_gid, tag_name
                """,
                chunk,
            ).fetchall()
            for row in tag_rows:
                raw_tag_name = (row["tag_name"] or "").strip()
                tag_name = normalize_label_value(raw_tag_name)
                if not tag_name:
                    continue
                entries = tags_map.setdefault(row["task_gid"], [])
                if tag_name not in entries:
                    entries.append(tag_name)

    attachment_names: Dict[str, str] = {}
    if task_gids:
        chunk_size = 500
        for start in range(0, len(task_gids), chunk_size):
            chunk = task_gids[start : start + chunk_size]
            placeholders = ",".join("?" for _ in chunk)
            rows = connection.execute(
                f"""
                SELECT gid, name
                FROM task_attachments
                WHERE task_gid IN ({placeholders})
                  AND (is_deleted IS NULL OR is_deleted = 0)
                """,
                chunk,
            ).fetchall()
            for row in rows:
                gid = row["gid"]
                if not gid:
                    continue
                name = (row["name"] or "").strip()
                attachment_names[gid] = jira_unique_attachment_name(name, gid)

    # If we injected epics, ensure they appear in the maps
    if add_epics:
        project_display = project_names.get(project_gid, project_gid)
        projects_map[project_gid] = [format_project_entry(project_display, project_gid)]
        # Ensure the synthetic epic lands in a section Jira can map; default to "Open".
        if project_gid not in section_map or not section_map.get(project_gid):
            section_map[project_gid] = ["Open"]
        if project_gid not in tags_map:
            tags_map[project_gid] = []

    max_sections = max((len(names) for names in section_map.values()), default=0)
    max_tags = max((len(tags) for tags in tags_map.values()), default=0)
    section_usage_count = sum(1 for names in section_map.values() if names)
    tag_usage_count = sum(1 for tags in tags_map.values() if tags)

    link_modifications: List[Dict[str, str]] = []

    def make_link_recorder(task_gid: str, field: str) -> Callable[[str, str], None]:
        def _record(original: str, modified: str) -> None:
            link_modifications.append(
                {
                    "task_gid": task_gid,
                    "field": field,
                    "original": original,
                    "modified": modified,
                }
            )

        return _record

    comments_by_task: Dict[str, List[str]] = {}
    missing_accounts: Dict[str, Set[str]] = {}
    associations: Dict[str, Counter] = {}
    last_active: Dict[str, str] = {}
    for row in comment_rows:
        task_gid = row["task_gid"]
        record_comment = make_link_recorder(task_gid, "comment") if not stats_only else None
        comment_string = format_comment_row(
            row,
            user_map,
            user_display_names,
            project_names,
            missing_accounts,
            jira_base_url,
            attachment_names,
            fallback_atlassian_id,
            record_comment,
            None,
        )
        comments_by_task.setdefault(task_gid, []).append(comment_string)
        record_user_association(row["created_by_gid"], "commenter", row["author_email"], associations, user_emails)

    if missing_accounts:
        details = []
        for author_gid, identifiers in sorted(missing_accounts.items()):
            details.append(f"{author_gid}: {', '.join(sorted(identifiers))}")
        logger.warning(
            "Missing Atlassian IDs for %s user(s); leaving comment author field blank. Details: %s",
            sum(len(ids) for ids in missing_accounts.values()),
            "; ".join(details),
        )
        # Backfill counts in case any commenter paths were logged without association counts.
        if not associations:
            for gid, identifiers in missing_accounts.items():
                record_user_association(
                    gid,
                    "commenter",
                    next(iter(identifiers)) if identifiers else "",
                    associations,
                    user_emails,
                )

    custom_fields: List[sqlite3.Row] = []
    if task_gids:
        chunk_size = 500
        seen_fields: Dict[str, sqlite3.Row] = {}
        for start in range(0, len(task_gids), chunk_size):
            chunk = task_gids[start : start + chunk_size]
            placeholders = ",".join("?" for _ in chunk)
            rows = connection.execute(
                f"""
                SELECT DISTINCT cf.gid, cf.name, cf.resource_subtype AS type
                FROM custom_fields cf
                JOIN task_custom_fields tcf ON tcf.custom_field_gid = cf.gid
                WHERE tcf.task_gid IN ({placeholders})
                ORDER BY cf.name
                """,
                chunk,
            ).fetchall()
            for row in rows:
                gid = row["gid"]
                if gid not in seen_fields:
                    seen_fields[gid] = row
        custom_fields = sorted(
            seen_fields.values(),
            key=lambda row: ((row["name"] or "").lower(), row["gid"]),
        )

    field_meta = []
    single_map = {}
    multi_map = {}
    option_map = {}
    field_usage: Dict[str, Set[str]] = defaultdict(set)
    field_multi_width: Dict[str, int] = {}

    if custom_fields and task_gids:
        custom_field_ids = [cf["gid"] for cf in custom_fields]

        single_rows: List[sqlite3.Row] = []
        chunk_size = 500
        for start in range(0, len(task_gids), chunk_size):
            chunk = task_gids[start : start + chunk_size]
            placeholders = ",".join("?" for _ in chunk)
            rows = connection.execute(
                f"""
                SELECT task_gid, custom_field_gid, enum_option_gid, text_value, number_value,
                       date_value, display_value, is_value_set
                FROM task_custom_fields
                WHERE task_gid IN ({placeholders})
            """,
                chunk,
            ).fetchall()
            single_rows.extend(rows)
        has_single_value = {cf["gid"]: False for cf in custom_fields}
        for row in single_rows:
            single_map[(row["task_gid"], row["custom_field_gid"])] = row
            if (
                row["enum_option_gid"]
                or row["text_value"]
                or row["number_value"] is not None
                or row["date_value"]
                or row["display_value"]
            ):
                has_single_value[row["custom_field_gid"]] = True
                field_usage[row["custom_field_gid"]].add(row["task_gid"])

        multi_rows: List[sqlite3.Row] = []
        for start in range(0, len(task_gids), chunk_size):
            chunk = task_gids[start : start + chunk_size]
            placeholders = ",".join("?" for _ in chunk)
            rows = connection.execute(
                f"""
                SELECT task_gid, custom_field_gid, enum_option_gid, position
                FROM task_custom_field_multi_enum
                WHERE task_gid IN ({placeholders})
                ORDER BY task_gid, custom_field_gid, position
                """,
                chunk,
            ).fetchall()
            multi_rows.extend(rows)
        has_multi_value = {cf["gid"]: False for cf in custom_fields}
        for row in multi_rows:
            key = (row["task_gid"], row["custom_field_gid"])
            multi_map.setdefault(key, []).append((row["position"], row["enum_option_gid"]))
            has_multi_value[row["custom_field_gid"]] = True
            field_usage[row["custom_field_gid"]].add(row["task_gid"])
        for values in multi_map.values():
            values.sort(key=lambda pair: pair[0] if pair[0] is not None else 0)
        for (task_gid, field_gid), values in multi_map.items():
            count = len(values)
            if count:
                field_multi_width[field_gid] = max(field_multi_width.get(field_gid, 0), count)

        field_placeholders = ",".join("?" for _ in custom_field_ids)
        option_rows = connection.execute(
            f"""
            SELECT custom_field_gid, enum_option_gid, name
            FROM custom_field_enum_options
            WHERE custom_field_gid IN ({field_placeholders})
            """,
            custom_field_ids,
        ).fetchall()
        option_map = {
            (row["custom_field_gid"], row["enum_option_gid"]): row["name"] for row in option_rows
        }

        for cf in custom_fields:
            gid = cf["gid"]
            field_type = (cf["type"] or "").lower()

            max_multi = 0
            if field_type == "multi_enum":
                max_multi = field_multi_width.get(gid, 0)

            has_any_value = has_single_value.get(gid) or has_multi_value.get(gid)
            if not has_any_value:
                continue

            field_meta.append(
                {
                    "gid": gid,
                    "name": cf["name"],
                    "type": field_type,
                    "max_multi": max_multi,
                }
            )

    available_fields = list(field_meta)
    field_usage_counts = {gid: len(task_ids) for gid, task_ids in field_usage.items() if task_ids}
    field_names = {cf["gid"]: cf["name"] for cf in custom_fields}

    for meta in field_meta:
        meta["value_count"] = field_usage_counts.get(meta["gid"], 0)

    if no_custom_fields:
        available_fields = []
        field_meta = []
        field_usage_counts = {}
        field_names = {}
        option_map = {}
        single_map = {}
        multi_map = {}
        field_multi_width = {}
        interactive_filters = None
    else:
        interactive_filters = custom_field_filters
        if (
            prompt_for_custom_fields
            and not stats_only
            and not custom_field_filters
            and available_fields
        ):
            interactive_filters = prompt_for_custom_field_choice(
                project_names.get(project_gid, project_gid),
                project_gid,
                available_fields,
            ) or None

        log_custom_field_list(project_gid, available_fields, "Custom fields with data")
        filtered_fields, unmatched_filters = apply_custom_field_filter(available_fields, interactive_filters)
        if interactive_filters and unmatched_filters:
            logger.warning(
                "Requested custom fields not found for project %s: %s",
                project_gid,
                ", ".join(unmatched_filters),
            )
        if interactive_filters:
            log_custom_field_list(project_gid, filtered_fields, "Custom fields selected for export")
        if selected_custom_field_ids is not None:
            selected_custom_field_ids.clear()
            selected_custom_field_ids.extend([meta["gid"] for meta in filtered_fields])
        if selected_custom_field_names is not None:
            selected_custom_field_names.clear()
            selected_custom_field_names.extend([meta["name"] for meta in filtered_fields])
        field_meta = filtered_fields

    max_comments = max((len(items) for items in comments_by_task.values()), default=0)
    parent_lookup: Dict[str, Optional[str]] = {
        row["gid"]: row["parent_gid"] for row in tasks if row["gid"]
    }
    collaborators_by_task = exclude_task_owner_collaborators(
        load_task_collaborators(connection, (row["gid"] for row in tasks)), tasks
    )
    max_collaborators = max((len(values) for values in collaborators_by_task.values()), default=0)

    bundle = {
        "tasks": tasks,
        "comments_by_task": comments_by_task,
        "collaborators_by_task": collaborators_by_task,
        "max_collaborators": max_collaborators,
        "section_map": section_map,
        "tags_map": tags_map,
        "projects_map": projects_map,
        "attachment_names": attachment_names,
        "field_meta": field_meta,
        "available_fields": available_fields,
        "field_usage_counts": field_usage_counts,
        "field_names": field_names,
        "max_sections": max_sections,
        "max_tags": max_tags,
        "max_comments": max_comments,
        "link_modifications": link_modifications,
        "project_name": project_names.get(project_gid, project_gid),
        "option_map": option_map,
        "single_map": single_map,
        "multi_map": multi_map,
        "field_multi_width": field_multi_width,
        "missing_accounts": missing_accounts,
        "associations": associations,
        "last_active": last_active,
    }
    return bundle


def export_project(
    database_path: str,
    project_gid: str,
    output_dir: Path,
    filename: Optional[str] = None,
    stats_only: bool = False,
    custom_field_filters: Optional[List[str]] = None,
    no_custom_fields: bool = False,
    prompt_for_custom_fields: bool = True,
    jira_base_url: Optional[str] = None,
    export_id: Optional[str] = None,
    fallback_atlassian_id: Optional[str] = None,
    incremental_decision: Optional[bool] = None,
    incremental_project_breakdown: Optional[str] = None,
    selected_custom_field_ids: Optional[List[str]] = None,
    selected_custom_field_names: Optional[List[str]] = None,
    skipped_log: Optional[List[Dict[str, str]]] = None,
    add_epics: bool = False,
    add_section_epics: bool = False,
    task_gids: Optional[Set[str]] = None,
    seen_task_gids: Optional[Set[str]] = None,
    jira_site_key: Optional[str] = None,
) -> Optional[Path]:
    database = Database(db_path=database_path)
    with database.connect() as refresh_connection:
        database.initialize(refresh_connection)
        latest_modified = fetch_project_latest_modified(refresh_connection, project_gid)
    prompt_and_run_incremental_refresh(
        project_gid,
        latest_modified,
        None,
        decision=incremental_decision,
        project_breakdown=incremental_project_breakdown,
    )

    connection = database.connect()

    try:
        database.initialize(connection)
        user_map = database.load_atlassian_account_map(connection)
        user_display_names = load_user_display_names(connection)
        user_emails = load_user_emails(connection)
        project_names = load_project_names(connection)
        jira_project_map = database.load_jira_project_mappings(
            connection, jira_site_key=jira_site_key
        )
        jira_base_url = normalize_jira_base_url(jira_base_url)
        if not jira_base_url:
            raise ValueError("Jira base URL is required.")
        last_active: Dict[str, str] = {}

        project_task_query = """
            WITH RECURSIVE project_tasks AS (
                SELECT t.*
                FROM tasks t
                JOIN task_projects tp ON tp.task_gid = t.gid
                WHERE tp.project_gid = ?
                UNION
                SELECT child.*
                FROM tasks child
                JOIN project_tasks parent ON parent.gid = child.parent_gid
            )
            SELECT DISTINCT
                pt.gid,
                pt.parent_gid,
                pt.name,
                pt.assignee_gid,
                assignee.name AS assignee_name,
                assignee.email AS assignee_email,
                creator.name AS creator_name,
                creator.email AS creator_email,
                pt.start_on,
                pt.start_at,
                pt.created_at,
                pt.due_on,
                pt.due_at,
                pt.completed,
                pt.completed_at,
                pt.actual_time_minutes,
                pt.notes,
                pt.html_notes,
                pt.permalink_url,
                pt.workspace_gid,
                thf.flattened_parent_gid AS jira_parent_gid,
                thf.flattened_depth AS jira_hierarchy_depth,
                pt.hierarchy_depth,
                pt.created_by_gid,
                pt.completed_by_gid
            FROM project_tasks pt
            LEFT JOIN task_hierarchy_flattened thf ON thf.task_gid = pt.gid
            LEFT JOIN users assignee ON assignee.gid = pt.assignee_gid
            LEFT JOIN users creator ON creator.gid = pt.created_by_gid
            ORDER BY pt.hierarchy_depth ASC NULLS LAST, pt.gid
        """

        def fetch_project_task_rows() -> List[sqlite3.Row]:
            return connection.execute(project_task_query, (project_gid,)).fetchall()

        tasks = [dict(row) for row in fetch_project_task_rows()]
        if task_gids is not None:
            tasks = [task for task in tasks if task["gid"] in task_gids]
        if not tasks:
            raise SystemExit(f"No tasks found for project {project_gid}.")

        exported_task_gids: Set[str] = set()
        exported_task_projects: Dict[str, str] = {}
        if export_id:
            exported_task_projects = database.get_exported_task_projects(connection, export_id)
            exported_task_gids = set(exported_task_projects.keys())
            if exported_task_gids:
                logger.info(
                    "Found %s previously exported task(s) for export_id %s; they will be skipped.",
                    len(exported_task_gids),
                    export_id,
                )
            retained_tasks = []
            for task in tasks:
                gid = task["gid"]
                if gid in exported_task_gids:
                    if skipped_log is not None:
                        existing_proj_gid = exported_task_projects.get(gid) or ""
                        skipped_log.append(
                            {
                                "task_gid": gid,
                                "skipped_project": project_names.get(project_gid, project_gid),
                                "existing_project": project_names.get(existing_proj_gid, existing_proj_gid),
                                "export_id": export_id,
                            }
                        )
                    continue
                retained_tasks.append(task)
            tasks = retained_tasks
            if not tasks:
                logger.info(
                    "All tasks for project %s are already recorded for export_id %s; skipping export.",
                    project_gid,
                    export_id,
                )
                return None

        if seen_task_gids is not None:
            tasks = [task for task in tasks if task["gid"] not in seen_task_gids]
            if not tasks:
                logger.info(
                    "All tasks for project %s were already included in this export run; skipping.",
                    project_gid,
                )
                return None
        exported_in_this_project = {task["gid"] for task in tasks if task.get("gid")}

        # Optionally add a synthetic project epic and re-parent top-level tasks
        if add_epics:
            project_display = project_names.get(project_gid, project_gid)
            epic_row = {
                "gid": project_gid,
                "parent_gid": "",
                "hierarchy_depth": -1,
                "jira_parent_gid": "",
                "jira_hierarchy_depth": -1,
                "name": project_display,
                "assignee_gid": "",
                "assignee_name": "",
                "assignee_email": "",
                "creator_name": "",
                "creator_email": "",
                "creator_atlassian_id": "",
                "creator_atlassian_email": "",
                "created_by_gid": "",
                "completed_by_gid": "",
                "start_on": "",
                "start_at": "",
                "created_at": "",
                "due_on": "",
                "due_at": "",
                "completed": 0,
                "completed_at": "",
                "actual_time_minutes": None,
                "notes": "",
                "html_notes": "",
                "project_num_tasks": project_num_tasks,
                "project_num_incomplete_tasks": project_num_incomplete_tasks,
                "project_num_completed_tasks": project_num_completed_tasks,
                "project_task_counts_captured_at": project_task_counts_captured_at,
            }
            # Place the synthetic epic first so Jira imports see the parent before children.
            tasks.insert(0, epic_row)
            for task in tasks:
                if not task.get("jira_parent_gid") and task.get("jira_hierarchy_depth") == 0:
                    task["jira_parent_gid"] = project_gid

        def _needs_flattened_refresh(rows: List[sqlite3.Row]) -> bool:
            return any(
                row["hierarchy_depth"] is not None and row["jira_hierarchy_depth"] is None
                for row in rows
            )

        if _needs_flattened_refresh(tasks):
            logger.info(
                "Flattened hierarchy data missing for project %s; rebuilding task_hierarchy_flattened.",
                project_gid,
            )
            database.rebuild_flattened_task_hierarchy(connection)
            tasks = [dict(row) for row in fetch_project_task_rows()]
            if task_gids is not None:
                tasks = [task for task in tasks if task["gid"] in task_gids]
            if _needs_flattened_refresh(tasks):
                missing_gids = [
                    row["gid"]
                    for row in tasks
                    if row["hierarchy_depth"] is not None and row["jira_hierarchy_depth"] is None
                ]
                logger.warning(
                    "Flattened hierarchy entries still missing for %s task(s) in project %s; proceeding with blanks. Examples: %s",
                    len(missing_gids),
                    project_gid,
                    ", ".join(missing_gids[:5]),
                )

        active_creator_fallback = _build_active_creator_fallback(
            tasks,
            user_map,
            user_display_names,
            user_emails,
            fallback_atlassian_id,
        )

        current_project_row = connection.execute(
            """
            SELECT
                name,
                permalink_url,
                num_tasks,
                num_incomplete_tasks,
                num_completed_tasks,
                task_counts_captured_at
            FROM projects
            WHERE gid = ?
            """,
            (project_gid,),
        ).fetchone()
        task_activity_row = fetch_project_task_activity(connection, project_gid)
        project_name = current_project_row["name"] if current_project_row else None
        project_num_tasks = current_project_row["num_tasks"] if current_project_row else None
        project_num_incomplete_tasks = (
            current_project_row["num_incomplete_tasks"] if current_project_row else None
        )
        project_num_completed_tasks = (
            current_project_row["num_completed_tasks"] if current_project_row else None
        )
        project_task_counts_captured_at = (
            current_project_row["task_counts_captured_at"] if current_project_row else None
        )
        project_last_task_created_at = (
            task_activity_row["last_task_created_at"] if task_activity_row else None
        )
        project_last_task_resolved_at = (
            task_activity_row["last_task_resolved_at"] if task_activity_row else None
        )
        project_last_task_updated_at = (
            task_activity_row["last_task_updated_at"] if task_activity_row else None
        )
        current_project_display = format_project_entry(
            project_name,
            project_gid,
        )

        task_gids = [task["gid"] for task in tasks]
        projects_map: Dict[str, List[str]] = {}
        if task_gids:
            chunk_size = 500
            for start in range(0, len(task_gids), chunk_size):
                chunk = task_gids[start : start + chunk_size]
                placeholders = ",".join("?" for _ in chunk)
                project_rows = connection.execute(
                    f"""
                    SELECT tp.task_gid, tp.project_gid, p.name AS project_name
                    FROM task_projects tp
                    LEFT JOIN projects p ON p.gid = tp.project_gid
                    WHERE tp.task_gid IN ({placeholders})
                    ORDER BY tp.task_gid, COALESCE(p.name, tp.project_gid)
                    """,
                    chunk,
                ).fetchall()
                for row in project_rows:
                    entry = format_project_entry(row["project_name"], row["project_gid"])
                    if not entry:
                        continue
                    entries = projects_map.setdefault(row["task_gid"], [])
                    if entry not in entries:
                        entries.append(entry)

        comment_rows: List[sqlite3.Row] = []
        if task_gids:
            chunk_size = 500
            for start in range(0, len(task_gids), chunk_size):
                chunk = task_gids[start : start + chunk_size]
                placeholders = ",".join("?" for _ in chunk)
                rows = connection.execute(
                    f"""
                    SELECT
                        tc.task_gid,
                        tc.created_at,
                        tc.created_by_gid,
                        tc.text,
                        tc.html_text,
                        users.email AS author_email
                    FROM task_comments tc
                    LEFT JOIN users ON users.gid = tc.created_by_gid
                    WHERE tc.task_gid IN ({placeholders})
                      AND tc.created_by_gid IS NOT NULL
                      AND (tc.is_deleted IS NULL OR tc.is_deleted = 0)
                    ORDER BY tc.task_gid, tc.created_at
                    """,
                    chunk,
                ).fetchall()
                comment_rows.extend(rows)
            comment_rows.sort(key=lambda row: (row["task_gid"], row["created_at"] or ""))

        section_rows: List[sqlite3.Row] = []
        if task_gids:
            chunk_size = 500
            for start in range(0, len(task_gids), chunk_size):
                chunk = task_gids[start : start + chunk_size]
                placeholders = ",".join("?" for _ in chunk)
                rows = connection.execute(
                    f"""
                    SELECT task_gid, section_name
                    FROM task_projects
                    WHERE project_gid = ?
                      AND task_gid IN ({placeholders})
                      AND section_name IS NOT NULL
                      AND section_name != ''
                    ORDER BY task_gid, section_name
                    """,
                    (project_gid, *chunk),
                ).fetchall()
                section_rows.extend(rows)

        section_map: Dict[str, List[str]] = {}
        for row in section_rows:
            bucket = section_map.setdefault(row["task_gid"], [])
            if row["section_name"] not in bucket:
                bucket.append(row["section_name"])

        tags_map: Dict[str, List[str]] = {}
        if task_gids:
            chunk_size = 500
            for start in range(0, len(task_gids), chunk_size):
                chunk = task_gids[start : start + chunk_size]
                placeholders = ",".join("?" for _ in chunk)
                tag_rows = connection.execute(
                    f"""
                    SELECT task_gid, tag_name
                    FROM task_tags
                    WHERE task_gid IN ({placeholders})
                    ORDER BY task_gid, tag_name
                    """,
                    chunk,
                ).fetchall()
                for row in tag_rows:
                    raw_tag_name = (row["tag_name"] or "").strip()
                    tag_name = normalize_label_value(raw_tag_name)
                    if not tag_name:
                        continue
                    entries = tags_map.setdefault(row["task_gid"], [])
                    if tag_name not in entries:
                        entries.append(tag_name)

        attachment_names: Dict[str, str] = {}
        if task_gids:
            chunk_size = 500
            for start in range(0, len(task_gids), chunk_size):
                chunk = task_gids[start : start + chunk_size]
                placeholders = ",".join("?" for _ in chunk)
                rows = connection.execute(
                    f"""
                    SELECT gid, name
                    FROM task_attachments
                    WHERE task_gid IN ({placeholders})
                      AND (is_deleted IS NULL OR is_deleted = 0)
                    """,
                    chunk,
                ).fetchall()
                for row in rows:
                    gid = row["gid"]
                    if not gid:
                        continue
                    name = (row["name"] or "").strip()
                    attachment_names[gid] = jira_unique_attachment_name(name, gid)

        # Optionally add a synthetic epic per section and re-parent top-level tasks.
        if add_section_epics:
            seen_slugs: Set[str] = set()

            def _slugify_section(section_name: str) -> str:
                base = FILENAME_SAFE_PATTERN.sub("_", section_name.strip()) if section_name else "section"
                base = base.strip("_") or "section"
                slug = base
                counter = 2
                while slug in seen_slugs:
                    slug = f"{base}_{counter}"
                    counter += 1
                seen_slugs.add(slug)
                return slug

            section_names: List[str] = sorted(
                {sec for names in section_map.values() for sec in names if sec},
                key=lambda value: value.lower(),
            )
            section_epic_ids: Dict[str, str] = {
                name: f"{project_gid}::section::{_slugify_section(name)}" for name in section_names
            }

            section_epics: List[Dict[str, object]] = []
            for name in section_names:
                epic_gid = section_epic_ids[name]
                section_epics.append(
                    {
                        "gid": epic_gid,
                        "parent_gid": "",
                        "hierarchy_depth": -1,
                        "jira_parent_gid": "",
                        "jira_hierarchy_depth": -1,
                        "name": name,
                        "assignee_gid": "",
                        "assignee_name": "",
                        "assignee_email": "",
                        "creator_name": "",
                        "creator_email": "",
                        "creator_atlassian_id": "",
                        "creator_atlassian_email": "",
                        "created_by_gid": "",
                        "completed_by_gid": "",
                        "start_on": "",
                        "start_at": "",
                        "created_at": "",
                        "due_on": "",
                        "due_at": "",
                        "completed": 0,
                        "completed_at": "",
                        "actual_time_minutes": None,
                        "notes": "",
                        "html_notes": "",
                        "project_num_tasks": project_num_tasks,
                        "project_num_incomplete_tasks": project_num_incomplete_tasks,
                        "project_num_completed_tasks": project_num_completed_tasks,
                        "project_task_counts_captured_at": project_task_counts_captured_at,
                        "project_last_task_created_at": project_last_task_created_at,
                        "project_last_task_resolved_at": project_last_task_resolved_at,
                        "project_last_task_updated_at": project_last_task_updated_at,
                    }
                )
                section_map[epic_gid] = [name]
                projects_map[epic_gid] = [current_project_display]
                tags_map[epic_gid] = []

            tasks[0:0] = section_epics

            for task in tasks[len(section_epics) :]:
                if task.get("jira_parent_gid") or task.get("jira_hierarchy_depth") != 0:
                    continue
                sections = section_map.get(task["gid"], [])
                primary_section = sections[0] if sections else ""
                epic_gid = section_epic_ids.get(primary_section)
                if epic_gid:
                    task["jira_parent_gid"] = epic_gid

        max_sections = max((len(names) for names in section_map.values()), default=0)
        max_tags = max((len(tags) for tags in tags_map.values()), default=0)
        section_usage_count = sum(1 for names in section_map.values() if names)
        tag_usage_count = sum(1 for tags in tags_map.values() if tags)

        link_modifications: List[Dict[str, str]] = []

        def make_link_recorder(task_gid: str, field: str) -> Callable[[str, str], None]:
            def _record(original: str, modified: str) -> None:
                link_modifications.append(
                    {
                        "task_gid": task_gid,
                        "field": field,
                        "original": original,
                        "modified": modified,
                    }
                )

            return _record

        comments_by_task: Dict[str, List[str]] = {}
        missing_accounts: Dict[str, Set[str]] = {}
        associations: Dict[str, Counter] = {}
        for row in comment_rows:
            task_gid = row["task_gid"]
            record_comment = make_link_recorder(task_gid, "comment") if not stats_only else None
            comment_string = format_comment_row(
                row,
                user_map,
                user_display_names,
                project_names,
                missing_accounts,
                jira_base_url,
                attachment_names,
                fallback_atlassian_id,
                record_comment,
                None,
            )
            comments_by_task.setdefault(task_gid, []).append(comment_string)
            record_user_association(row["created_by_gid"], "commenter", row["author_email"], associations, user_emails)
            if row["created_at"]:
                try:
                    dt = parse_iso_datetime(row["created_at"])
                    if dt:
                        last_active[row["created_by_gid"]] = max(
                            last_active.get(row["created_by_gid"], ""), dt.date().isoformat()
                        )
                except Exception:
                    pass

        if missing_accounts:
            details = []
            for author_gid, identifiers in sorted(missing_accounts.items()):
                details.append(f"{author_gid}: {', '.join(sorted(identifiers))}")
            logger.warning(
                "Missing Atlassian IDs for %s user(s); leaving comment author field blank. Details: %s",
                sum(len(ids) for ids in missing_accounts.values()),
                "; ".join(details),
            )

        custom_fields: List[sqlite3.Row] = []
        if task_gids:
            chunk_size = 500
            seen_fields: Dict[str, sqlite3.Row] = {}
            for start in range(0, len(task_gids), chunk_size):
                chunk = task_gids[start : start + chunk_size]
                placeholders = ",".join("?" for _ in chunk)
                rows = connection.execute(
                    f"""
                    SELECT DISTINCT cf.gid, cf.name, cf.resource_subtype AS type
                    FROM custom_fields cf
                    JOIN task_custom_fields tcf ON tcf.custom_field_gid = cf.gid
                    WHERE tcf.task_gid IN ({placeholders})
                    ORDER BY cf.name
                    """,
                    chunk,
                ).fetchall()
                for row in rows:
                    gid = row["gid"]
                    if gid not in seen_fields:
                        seen_fields[gid] = row
            custom_fields = sorted(
                seen_fields.values(),
                key=lambda row: ((row["name"] or "").lower(), row["gid"]),
            )

        field_meta = []
        single_map = {}
        multi_map = {}
        option_map = {}
        field_usage: Dict[str, Set[str]] = defaultdict(set)
        field_multi_width: Dict[str, int] = {}

        if custom_fields and task_gids:
            custom_field_ids = [cf["gid"] for cf in custom_fields]

            single_rows: List[sqlite3.Row] = []
            chunk_size = 500
            for start in range(0, len(task_gids), chunk_size):
                chunk = task_gids[start : start + chunk_size]
                placeholders = ",".join("?" for _ in chunk)
                rows = connection.execute(
                    f"""
                    SELECT task_gid, custom_field_gid, enum_option_gid, text_value, number_value,
                           date_value, display_value, is_value_set
                    FROM task_custom_fields
                    WHERE task_gid IN ({placeholders})
                """,
                    chunk,
                ).fetchall()
                single_rows.extend(rows)
            has_single_value = {cf["gid"]: False for cf in custom_fields}
            for row in single_rows:
                single_map[(row["task_gid"], row["custom_field_gid"])] = row
                if (
                    row["enum_option_gid"]
                    or row["text_value"]
                    or row["number_value"] is not None
                    or row["date_value"]
                    or row["display_value"]
                ):
                    has_single_value[row["custom_field_gid"]] = True
                    field_usage[row["custom_field_gid"]].add(row["task_gid"])

            multi_rows: List[sqlite3.Row] = []
            for start in range(0, len(task_gids), chunk_size):
                chunk = task_gids[start : start + chunk_size]
                placeholders = ",".join("?" for _ in chunk)
                rows = connection.execute(
                    f"""
                    SELECT task_gid, custom_field_gid, enum_option_gid, position
                    FROM task_custom_field_multi_enum
                    WHERE task_gid IN ({placeholders})
                    ORDER BY task_gid, custom_field_gid, position
                    """,
                    chunk,
                ).fetchall()
                multi_rows.extend(rows)
            has_multi_value = {cf["gid"]: False for cf in custom_fields}
            for row in multi_rows:
                key = (row["task_gid"], row["custom_field_gid"])
                multi_map.setdefault(key, []).append((row["position"], row["enum_option_gid"]))
                has_multi_value[row["custom_field_gid"]] = True
                field_usage[row["custom_field_gid"]].add(row["task_gid"])
            for values in multi_map.values():
                values.sort(key=lambda pair: pair[0] if pair[0] is not None else 0)
            for (task_gid, field_gid), values in multi_map.items():
                count = len(values)
                if count:
                    field_multi_width[field_gid] = max(field_multi_width.get(field_gid, 0), count)

            field_placeholders = ",".join("?" for _ in custom_field_ids)
            option_rows = connection.execute(
                f"""
                SELECT custom_field_gid, enum_option_gid, name
                FROM custom_field_enum_options
                WHERE custom_field_gid IN ({field_placeholders})
                """,
                custom_field_ids,
            ).fetchall()
            option_map = {
                (row["custom_field_gid"], row["enum_option_gid"]): row["name"] for row in option_rows
            }

            for cf in custom_fields:
                gid = cf["gid"]
                field_type = (cf["type"] or "").lower()

                max_multi = 0
                if field_type == "multi_enum":
                    max_multi = field_multi_width.get(gid, 0)

                has_any_value = has_single_value.get(gid) or has_multi_value.get(gid)
                if not has_any_value:
                    continue

                field_meta.append(
                    {
                        "gid": gid,
                        "name": cf["name"],
                        "type": field_type,
                        "max_multi": max_multi,
                    }
                )

        available_fields = list(field_meta)
        field_usage_counts = {gid: len(task_ids) for gid, task_ids in field_usage.items() if task_ids}
        field_names = {cf["gid"]: cf["name"] for cf in custom_fields}

        for meta in field_meta:
            meta["value_count"] = field_usage_counts.get(meta["gid"], 0)

        available_fields = list(field_meta)

        if no_custom_fields:
            available_fields = []
            field_meta = []
            field_usage_counts = {}
            field_names = {}
            option_map = {}
            single_map = {}
            multi_map = {}
            field_multi_width = {}
            interactive_filters = None
        else:
            interactive_filters = custom_field_filters
            if (
                prompt_for_custom_fields
                and not stats_only
                and not custom_field_filters
                and available_fields
            ):
                interactive_filters = prompt_for_custom_field_choice(
                    project_name or project_gid,
                    project_gid,
                    available_fields,
                ) or None

            log_custom_field_list(project_gid, available_fields, "Custom fields with data")
            filtered_fields, unmatched_filters = apply_custom_field_filter(available_fields, interactive_filters)
            if interactive_filters:
                if unmatched_filters:
                    logger.warning(
                        "Requested custom fields not found for project %s: %s",
                        project_gid,
                        ", ".join(unmatched_filters),
                    )
                log_custom_field_list(project_gid, filtered_fields, "Custom fields selected for export")
            field_meta = filtered_fields

        header = [
            "current_project",
            "jira_project_name",
            "jira_project_key",
            "jira_project_type",
            "Asana Project Link",
            "Asana Task Link",
            "task_gid",
            JIRA_TASK_ID_FIELD_NAME,
            "parent_gid",
            "hierarchy_depth",
            "jira_parent_gid",
            "jira_hierarchy_depth",
            "task_name",
            "assignee",
            "assignee_email",
            "assignee_atlassian_id",
            "creator",
            "creator_email",
            "creator_atlassian_id",
            "projects",
            "modified_section",
        ]
        # Keep the raw Section column(s) adjacent to modified_section.
        if max_sections:
            for idx in range(1, max_sections + 1):
                suffix = f" ({idx})" if idx > 1 else ""
                header.append(f"Section{suffix}")
        header.extend(
            [
                "start_on",
                "created_at",
                "due_date",
                "completed",
                "completed_at",
                "actual_time_minutes",
            ]
        )
        if max_tags:
            header.extend(["Tags"] * max_tags)
        for meta in field_meta:
            if meta["type"] == "multi_enum" and meta["max_multi"] > 1:
                for idx in range(1, meta["max_multi"] + 1):
                    suffix = f" ({idx})" if idx > 1 else ""
                    header.append(f"{meta['name']}{suffix}")
            else:
                header.append(meta["name"])
        # notes sits immediately before the comment columns.
        header.append("notes")
        max_comments = max((len(items) for items in comments_by_task.values()), default=0)
        header.extend(["Comment"] * max_comments)

        rows: List[List[str]] = []
        resolved_workspace_gid = next(
            (
                (task.get("workspace_gid") or "").strip()
                for task in tasks
                if (task.get("workspace_gid") or "").strip()
            ),
            None,
        ) or infer_workspace_gid(connection)
        project_asana_link = build_project_url(resolved_workspace_gid, project_gid) or (
            (current_project_row["permalink_url"] or "") if current_project_row else ""
        )
        parent_lookup_local = {row["gid"]: row["parent_gid"] for row in tasks if row["gid"]}
        collaborators_by_task = exclude_task_owner_collaborators(
            load_task_collaborators(connection, (row["gid"] for row in tasks)), tasks
        )
        max_collaborators = max(
            (len(values) for values in collaborators_by_task.values()), default=0
        )
        header[header.index("projects"):header.index("projects")] = collaborator_column_headers(
            max_collaborators
        )

        def _sort_task_key(task: dict) -> tuple:
            depth = task.get("hierarchy_depth")
            depth_key = (depth is None, depth if depth is not None else 10**9)
            created_raw = task.get("created_at")
            created_dt = parse_iso_datetime(created_raw) if created_raw else None
            created_key = (created_dt is None, created_dt or datetime.max.replace(tzinfo=timezone.utc))
            gid_key = task.get("gid") or ""
            return (depth_key, created_key, gid_key)

        tasks.sort(key=_sort_task_key)
        section_counts: Dict[str, int] = defaultdict(int)
        full_contents: List[Dict[str, str]] = []
        for task in tasks:
            due_source = task["due_on"] or task["due_at"] or ""
            due_date = format_export_datetime(due_source)
            completed_flag = ""
            if task["completed"] is not None:
                completed_flag = "Yes" if task["completed"] else "No"
            depth = task["hierarchy_depth"]
            hierarchy_value = "" if depth is None else str(depth)
            jira_parent_gid = task["jira_parent_gid"] or ""
            jira_depth_raw = task["jira_hierarchy_depth"]
            jira_depth_value = format_jira_hierarchy_depth(jira_depth_raw)
            assignee_gid = task["assignee_gid"]
            if not _is_explicitly_inactive_atlassian_user(user_map, assignee_gid) or not active_creator_fallback:
                assignee_display_name = format_user_display_name(task["assignee_gid"], task["assignee_name"])
                assignee_email = task["assignee_email"] or ""
                assignee_atl, _ = resolve_atlassian_identity(
                    user_map, assignee_gid, fallback_atlassian_id
                )
                assignee_gid_effective = assignee_gid
            else:
                assignee_display_name = active_creator_fallback["display_name"]
                assignee_email = active_creator_fallback["email"]
                assignee_atl = active_creator_fallback["atlassian_id"]
                assignee_gid_effective = active_creator_fallback["user_gid"]
            record_user_association(
                assignee_gid_effective,
                "assignee",
                assignee_email,
                associations,
                user_emails,
            )
            if task["created_at"]:
                try:
                    dt_created = parse_iso_datetime(task["created_at"])
                    if dt_created:
                        last_active[assignee_gid_effective] = max(
                            last_active.get(assignee_gid_effective, ""), dt_created.date().isoformat()
                        )
                except Exception:
                    pass
            creator_gid = task["created_by_gid"]
            creator_atl, _ = resolve_atlassian_identity(
                user_map, creator_gid, fallback_atlassian_id
            )
            record_user_association(
                creator_gid,
                "creator",
                task["creator_email"],
                associations,
                user_emails,
            )
            if task["created_at"]:
                try:
                    dt_created = parse_iso_datetime(task["created_at"])
                    if dt_created:
                        last_active[creator_gid] = max(
                            last_active.get(creator_gid, ""), dt_created.date().isoformat()
                        )
                except Exception:
                    pass
            completed_by_gid = task.get("completed_by_gid")
            record_user_association(
                completed_by_gid,
                "completed_by",
                None,
                associations,
                user_emails,
            )
            collaborator_gids = collaborators_by_task.get(task["gid"], [])
            for collaborator_gid in collaborator_gids:
                record_user_association(
                    collaborator_gid,
                    "collaborator",
                    user_emails.get(collaborator_gid),
                    associations,
                    user_emails,
                )
            collaborator_values = format_task_collaborator_values(
                collaborator_gids,
                user_display_names,
                user_emails,
                user_map,
                fallback_atlassian_id,
            )
            collaborator_values.extend(
                [""] * (max_collaborators * 3 - len(collaborator_values))
            )
            if task.get("completed_at"):
                try:
                    dt_completed = parse_iso_datetime(task["completed_at"])
                    if dt_completed:
                        last_active[completed_by_gid] = max(
                            last_active.get(completed_by_gid, ""), dt_completed.date().isoformat()
                        )
                except Exception:
                    pass
            project_entries = projects_map.get(task["gid"], [])
            projects_value = "; ".join(project_entries)
            actual_time_value = (
                "" if task["actual_time_minutes"] is None else str(task["actual_time_minutes"])
            )
            start_on_value = format_export_datetime(task["start_on"])
            created_at_value = format_export_datetime(task["created_at"])
            completed_at_value = format_export_datetime(task["completed_at"])

            profile_overrides = extract_profile_gid_overrides(task["html_notes"])
            note_recorder = make_link_recorder(task["gid"], "notes")
            raw_notes = prefer_html_text_with_links(task["notes"], task["html_notes"])
            notes_value = transform_asana_links(
                raw_notes,
                user_map,
                user_display_names,
                project_names,
                jira_base_url,
                attachment_names,
                profile_overrides,
                fallback_atlassian_id,
                note_recorder,
            )
            full_contents.append(
                {"task_gid": task["gid"], "field": "notes", "content": notes_value}
            )

            # Resolve sections (with inheritance)
            sections = section_map.get(task["gid"], [])
            if not sections and task["parent_gid"]:
                ancestor = task["parent_gid"]
                visited: Set[str] = set()
                while ancestor and ancestor not in visited:
                    visited.add(ancestor)
                    anc_sections = section_map.get(ancestor, [])
                    if anc_sections:
                        sections = anc_sections
                        break
                    ancestor = parent_lookup_local.get(ancestor)
            for sec in sections:
                if sec:
                    section_counts[sec] += 1
            primary_section = sections[0] if sections else ""
            if task["completed"]:
                modified_section = "Done"
            elif primary_section.lower() == "done":
                modified_section = "In Progress"
            else:
                modified_section = primary_section

            jira_proj = jira_project_map.get(project_gid, {})
            is_synthetic_epic = task.get("hierarchy_depth") == -1
            task_workspace_gid = task.get("workspace_gid") or resolved_workspace_gid
            task_asana_link = "" if is_synthetic_epic else (
                build_task_url(task_workspace_gid, task["gid"])
                or task.get("permalink_url")
                or ""
            )
            row = [
                current_project_display,
                jira_proj.get("jira_project_name", ""),
                jira_proj.get("jira_project_key", ""),
                "software",
                project_asana_link,
                task_asana_link,
                task["gid"],
                task["gid"],
                task["parent_gid"] or "",
                hierarchy_value,
                jira_parent_gid,
                jira_depth_value,
                export_task_name(task["name"], task["created_at"]),
                assignee_display_name,
                assignee_email,
                assignee_atl,
                format_user_display_name(task["created_by_gid"], task["creator_name"]),
                task["creator_email"] or "",
                creator_atl,
                *collaborator_values,
                projects_value,
                modified_section,
            ]
            if max_sections:
                row.extend(sections)
                if len(sections) < max_sections:
                    row.extend([""] * (max_sections - len(sections)))
            row.extend(
                [
                    start_on_value,
                    created_at_value,
                    due_date,
                    completed_flag,
                    completed_at_value,
                    actual_time_value,
                ]
            )
            if max_tags:
                tags = tags_map.get(task["gid"], [])
                row.extend(tags)
                if len(tags) < max_tags:
                    row.extend([""] * (max_tags - len(tags)))
            for meta in field_meta:
                key = (task["gid"], meta["gid"])
                if meta["type"] == "multi_enum" and meta["max_multi"] > 1:
                    values = [""] * meta["max_multi"]
                    for position, enum_gid in multi_map.get(key, []):
                        option_name = option_map.get((meta["gid"], enum_gid), "")
                        if 0 <= position < len(values):
                            values[position] = option_name
                    row.extend(values)
                else:
                    single = single_map.get(key)
                    cell = ""
                    if single:
                        if meta["type"] in {"enum", "multi_enum"} and single["enum_option_gid"]:
                            cell = option_map.get(
                                (meta["gid"], single["enum_option_gid"])
                            ) or single["display_value"] or ""
                        elif single["display_value"]:
                            cell = single["display_value"]
                        elif single["text_value"]:
                            cell = single["text_value"]
                        elif single["number_value"] is not None:
                            cell = str(single["number_value"])
                        elif single["date_value"]:
                            cell = format_export_datetime(single["date_value"])
                    row.append(cell)
            row.append(notes_value)
            if max_comments:
                comments = comments_by_task.get(task["gid"], [])
                if comments:
                    for idx, comment_text in enumerate(comments, start=1):
                        full_contents.append(
                            {
                                "task_gid": task["gid"],
                                "field": f"comment_{idx}",
                                "content": comment_text,
                            }
                        )
                row.extend(comments)
                if len(comments) < max_comments:
                    row.extend([""] * (max_comments - len(comments)))
            rows.append(row)

        if rows:
            required_columns = {
                "task_gid",
                JIRA_TASK_ID_FIELD_NAME,
                "parent_gid",
                "hierarchy_depth",
                "jira_parent_gid",
                "jira_hierarchy_depth",
                "jira_project_name",
                "jira_project_key",
                "jira_project_type",
                "Asana Project Link",
                "Asana Task Link",
            }
            columns_to_keep: List[int] = []
            for index, name in enumerate(header):
                if name in required_columns:
                    columns_to_keep.append(index)
                    continue
                has_data = any(
                    bool((row[index].strip() if isinstance(row[index], str) else row[index]))
                    for row in rows
                )
                if has_data:
                    columns_to_keep.append(index)
            header = [header[idx] for idx in columns_to_keep]
            rows = [[row[idx] for idx in columns_to_keep] for row in rows]

        output_dir.mkdir(parents=True, exist_ok=True)
        if filename:
            export_filename = ensure_tasks_suffix(filename)
        else:
            export_filename = build_default_filename(project_name or project_gid)
        output_path = output_dir / export_filename
        if not stats_only:
            with output_path.open("w", newline="", encoding="utf-8") as fp:
                writer = csv.writer(fp)
                writer.writerow(header)
                writer.writerows(rows)

        # Write section counts for this project
        if section_counts:
            section_path = output_path.with_name(f"{output_path.stem}_section_counts.csv")
            with section_path.open("w", newline="", encoding="utf-8") as fp:
                writer = csv.writer(fp)
                writer.writerow(["section_name", "task_count", "project"])
                for name, count in sorted(section_counts.items(), key=lambda item: item[0].lower()):
                    writer.writerow([name, count, project_name or project_gid])
            logger.info("Wrote section counts for project %s to %s", project_gid, section_path)

        if available_fields:
            exported_field_ids = {meta["gid"] for meta in field_meta}
            summary_rows = []
            for meta in sorted(available_fields, key=lambda item: item["name"].lower()):
                summary_rows.append(
                    [
                        meta["name"],
                        meta["gid"],
                        meta.get("value_count", 0),
                        "Yes" if meta["gid"] in exported_field_ids else "No",
                    ]
                )
            summary_path = output_path.with_name(f"{output_path.stem}_field_counts.csv")
            with summary_path.open("w", newline="", encoding="utf-8") as fp:
                writer = csv.writer(fp)
                writer.writerow(
                    ["custom_field_name", "custom_field_gid", "values_found", "included_in_export"]
                )
                writer.writerows(summary_rows)
            logger.info(
                "Wrote custom field usage summary for project %s to %s",
                project_gid,
                summary_path,
            )

        report_path = write_link_modifications_report(output_path, link_modifications, full_contents)
        logger.info(
            "Wrote link modification report for project %s to %s",
            project_gid,
            report_path,
        )

        # Ensure missing_accounts from comment author detection are merged into associations
        for gid, identifiers in missing_accounts.items():
            counts = associations.setdefault(gid, Counter())
            if counts.get("commenter", 0) == 0:
                record_user_association(
                    gid,
                    "commenter",
                    next(iter(identifiers)) if identifiers else None,
                    associations,
                    user_emails,
                )

        assoc_report = write_user_association_report(
            output_path,
            associations,
            user_map,
            user_display_names,
            user_emails,
            project_name or project_gid,
            last_active,
        )
        logger.info(
            "Wrote user association report for project %s to %s",
            project_gid,
            assoc_report,
        )

        if export_id:
            recorded = database.record_exported_tasks(
                connection,
                export_id,
                (task["gid"] for task in tasks),
                project_gid,
                current_utc_time(),
            )
            logger.info(
                "Recorded %s task(s) for export_id %s after exporting project %s.",
                recorded,
                export_id,
                project_gid,
            )
        if seen_task_gids is not None:
            seen_task_gids.update(exported_in_this_project)

        return output_path
    finally:
        connection.close()


def format_comment_row(
    row: sqlite3.Row,
    user_map: Dict[str, Dict[str, Optional[str]]],
    user_display_names: Dict[str, str],
    project_names: Dict[str, str],
    missing_accounts: Dict[str, Set[str]],
    jira_base_url: str,
    attachment_names: Optional[Dict[str, str]] = None,
    fallback_account_id: Optional[str] = None,
    record_transformation: Optional[Callable[[str, str], None]] = None,
    record_missing_account: Optional[Callable[[str, Optional[str]], None]] = None,
) -> str:
    timestamp = format_comment_timestamp(row["created_at"])
    author_gid = row["created_by_gid"]
    account_id, _ = resolve_atlassian_identity(user_map, author_gid, fallback_account_id)
    if author_gid and not account_id:
        identifier = row["author_email"] or author_gid
        missing_accounts.setdefault(author_gid, set()).add(identifier)
        if record_missing_account:
            record_missing_account(author_gid, row["author_email"])
    profile_overrides = extract_profile_gid_overrides(row["html_text"])
    raw_text = prefer_html_text_with_links(row["text"], row["html_text"])
    text = transform_asana_links(
        raw_text,
        user_map,
        user_display_names,
        project_names,
        jira_base_url,
        attachment_names,
        profile_overrides,
        fallback_account_id,
        record_transformation,
    )
    base = f"{timestamp};{account_id};"
    if text and text.strip():
        return f"{base} {text}"
    return f"{base} {blank_comment_placeholder(row['created_at'])}"


def _creation_date(value: Optional[str]) -> str:
    """Return an ISO calendar date for a creation timestamp when available."""
    parsed = parse_iso_datetime(value)
    return parsed.date().isoformat() if parsed else ""


def blank_task_title_placeholder(created_at: Optional[str]) -> str:
    """Provide a Jira-importable summary for an Asana task with no title."""
    return f"--Blank Title--{_creation_date(created_at)}"


def blank_comment_placeholder(created_at: Optional[str]) -> str:
    """Provide visible content for an Asana comment with no text."""
    return f"--Blank Comment--{_creation_date(created_at)}"


def export_task_name(name: Optional[str], created_at: Optional[str]) -> str:
    """Preserve unnamed tasks by substituting the requested dated title."""
    return name if name and name.strip() else blank_task_title_placeholder(created_at)


def format_comment_timestamp(value: Optional[str]) -> str:
    if not value:
        return ""
    parsed = parse_iso_datetime(value)
    if not parsed:
        return ""
    utc_time = parsed.astimezone(timezone.utc)
    date_part = utc_time.strftime("%d/%b/%y")
    time_part = utc_time.strftime("%I:%M %p").lstrip("0")
    if not time_part:
        time_part = utc_time.strftime("%I:%M %p")
    return f"{date_part} {time_part}"


def parse_iso_datetime(value: str) -> Optional[datetime]:
    try:
        cleaned = value.strip()
        if cleaned.endswith("Z"):
            cleaned = cleaned[:-1] + "+00:00"
        parsed = datetime.fromisoformat(cleaned)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except (ValueError, AttributeError):
        logger.warning("Unable to parse timestamp '%s'", value)
        return None


def format_export_datetime(value: Optional[str]) -> str:
    if not value:
        return ""
    parsed = parse_iso_datetime(value)
    if not parsed:
        return ""
    utc_time = parsed.astimezone(timezone.utc)
    date_part = utc_time.strftime("%d/%b/%y")
    time_part = utc_time.strftime("%I:%M %p")
    time_part = time_part.lstrip("0") or utc_time.strftime("%I:%M %p")
    return f"{date_part} {time_part.lower()}"


def format_jira_hierarchy_depth(value: Optional[int]) -> str:
    """Return the Jira issue-type label represented by a flattened depth."""
    labels = {-1: "Epic", 0: "Task", 1: "Sub-task"}
    if value is None:
        return ""
    return labels.get(value, str(value))


def format_project_entry(name: Optional[str], gid: Optional[str]) -> str:
    if name and gid:
        if name == gid:
            return name
        return f"{name} ({gid})"
    if name:
        return name
    return gid or ""


def write_link_modifications_report(
    output_path: Path,
    modifications: List[Dict[str, str]],
    full_contents: Optional[List[Dict[str, str]]] = None,
) -> Path:
    report_path = output_path.with_name(f"{output_path.stem}_link_modifications.csv")
    fieldnames = ["task_gid", "field", "original", "modified", "full_content"]
    rows: List[Dict[str, str]] = []
    modified_keys: Set[Tuple[str, str]] = set()
    modified_fields_by_task: DefaultDict[str, Set[str]] = defaultdict(set)
    for entry in modifications:
        # Keep only entries where something actually changed
        modified_text = entry.get("modified")
        if not modified_text:
            continue
        key = (entry.get("task_gid", ""), entry.get("field", ""))
        modified_keys.add(key)
        if key[0]:
            modified_fields_by_task[key[0]].add(key[1])
        rows.append(
            {
                "task_gid": entry.get("task_gid", ""),
                "field": entry.get("field", ""),
                "original": entry.get("original", ""),
                "modified": modified_text,
                "full_content": "",
            }
        )
    if full_contents and modified_keys:
        modified_tasks = set(modified_fields_by_task.keys())
        for entry in full_contents:
            key = (entry.get("task_gid", ""), entry.get("field", ""))
            task_id, field = key
            if task_id not in modified_tasks:
                continue
            task_fields = modified_fields_by_task.get(task_id, set())
            # Allow comment_* full contents when any comment on the task was modified.
            if field.startswith("comment") and ("comment" in task_fields or field in task_fields):
                pass
            elif field not in task_fields:
                continue
            # Attach full content to a matching modification row for the same task/field.
            attached = False
            for row in rows:
                if row["task_gid"] != task_id:
                    continue
                # Map comment_* full content to the generic "comment" modification entry.
                if field.startswith("comment") and row["field"] == "comment":
                    row["full_content"] = entry.get("content", "")
                    attached = True
                    break
                if row["field"] == field:
                    row["full_content"] = entry.get("content", "")
                    attached = True
                    break
            if not attached:
                rows.append(
                    {
                        "task_gid": entry.get("task_gid", ""),
                        "field": "comment" if field.startswith("comment") else entry.get("field", ""),
                        "original": "",
                        "modified": "",
                        "full_content": entry.get("content", ""),
                    }
                )
    with report_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        if rows:
            writer.writerows(rows)
        else:
            # Always emit a CSV with headers; add a note row to clarify emptiness.
            writer.writerow(
                {
                    "task_gid": "",
                    "field": "",
                    "original": "",
                    "modified": "No link modifications were recorded.",
                    "full_content": "",
                }
            )
    return report_path


def print_project_stats(
    project_gid: str,
    tasks: List[sqlite3.Row],
    comment_rows: List[sqlite3.Row],
    user_map: Dict[str, Dict[str, Optional[str]]],
    field_names: Dict[str, str],
    field_usage_counts: Dict[str, int],
    section_usage_count: int,
    tag_usage_count: int,
    connection: sqlite3.Connection,
) -> None:
    heading = f"Project {project_gid} statistics"
    print(heading)
    print("-" * len(heading))

    project_row = connection.execute(
        """
        SELECT
            name,
            num_tasks,
            num_incomplete_tasks,
            num_completed_tasks,
            task_counts_captured_at
        FROM projects
        WHERE gid = ?
        """,
        (project_gid,),
    ).fetchone()
    task_activity_row = fetch_project_task_activity(connection, project_gid)
    if project_row and (
        project_row["num_tasks"] is not None
        or project_row["num_incomplete_tasks"] is not None
        or project_row["num_completed_tasks"] is not None
        or project_row["task_counts_captured_at"]
    ):
        print("Project task count snapshot:")
        print(f"  Total tasks: {project_row['num_tasks'] if project_row['num_tasks'] is not None else 'n/a'}")
        print(
            f"  Incomplete tasks: {project_row['num_incomplete_tasks'] if project_row['num_incomplete_tasks'] is not None else 'n/a'}"
        )
        print(
            f"  Completed tasks: {project_row['num_completed_tasks'] if project_row['num_completed_tasks'] is not None else 'n/a'}"
        )
        captured_at = project_row["task_counts_captured_at"] or "n/a"
        print(f"  Captured at: {captured_at}")
    if task_activity_row and (
        task_activity_row["last_task_created_at"]
        or task_activity_row["last_task_resolved_at"]
        or task_activity_row["last_task_updated_at"]
    ):
        print("Project task activity:")
        print(f"  Last created: {task_activity_row['last_task_created_at'] or 'n/a'}")
        print(f"  Last resolved: {task_activity_row['last_task_resolved_at'] or 'n/a'}")
        print(f"  Last updated: {task_activity_row['last_task_updated_at'] or 'n/a'}")

    total_tasks = len(tasks)
    print(f"Total tasks: {total_tasks}")

    depth_counts: Dict[Optional[int], int] = defaultdict(int)
    flattened_counts: Dict[Optional[int], int] = defaultdict(int)
    flattened_available = False
    for task in tasks:
        depth_counts[task["hierarchy_depth"]] += 1
        jira_depth = task.get("jira_hierarchy_depth") if hasattr(task, "get") else None
        if jira_depth is None and hasattr(task, "keys") and "jira_hierarchy_depth" in task.keys():
            jira_depth = task["jira_hierarchy_depth"]
        flattened_counts[jira_depth] += 1
        if jira_depth is not None:
            flattened_available = True

    print("Tasks by hierarchy depth:")
    for depth in sorted(depth_counts.keys(), key=lambda value: (value is None, value if value is not None else -1)):
        label = "None" if depth is None else str(depth)
        print(f"  Depth {label}: {depth_counts[depth]}")

    if flattened_available:
        print("Tasks by flattened hierarchy depth:")
        for depth in sorted(
            flattened_counts.keys(), key=lambda value: (value is None, value if value is not None else -1)
        ):
            if depth is None:
                continue
            print(f"  Depth {depth}: {flattened_counts[depth]}")
    else:
        print("Flattened hierarchy depths: not available (run scripts.backfill_task_hierarchy_depth).")

    user_ids: Set[str] = set()
    for task in tasks:
        for key in ("assignee_gid", "created_by_gid", "completed_by_gid"):
            gid = task[key]
            if gid:
                user_ids.add(gid)
    for comment in comment_rows:
        if comment["created_by_gid"]:
            user_ids.add(comment["created_by_gid"])

    user_details: Dict[str, sqlite3.Row] = {}
    if user_ids:
        placeholders = ",".join("?" for _ in user_ids)
        rows = connection.execute(
            f"SELECT gid, name, email FROM users WHERE gid IN ({placeholders})",
            tuple(user_ids),
        ).fetchall()
        user_details = {row["gid"]: row for row in rows}

    print(f"Users referenced ({len(user_ids)}):")
    if user_ids:
        def sort_key(gid: str) -> tuple[str, str]:
            row = user_details.get(gid)
            name = row["name"].lower() if row and row["name"] else ""
            return (name, gid)

        for gid in sorted(user_ids, key=sort_key):
            row = user_details.get(gid)
            name = row["name"] if row and row["name"] else "<unknown>"
            email = row["email"] if row and row["email"] else "<no-email>"
            atl_id, atl_email = resolve_atlassian_identity(user_map, gid)
            atl_display = atl_id or "<missing>"
            atl_email_display = atl_email or "<no-atlassian-email>"
            print(
                f"  {name} | {email} | Asana: {gid} | Atlassian: {atl_display} | Atlassian email: {atl_email_display}"
            )
    else:
        print("  (none)")

    if not field_usage_counts and not section_usage_count and not tag_usage_count:
        print("Custom fields with values: none")
    else:
        print("Custom fields with values:")
        if section_usage_count:
            print(f"  Section: {section_usage_count} task(s)")
        if tag_usage_count:
            print(f"  Tags: {tag_usage_count} task(s)")
        for gid in sorted(field_usage_counts.keys(), key=lambda key: field_names.get(key, key)):
            name = field_names.get(gid, gid)
            print(f"  {name}: {field_usage_counts[gid]} task(s)")


def main(argv: Optional[Iterable[str]] = None) -> None:
    args = parse_args(argv)
    unmapped_jira_project_key = (args.map_unmapped_to_jira_project_key or "").strip()
    unmapped_jira_project_name = (args.map_unmapped_to_jira_project_name or "").strip()
    if bool(unmapped_jira_project_key) != bool(unmapped_jira_project_name):
        raise SystemExit(
            "--map-unmapped-to-jira-project-key and --map-unmapped-to-jira-project-name must be used together."
        )
    try:
        log_level = resolve_log_level(args.log_level)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    configure_logging(log_level)

    database = Database(db_path=args.database)

    output_root = Path(args.output_dir)
    if not args.export_id:
        output_root = next_export_dir(
            output_root, export_id="stats" if args.stats_only else None
        )
    if args.clear_export_id:
        with database.connect() as connection:
            database.initialize(connection)
            cleared = database.clear_export_history_for_id(connection, args.clear_export_id)
        logger.info(
            "Cleared %s recorded export_id/task mapping(s) for export_id %s.",
            cleared,
            args.clear_export_id,
        )
        return
    if args.clear_export_ids:
        with database.connect() as connection:
            database.initialize(connection)
            cleared = database.clear_export_history(connection)
        logger.info("Cleared %s recorded export_id/task mapping(s).", cleared)
        return
    if args.clear_jira_project_map:
        with database.connect() as connection:
            database.initialize(connection)
            cleared = database.clear_jira_project_mappings(connection)
        logger.info("Cleared %s Jira project association(s).", cleared)
        return

    custom_field_filters = (
        [value.strip() for value in args.custom_fields.split(",") if value.strip()]
        if args.custom_fields
        else None
    )
    credentials = load_credentials()
    try:
        jira_base_url = normalize_jira_base_url(resolve_jira_base_url(credentials, prod=args.prod, site_key=args.jira_site))
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if not jira_base_url:
        raise SystemExit("Jira base URL is required.")
    output_dir = output_root
    # Auto-group exports with an export_id into numbered folders when using the default output dir.
    if args.export_id and output_dir == Path(DEFAULT_OUTPUT_DIR):
        output_dir = next_export_dir(Path(DEFAULT_OUTPUT_DIR), args.export_id)
        logger.info("Using grouped export directory %s for export_id %s", output_dir, args.export_id)
    output_dir.mkdir(parents=True, exist_ok=True)

    with database.connect() as connection:
        database.initialize(connection)
        project_scope = any(
            (args.project_gid, args.project_gids, args.project_gids_file, args.team_gid)
        )
        task_scope = any((args.task_gids, args.task_gids_file))
        if project_scope and task_scope:
            raise SystemExit("Project and task scope options cannot be combined.")
        if task_scope:
            requested_task_gids = parse_task_gids(args.task_gids, args.task_gids_file)
            projects, task_filters = resolve_task_projects(
                connection,
                requested_task_gids,
                logger,
            )
        else:
            projects = parse_project_list(
                connection=connection,
                project_gid=args.project_gid,
                project_gids=args.project_gids,
                project_gids_file=args.project_gids_file,
                team_gid=args.team_gid,
                archived_scope=args.archived_scope,
            )
            task_filters = {}

    if unmapped_jira_project_key:
        added_mappings = assign_unmapped_jira_project(
            database,
            projects,
            unmapped_jira_project_key,
            unmapped_jira_project_name,
            args.jira_site,
        )
        logger.info(
            "Added %s Jira project association(s) for previously unmapped selected project(s).",
            added_mappings,
        )
    else:
        added_mappings = prompt_for_unmapped_jira_project(
            database,
            projects,
            args.jira_site,
            jira_base_url,
            credentials,
        )
        if added_mappings:
            logger.info(
                "Added %s Jira project association(s) for previously unmapped selected project(s).",
                added_mappings,
            )

    project_count = len(projects)
    # Suppress repeated per-project breakdown prompts during incremental refresh when exporting many projects.
    incremental_breakdown = args.incremental_project_breakdown
    if incremental_breakdown is None and project_count > 1:
        incremental_breakdown = "no"

    if args.no_refresh_prompt:
        incremental_decision: Optional[bool] = False
    elif args.incremental_refresh == "yes":
        incremental_decision = True
    elif args.incremental_refresh == "no":
        incremental_decision = False
    else:
        incremental_decision = None

    cached_custom_field_ids: List[str] = []
    cached_custom_field_names: List[str] = []
    reuse_custom_fields = project_count > 1 and not args.stats_only and not args.no_custom_fields
    skipped_log: List[Dict[str, str]] = []
    seen_task_gids: Set[str] = set()
    per_project_skipped: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    task_parts: List[CsvPart] = []
    field_count_files: List[Path] = []
    link_mod_files: List[Path] = []
    section_files: List[Path] = []
    user_assoc_files: List[Path] = []

    if reuse_custom_fields:
        union_fields: Dict[str, str] = {}
        with database.connect() as connection:
            database.initialize(connection)
            for gid, name in projects:
                fields = preview_project_custom_fields(connection, gid)
                if fields:
                    logger.info(
                        "Project %s (%s) has %s custom field(s) with data: %s",
                        name,
                        gid,
                        len(fields),
                        ", ".join(sorted({fname for _, fname in fields if fname})),
                    )
                    for cf_gid, cf_name in fields:
                        union_fields.setdefault(cf_gid, cf_name)
                else:
                    logger.info("Project %s (%s) has no custom fields with data.", name, gid)

        # If user supplied --custom-fields, respect it; otherwise optionally prompt once using the union.
        if not args.custom_fields and not args.no_custom_field_prompt and not args.no_custom_fields:
            union_meta = [{"gid": gid, "name": name, "type": ""} for gid, name in union_fields.items()]
            union_meta.sort(key=lambda m: (m["name"] or m["gid"]))
            selected_union = prompt_for_custom_field_choice(
                "Multiple Projects",
                "multiple",
                union_meta,
            ) or None
            if selected_union:
                cached_custom_field_ids.extend([token for token in selected_union if token.isdigit() or token.isnumeric() or token.startswith("1")])
                cached_custom_field_names.extend([token for token in selected_union if token not in cached_custom_field_ids])
                custom_field_filters = selected_union
            else:
                custom_field_filters = None
        elif args.custom_fields:
            cached_custom_field_ids.extend(
                [value.strip() for value in args.custom_fields.split(",") if value.strip()]
            )

    failures = 0
    for index, (project_gid, project_name) in enumerate(projects):
        logger.info("Starting export for project %s (%s)", project_name, project_gid)
        custom_fields_for_project = custom_field_filters
        prompt_custom_fields = not args.no_custom_field_prompt
        selected_cache: Optional[List[str]] = None
        selected_cache_names: Optional[List[str]] = None
        if args.no_custom_fields:
            custom_fields_for_project = []
            prompt_custom_fields = False
        if reuse_custom_fields:
            if index == 0:
                selected_cache = cached_custom_field_ids
                selected_cache_names = cached_custom_field_names
                # If we already prompted at the union level, suppress per-project prompt.
                if custom_field_filters is not None:
                    prompt_custom_fields = False
            else:
                # Reuse the same filters for every project to keep columns consistent.
                reuse_tokens = custom_field_filters or cached_custom_field_ids or cached_custom_field_names
                custom_fields_for_project = list(reuse_tokens) if reuse_tokens else None
                prompt_custom_fields = False
        output_path = export_project(
            args.database,
            project_gid,
            output_dir,
            args.filename,
            args.stats_only,
            custom_fields_for_project,
            prompt_for_custom_fields=prompt_custom_fields,
            jira_base_url=jira_base_url,
            export_id=args.export_id,
            fallback_atlassian_id=args.fallback_atlassian_id,
            incremental_decision=incremental_decision,
            incremental_project_breakdown=incremental_breakdown,
            selected_custom_field_ids=selected_cache,
            selected_custom_field_names=selected_cache_names,
            skipped_log=skipped_log,
            add_epics=args.add_epics,
            no_custom_fields=args.no_custom_fields,
            add_section_epics=args.add_section_epics,
            task_gids=task_filters.get(project_gid),
            seen_task_gids=seen_task_gids,
            jira_site_key=args.jira_site,
        )
        if args.stats_only and output_path:
            logger.info("Generated statistics for project %s (%s).", project_name, project_gid)
        elif output_path:
            logger.info("Exported project %s (%s) to %s", project_name, project_gid, output_path)
        else:
            logger.warning(
                "Export skipped for project %s (%s); likely already exported for export_id %s",
                project_name,
                project_gid,
                args.export_id or "(none)",
            )
            continue

        if output_path:
            if not args.stats_only and output_path.exists():
                project_parts = partition_project_csv(
                    output_path,
                    synthetic_epic_limit=MAX_ISSUES_PER_SYNTHETIC_EPIC,
                )
                task_parts.extend(project_parts)
                if len(project_parts) > 1:
                    logger.info(
                        "Split project %s into %s Jira CSV parts: %s",
                        project_name,
                        len(project_parts),
                        ", ".join(
                            f"{part.path.name} ({part.issue_count:,} issues)"
                            for part in project_parts
                        ),
                    )
            section_file = output_path.with_name(f"{output_path.stem}_section_counts.csv")
            if section_file.exists():
                section_files.append(section_file)
            field_counts = output_path.with_name(f"{output_path.stem}_field_counts.csv")
            if field_counts.exists():
                field_count_files.append(field_counts)
            lm_csv = output_path.with_name(f"{output_path.stem}_link_modifications.csv")
            lm_txt = output_path.with_name(f"{output_path.stem}_link_modifications.txt")
            if lm_csv.exists():
                link_mod_files.append(lm_csv)
            elif lm_txt.exists():
                link_mod_files.append(lm_txt)
            missing_file = output_path.with_name(f"{output_path.stem}_missing_accounts.csv")
            if missing_file.exists():
                missing_account_files.append(missing_file)
            assoc_file = output_path.with_name(f"{output_path.stem}_user_associations.csv")
            if assoc_file.exists():
                user_assoc_files.append(assoc_file)

        if skipped_log:
            per_project_skipped[project_gid].extend(
                entry for entry in skipped_log if entry.get("skipped_project") == project_name
            )

    if skipped_log and not args.stats_only:
        timestamp = time.strftime('%Y%m%d-%H%M')
        skipped_path = output_dir / f"_Consolidated-{timestamp}_skipped_tasks.csv"
        with skipped_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=["task_gid", "skipped_project", "existing_project", "export_id"]
            )
            writer.writeheader()
            writer.writerows(skipped_log)
        logger.info("Wrote skipped-task report to %s", skipped_path)

        for project_gid, rows in per_project_skipped.items():
            if not rows:
                continue
            project_name = next((name for gid, name in projects if gid == project_gid), project_gid)
            project_path = output_dir / f"{sanitize_filename_component(project_name)}-{timestamp}_skipped_tasks.csv"
            with project_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(
                    handle, fieldnames=["task_gid", "skipped_project", "existing_project", "export_id"]
                )
                writer.writeheader()
                writer.writerows(rows)
            logger.info("Wrote per-project skipped-task report to %s", project_path)

    consolidated_timestamp: Optional[str] = None
    if task_parts or field_count_files or link_mod_files or section_files or user_assoc_files:
        timestamp = time.strftime('%Y%m%d-%H%M')
        consolidated_timestamp = timestamp

        if task_parts:
            consolidated_batches = pack_csv_parts(task_parts)
            consolidated_parts: List[CsvPart] = []
            for number, batch in enumerate(consolidated_batches, start=1):
                suffix = (
                    f"_part_{number:03d}" if len(consolidated_batches) > 1 else ""
                )
                consolidated_tasks = output_dir / f"_Consolidated-{timestamp}_tasks{suffix}.csv"
                merge_csv_files(
                    [part.path for part in batch],
                    consolidated_tasks,
                    source_column="project",
                    strip_suffix=None,
                )
                issue_count = sum(part.issue_count for part in batch)
                consolidated_parts.append(CsvPart(consolidated_tasks, issue_count))
                logger.info(
                    "Wrote consolidated tasks to %s (%s issues)",
                    consolidated_tasks,
                    f"{issue_count:,}",
                )
            manifest_path = output_dir / f"_Consolidated-{timestamp}_tasks_manifest.csv"
            write_partition_manifest(manifest_path, task_parts, consolidated_parts)
            logger.info("Wrote Jira CSV partition manifest to %s", manifest_path)

        if field_count_files:
            consolidated_fc = output_dir / f"_Consolidated-{timestamp}_tasks_field_counts.csv"
            merge_field_counts(field_count_files, consolidated_fc, source_column="project", strip_suffix=None)
            logger.info("Wrote consolidated field counts to %s", consolidated_fc)

        if link_mod_files:
            consolidated_lm = output_dir / f"_Consolidated-{timestamp}_tasks_link_modifications.csv"
            merge_link_mods(link_mod_files, consolidated_lm, source_column="project", strip_suffix=None)
            logger.info("Wrote consolidated link modifications to %s", consolidated_lm)

        if section_files:
            consolidated_sections = output_dir / f"_Consolidated-{timestamp}_tasks_section_counts.csv"
            merged_rows: List[Dict[str, str]] = []
            for path in section_files:
                with path.open("r", newline="", encoding="utf-8") as handle:
                    reader = csv.DictReader(handle)
                    for row in reader:
                        merged_rows.append(
                            {
                                "section_name": row.get("section_name", ""),
                                "task_count": row.get("task_count", ""),
                                "project": row.get("project", ""),
                            }
                        )
            if merged_rows:
                with consolidated_sections.open("w", newline="", encoding="utf-8") as handle:
                    writer = csv.DictWriter(handle, fieldnames=["section_name", "task_count", "project"])
                    writer.writeheader()
                    writer.writerows(merged_rows)
                logger.info("Wrote consolidated section counts to %s", consolidated_sections)

        if user_assoc_files:
            consolidated_assoc = output_dir / f"_Consolidated-{timestamp}_user_associations.csv"
            merged_rows: List[Dict[str, str]] = []
            for path in user_assoc_files:
                with path.open("r", newline="", encoding="utf-8") as handle:
                    reader = csv.DictReader(handle)
                    for row in reader:
                        merged_rows.append(row)
            if merged_rows:
                fieldnames = [
                    "user_gid",
                    "display_name",
                    "email",
                    "atlassian_account_id",
                    "atlassian_email",
                    "last_active_date",
                    "creator_count",
                    "assignee_count",
                    "completed_by_count",
                    "commenter_count",
                    "collaborator_count",
                    "project",
                ]
                with consolidated_assoc.open("w", newline="", encoding="utf-8") as handle:
                    writer = csv.DictWriter(handle, fieldnames=fieldnames)
                    writer.writeheader()
                    writer.writerows(merged_rows)
                logger.info("Wrote consolidated user association report to %s", consolidated_assoc)

    copied_config = copy_active_profile_import_config(output_dir)
    if copied_config:
        logger.info("Included active profile Jira importer configuration: %s", copied_config)
    organize_outputs(output_dir)

    if failures:
        raise SystemExit(f"{failures} project(s) failed to export.")


if __name__ == "__main__":
    main()
