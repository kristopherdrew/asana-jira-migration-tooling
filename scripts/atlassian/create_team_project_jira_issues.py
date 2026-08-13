#!/usr/bin/env python3
"""Create Jira Work Items for Asana teams and projects directly via the REST API.

A direct alternative to the CSV import produced by
``export_team_project_jira_csv.py``: this script POSTs Epics (one per team) and
child Tasks (one per project) to Jira Cloud, resolving issue-type and custom-field
IDs at runtime via ``createmeta``, linking each task to its team's epic by real
issue key, and persisting Asana→Jira mappings so re-runs skip work already done.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.lib.adf import text_to_adf
from src.lib.atlassian_status import is_active_atlassian_status
from src.lib.config import Credentials, load_credentials, resolve_jira_base_url
from src.lib.database import Database, resolve_default_db_path
from src.lib.jira_client import JiraApiError, JiraClient
from src.lib.logging_utils import configure_logging, resolve_log_level
from src.lib.team_picker import search_team
from scripts.atlassian.export_team_project_jira_csv import build_team_project_rows

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = "data/asana.sqlite"
DEFAULT_OUTPUT_DIR = "exports"
DEFAULT_WORKERS = 4

# When a creation is rate limited even after the Jira client's own retries, the
# item is requeued to the end of the run and retried up to this many extra rounds
# (with an escalating delay) before being recorded as a hard error.
MAX_RATE_LIMIT_ROUNDS = 3
RATE_LIMIT_REQUEUE_DELAY = 5.0  # seconds, multiplied by the round number

# Message recorded in the run report when an issue is created after dropping an
# assignee Jira refused to accept.
ASSIGNEE_DROPPED_MESSAGE = "assignee dropped (user not assignable)"

# Maps an internal row column to the Jira custom field *name* it should populate.
# Override individual entries with a --field-map JSON file when the Jira field is
# named differently. Standard fields (summary, description, assignee, labels,
# parent) are handled separately and are not listed here.
DEFAULT_FIELD_MAP: Dict[str, str] = {
    "Team URL": "Team (Asana)",
    "Project URL": "Project (Asana)",
    "Instructions": "Testing Instructions",
    "Project Status": "Asana Current Project Status",
    "Project Count (Asana)": "Project Count (Asana)",
    "Task Count (Asana)": "Task Count (Asana)",
    # Each project's most recent task-update date (YYYY-MM-DD) is set at creation
    # on the "Last working day" date field. The standalone
    # set_project_last_updated_jira_field.py updates this same field on existing
    # issues (default id customfield_11475).
    "Last Working Day": "customfield_11475",
}

REPORT_FIELDNAMES = ["kind", "asana_gid", "summary", "jira_key", "status", "message"]


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default=resolve_default_db_path(), help=f"Path to the SQLite database (default: {DEFAULT_DB_PATH}).")
    parser.add_argument("--workspace-gid", help="Optional Asana workspace GID used to build project URLs.")
    parser.add_argument(
        "--prod",
        action="store_true",
        help="Target the production Jira site (JIRA_BASE_URL). Default: sandbox (JIRA_SANDBOX_BASE_URL).",
    )
    parser.add_argument("--jira-site", help="Named Jira site key from the active profile.")
    parser.add_argument("--jira-project-key", required=True, help="Target Jira project key where issues are created (e.g., MIG).")
    parser.add_argument("--epic-issue-type", default="Epic", help="Issue type name for team epics (default: Epic).")
    parser.add_argument("--task-issue-type", default="Task", help="Issue type name for project tasks (default: Task).")
    parser.add_argument("--field-map", help="Optional JSON file overriding column -> Jira custom field name/id mappings.")
    parser.add_argument("--map-fields", action="store_true", help="Interactively map each custom field to a Jira field id (blank = resolve by field name via createmeta), then optionally save the mapping to a file for future --field-map runs.")
    team_group = parser.add_mutually_exclusive_group()
    team_group.add_argument("--team-gid", help="Restrict creation to a single Asana team by GID (its Epic plus that team's project Tasks).")
    team_group.add_argument("--pick-team", action="store_true", help="Interactively search for a team by name and restrict creation to the selected team.")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help=f"Concurrent workers for task creation (default: {DEFAULT_WORKERS}).")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help=f"Directory for the run report CSV (default: {DEFAULT_OUTPUT_DIR}/).")
    parser.add_argument("--limit", type=int, help="Cap the number of issues created this run (epics counted first). Skips are not counted.")
    parser.add_argument("--reset", action="store_true", help="Delete persisted Asana->Jira mappings for the in-scope rows before creating (use after deleting the issues in Jira so they get recreated). Honors --team-gid/--pick-team; without a team scope it resets every mapping.")
    parser.add_argument("--dry-run", action="store_true", help="Resolve fields and print the JSON payloads without creating issues.")
    parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt before creating issues.")
    parser.add_argument("--include-empty-projects", action="store_true", help="Create a Jira issue for projects that have zero synced tasks (default: skip empty projects).")
    parser.add_argument("--log-level", default="INFO", help="Logging verbosity (default: INFO).")
    return parser.parse_args(argv)


def ensure_atlassian_credentials(creds: Credentials) -> Tuple[str, str]:
    if not creds.atlassian_username or not creds.atlassian_token:
        raise ValueError("Resolved credentials must include 'ATLASSIAN_USERNAME' and 'ATLASSIAN_TOKEN' to create Jira issues.")
    return creds.atlassian_username, creds.atlassian_token


def load_field_map(path: Optional[str]) -> Dict[str, str]:
    """Return column -> Jira field name map, applying overrides from a JSON file."""
    field_map = dict(DEFAULT_FIELD_MAP)
    if not path:
        return field_map
    with open(path, "r", encoding="utf-8") as handle:
        overrides = json.load(handle)
    if not isinstance(overrides, dict):
        raise ValueError("--field-map JSON must be an object of column -> Jira field name.")
    for column, jira_name in overrides.items():
        field_map[str(column)] = str(jira_name)
    return field_map


def _resolve_field_def(
    field_defs: Dict[str, Dict[str, object]], reference: str
) -> Optional[Dict[str, object]]:
    """Resolve a field-map value that may be a Jira field *name* or a field *id*.

    Tries an exact field-name match first (the createmeta dict is keyed by name),
    then falls back to scanning for a matching ``fieldId`` so interactive id-based
    mappings work too. Returns the createmeta field definition or ``None``."""
    reference = (reference or "").strip()
    if not reference:
        return None
    if reference in field_defs:
        return field_defs[reference]
    for defn in field_defs.values():
        if str(defn.get("fieldId")) == reference:
            return defn
    return None


def prompt_field_map(
    field_map: Dict[str, str],
    field_defs_by_type: Dict[str, Dict[str, Dict[str, object]]],
    *,
    input_func=input,
) -> Dict[str, str]:
    """Interactively resolve each column to a Jira custom field.

    For every column, prints the default field name and the field id it resolves to
    via createmeta, then reads a field id from ``input_func``. A blank answer keeps
    the default field name (resolved by name at build time); a non-blank answer is
    stored verbatim as the field id. Returns the new column -> (id or name) map."""
    print("\nInteractive field mapping — for each Asana column, enter the Jira custom")
    print("field id to use, or press Enter to resolve it by field name via createmeta.")
    resolved: Dict[str, str] = {}
    for column, default_name in field_map.items():
        found_id = None
        for defs in field_defs_by_type.values():
            defn = _resolve_field_def(defs, default_name)
            if defn:
                found_id = str(defn.get("fieldId"))
                break
        hint = f"-> {found_id}" if found_id else "NOT found via createmeta"
        answer = input_func(
            f"  {column} [default name '{default_name}' {hint}] field id: "
        ).strip()
        if answer:
            if not any(_resolve_field_def(defs, answer) for defs in field_defs_by_type.values()):
                print(f"    ! warning: '{answer}' is not on any create screen; it will be sent as-is.")
            resolved[column] = answer
        else:
            resolved[column] = default_name
    return resolved


def save_field_map(field_map: Dict[str, str], path: Path) -> Path:
    """Write the field map as JSON (loadable later via ``--field-map``)."""
    with path.open("w", encoding="utf-8") as handle:
        json.dump(field_map, handle, indent=2, sort_keys=True)
    return path


def maybe_save_field_map(
    field_map: Dict[str, str],
    *,
    default_path: str = "field_map.json",
    input_func=input,
) -> Optional[Path]:
    """Ask whether to persist the mapping for future runs and write it if confirmed."""
    answer = input_func("\nSave this field mapping for future runs? [Y/n] ").strip().lower()
    if answer in {"n", "no"}:
        return None
    path_str = input_func(f"  File path [{default_path}]: ").strip() or default_path
    path = save_field_map(field_map, Path(path_str))
    print(f"  Saved field mapping to {path.resolve()} (reuse with --field-map {path}).")
    return path


def resolve_issue_type_id(issue_types: List[Dict[str, object]], name: str) -> str:
    target = name.strip().casefold()
    for issue_type in issue_types:
        if str(issue_type.get("name", "")).strip().casefold() == target:
            return str(issue_type.get("id"))
    available = ", ".join(sorted(str(it.get("name", "")) for it in issue_types))
    raise ValueError(f"Issue type '{name}' not found for project. Available: {available}")


def _is_active_status(status: Optional[str]) -> bool:
    return is_active_atlassian_status(status)


def _is_rich_text_schema(schema: Dict[str, object]) -> bool:
    """True for Jira fields that expect an ADF document over the v3 REST API: the
    system rich-text fields (createmeta ``type`` ``doc``) and "Paragraph" custom
    fields, which createmeta reports as ``type`` ``string`` with a ``:textarea``
    custom type even though the v3 API rejects a plain string for them."""
    if str(schema.get("type")) == "doc":
        return True
    return str(schema.get("custom", "")).endswith(":textarea")


def format_custom_value(schema: Dict[str, object], value: str) -> object:
    """Format a string value for a Jira custom field based on its createmeta schema."""
    field_type = str(schema.get("type", "string"))
    if field_type == "option":
        return {"value": value}
    if field_type == "array":
        items = str(schema.get("items", ""))
        if items == "option":
            return [{"value": value}]
        return [value]
    if field_type == "number":
        try:
            return float(value) if "." in value else int(value)
        except ValueError:
            return value
    if _is_rich_text_schema(schema):
        return text_to_adf(value)
    return value


def _is_unassignable_error(exc: Exception) -> bool:
    """True when Jira rejected the request specifically because of the assignee.

    Jira returns ``400`` with a per-field ``errors`` dict like
    ``{"errors": {"assignee": "User '...' cannot be assigned issues."}}`` when the
    (active) account can't be assigned issues in the target project. Parses the
    error body, falling back to a substring check if it isn't JSON."""
    if not isinstance(exc, JiraApiError) or exc.status != 400:
        return False
    body = getattr(exc, "body", "") or ""
    try:
        parsed = json.loads(body)
        errors = parsed.get("errors") if isinstance(parsed, dict) else None
        if isinstance(errors, dict) and "assignee" in errors:
            return True
    except (ValueError, TypeError):
        pass
    lowered = body.lower()
    return "assignee" in lowered and "cannot be assigned" in lowered


def _is_rate_limited(exc: Exception) -> bool:
    """True when the Jira client exhausted its retries on an HTTP 429."""
    return isinstance(exc, JiraApiError) and exc.status == 429


def _create_issue_with_assignee_fallback(
    client: JiraClient, payload: Dict[str, object]
) -> Tuple[Dict[str, object], bool]:
    """Create an issue, retrying once without the assignee if Jira rejects it.

    Returns ``(response, assignee_dropped)``. Only the assignee-rejected ``400``
    triggers the retry; every other error (including rate limits) propagates so
    the caller's own retry/requeue handling still applies."""
    try:
        return client.create_issue(payload), False
    except JiraApiError as exc:
        fields = payload.get("fields") if isinstance(payload, dict) else None
        if _is_unassignable_error(exc) and isinstance(fields, dict) and "assignee" in fields:
            retry_fields = {k: v for k, v in fields.items() if k != "assignee"}
            retry_payload = {**payload, "fields": retry_fields}
            return client.create_issue(retry_payload), True
        raise


def build_issue_payload(
    row: Dict[str, str],
    *,
    project_key: str,
    issue_type_id: str,
    field_defs: Dict[str, Dict[str, object]],
    custom_field_map: Dict[str, str],
    parent_key: Optional[str] = None,
) -> Dict[str, object]:
    """Assemble the ``{"fields": {...}}`` payload for one issue."""
    available_ids = {str(defn.get("fieldId")) for defn in field_defs.values()}
    fields: Dict[str, object] = {
        "project": {"key": project_key},
        "issuetype": {"id": issue_type_id},
        "summary": (row.get("Summary") or "").strip() or "(no summary)",
    }

    description = (row.get("Description") or "").strip()
    if "description" in available_ids:
        # Prefer a pre-built rich ADF document (Task rows) and fall back to converting
        # the plain-text description (Epic rows have no override).
        override = (row.get("_adf_overrides") or {}).get("Description")
        if override is not None:
            fields["description"] = override
        elif description:
            fields["description"] = text_to_adf(description)

    account_id = (row.get("Owner Atlassian ID") or "").strip()
    if account_id and "assignee" in available_ids and _is_active_status(row.get("Owner Atlassian Status")):
        fields["assignee"] = {"accountId": account_id}

    if "labels" in available_ids:
        labels = []
        for raw in (row.get("Owner Source"), row.get("Labels"), row.get("Project Type")):
            token = (raw or "").strip()
            if token:
                labels.append("_".join(token.split()))
        if labels:
            fields["labels"] = labels

    if parent_key and "parent" in available_ids:
        fields["parent"] = {"key": parent_key}

    adf_overrides = row.get("_adf_overrides") or {}
    for column, field_ref in custom_field_map.items():
        value = (row.get(column) or "").strip()
        if not value:
            continue
        defn = _resolve_field_def(field_defs, field_ref)
        if not defn:
            continue
        field_id = str(defn.get("fieldId"))
        schema = defn.get("schema") or {}
        # Prefer a pre-built rich ADF document when the Jira field is rich-text;
        # otherwise format the plain string per the field's schema type.
        override = adf_overrides.get(column)
        if override is not None and _is_rich_text_schema(schema):
            fields[field_id] = override
        else:
            fields[field_id] = format_custom_value(schema, value)

    return {"fields": fields}


def _resolve_missing_field_warnings(
    custom_field_map: Dict[str, str],
    field_defs: Dict[str, Dict[str, object]],
    issue_type_name: str,
) -> None:
    for column, field_ref in custom_field_map.items():
        if _resolve_field_def(field_defs, field_ref) is None:
            logger.warning(
                "Custom field '%s' (for column '%s') is not available on the %s create screen; values will be skipped.",
                field_ref,
                column,
                issue_type_name,
            )


def _filter_rows_to_team(
    epic_rows: List[Dict[str, str]],
    task_rows: List[Dict[str, str]],
    team_gid: str,
) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
    """Restrict rows to a single team: its Epic plus that team's project Tasks.

    The synthetic ``No Team`` epic and its orphan-project tasks are excluded; only
    real teams are addressable by GID (and selectable via the interactive picker)."""
    epics = [r for r in epic_rows if r.get("_kind") == "team" and r.get("_asana_gid") == team_gid]
    tasks = [r for r in task_rows if r.get("_parent_kind") == "team" and r.get("_parent_asana_gid") == team_gid]
    return epics, tasks


def write_report(rows: List[Dict[str, str]], output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=REPORT_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)
    return output_path


def create_team_project_jira_issues(
    database_path: str,
    jira_base_url: str,
    jira_project_key: str,
    output_dir: Path,
    *,
    workspace_gid: Optional[str] = None,
    epic_issue_type: str = "Epic",
    task_issue_type: str = "Task",
    field_map_path: Optional[str] = None,
    map_fields: bool = False,
    workers: int = DEFAULT_WORKERS,
    limit: Optional[int] = None,
    reset: bool = False,
    dry_run: bool = False,
    assume_yes: bool = False,
    team_gid: Optional[str] = None,
    pick_team: bool = False,
    include_empty_projects: bool = False,
    client: Optional[JiraClient] = None,
) -> Path:
    credentials = load_credentials()
    username, token = ensure_atlassian_credentials(credentials)
    custom_field_map = load_field_map(field_map_path)

    database = Database(db_path=database_path)
    with database.connect() as connection:
        database.initialize(connection)
        epic_rows, task_rows = build_team_project_rows(
            connection, workspace_gid, include_empty_projects=include_empty_projects
        )
        existing = database.load_team_project_jira_mappings(connection)

        selected_team_gid = team_gid
        if pick_team:
            selection = search_team(connection)
            if selection is None:
                raise SystemExit("No team selected; nothing to create.")
            selected_team_gid = selection[0]

        if selected_team_gid:
            epic_rows, task_rows = _filter_rows_to_team(epic_rows, task_rows, selected_team_gid)
            if not epic_rows:
                raise SystemExit(f"No migratable team found with GID '{selected_team_gid}'.")
            logger.info(
                "Restricting creation to team '%s' (%s): 1 epic and %s task(s).",
                epic_rows[0].get("Summary", ""),
                selected_team_gid,
                len(task_rows),
            )

        if reset:
            if selected_team_gid:
                reset_keys = [
                    (row["_kind"], row["_asana_gid"]) for row in epic_rows + task_rows
                ]
                deleted = database.delete_team_project_jira_mappings(connection, reset_keys)
                logger.info(
                    "Reset %s existing mapping(s) for team '%s' (%s).",
                    deleted,
                    epic_rows[0].get("Summary", ""),
                    selected_team_gid,
                )
            else:
                deleted = database.delete_team_project_jira_mappings(connection)
                logger.info("Reset %s existing mapping(s) (all teams).", deleted)
            existing = database.load_team_project_jira_mappings(connection)

        if client is None:
            client = JiraClient(
                base_url=jira_base_url,
                username=username,
                token=token,
                skip_ssl_verification=credentials.skip_ssl_verification,
            )

        issue_types = client.get_createmeta_issue_types(jira_project_key)
        epic_type_id = resolve_issue_type_id(issue_types, epic_issue_type)
        task_type_id = resolve_issue_type_id(issue_types, task_issue_type)
        epic_fields = client.get_createmeta_fields(jira_project_key, epic_type_id)
        task_fields = client.get_createmeta_fields(jira_project_key, task_type_id)
        if map_fields:
            custom_field_map = prompt_field_map(
                custom_field_map,
                {epic_issue_type: epic_fields, task_issue_type: task_fields},
            )
            maybe_save_field_map(custom_field_map)
        _resolve_missing_field_warnings(custom_field_map, epic_fields, epic_issue_type)
        _resolve_missing_field_warnings(custom_field_map, task_fields, task_issue_type)

        pending_epics = [r for r in epic_rows if (r["_kind"], r["_asana_gid"]) not in existing]
        pending_tasks = [r for r in task_rows if (r["_kind"], r["_asana_gid"]) not in existing]
        total_pending = len(pending_epics) + len(pending_tasks)
        logger.info(
            "%s epic(s) and %s task(s) pending creation (%s already mapped).",
            len(pending_epics),
            len(pending_tasks),
            len(existing),
        )

        if not dry_run and not assume_yes:
            planned = total_pending if limit is None else min(limit, total_pending)
            answer = input(
                f"About to create up to {planned} issue(s) in Jira project {jira_project_key}. Continue? [y/N] "
            ).strip().lower()
            if answer not in {"y", "yes"}:
                raise SystemExit("Aborted by user.")

        results: List[Dict[str, str]] = []
        created_keys: Dict[Tuple[str, str], str] = dict(existing)
        created_count = 0
        dry_counter = 0

        def limit_reached() -> bool:
            return limit is not None and created_count >= limit

        # Record skips for already-mapped entries up front.
        for row in epic_rows + task_rows:
            key = (row["_kind"], row["_asana_gid"])
            if key in existing:
                results.append({
                    "kind": row["_kind"],
                    "asana_gid": row["_asana_gid"],
                    "summary": row.get("Summary", ""),
                    "jira_key": existing[key],
                    "status": "skipped-existing",
                    "message": "",
                })

        # Pass A — epics (sequential; tasks depend on their keys). Rate-limited
        # creations are requeued and retried in later rounds.
        epic_queue = list(pending_epics)
        epic_round = 0
        while epic_queue:
            epic_requeue: List[Dict[str, str]] = []
            for row in epic_queue:
                key = (row["_kind"], row["_asana_gid"])
                payload = build_issue_payload(
                    row,
                    project_key=jira_project_key,
                    issue_type_id=epic_type_id,
                    field_defs=epic_fields,
                    custom_field_map=custom_field_map,
                )
                if limit_reached():
                    results.append(_result(row, "", "skipped-limit", ""))
                    continue
                if dry_run:
                    dry_counter += 1
                    dry_key = f"{jira_project_key}-NEW{dry_counter}"
                    created_keys[key] = dry_key
                    logger.info("[dry-run] Epic payload:\n%s", json.dumps(payload, indent=2))
                    results.append(_result(row, dry_key, "dry-run", ""))
                    continue
                try:
                    response, assignee_dropped = _create_issue_with_assignee_fallback(client, payload)
                    jira_key = str(response.get("key", ""))
                    created_keys[key] = jira_key
                    database.upsert_team_project_jira_mappings(
                        connection,
                        [{"kind": row["_kind"], "asana_gid": row["_asana_gid"], "jira_key": jira_key}],
                        datetime.now(timezone.utc),
                    )
                    created_count += 1
                    results.append(_result(row, jira_key, "created", ASSIGNEE_DROPPED_MESSAGE if assignee_dropped else ""))
                    if assignee_dropped:
                        logger.warning("Created epic %s for %s without assignee (user not assignable).", jira_key, row.get("Summary", ""))
                    else:
                        logger.info("Created epic %s for %s", jira_key, row.get("Summary", ""))
                except (JiraApiError, ValueError) as exc:
                    if _is_rate_limited(exc) and epic_round < MAX_RATE_LIMIT_ROUNDS:
                        epic_requeue.append(row)
                        logger.warning("Rate limited creating epic for %s; requeued (round %s).", row.get("Summary", ""), epic_round + 1)
                        continue
                    results.append(_result(row, "", "error", str(exc)))
                    logger.error("Failed to create epic for %s: %s", row.get("Summary", ""), exc)
            if not epic_requeue:
                break
            epic_round += 1
            time.sleep(RATE_LIMIT_REQUEUE_DELAY * epic_round)
            epic_queue = epic_requeue

        # Pass B — tasks (parallel network calls; mappings persisted on the main thread).
        remaining = pending_tasks if limit is None else pending_tasks[: max(0, limit - created_count)]
        if len(remaining) < len(pending_tasks):
            for row in pending_tasks[len(remaining):]:
                results.append(_result(row, "", "skipped-limit", ""))

        def build_task(row: Dict[str, str]) -> Tuple[Dict[str, str], Dict[str, object], Optional[str]]:
            parent_key = created_keys.get((row["_parent_kind"], row["_parent_asana_gid"]))
            payload = build_issue_payload(
                row,
                project_key=jira_project_key,
                issue_type_id=task_type_id,
                field_defs=task_fields,
                custom_field_map=custom_field_map,
                parent_key=parent_key,
            )
            return row, payload, parent_key

        if dry_run:
            for row in remaining:
                _, payload, _ = build_task(row)
                dry_counter += 1
                dry_key = f"{jira_project_key}-NEW{dry_counter}"
                logger.info("[dry-run] Task payload:\n%s", json.dumps(payload, indent=2))
                results.append(_result(row, dry_key, "dry-run", ""))
        elif remaining:
            def worker(row: Dict[str, str]) -> Dict[str, object]:
                _, payload, parent_key = build_task(row)
                if not parent_key:
                    return {"row": row, "jira_key": None, "error": "parent epic was not created (skipped or failed)", "dropped_assignee": False, "rate_limited": False}
                try:
                    response, assignee_dropped = _create_issue_with_assignee_fallback(client, payload)
                    return {"row": row, "jira_key": str(response.get("key", "")), "error": None, "dropped_assignee": assignee_dropped, "rate_limited": False}
                except JiraApiError as exc:
                    return {"row": row, "jira_key": None, "error": str(exc), "dropped_assignee": False, "rate_limited": _is_rate_limited(exc)}
                except ValueError as exc:
                    return {"row": row, "jira_key": None, "error": str(exc), "dropped_assignee": False, "rate_limited": False}

            # Rate-limited tasks are requeued and retried in later rounds.
            task_queue = list(remaining)
            task_round = 0
            while task_queue:
                task_requeue: List[Dict[str, str]] = []
                with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
                    future_map = {executor.submit(worker, row): row for row in task_queue}
                    for future in as_completed(future_map):
                        res = future.result()
                        row = res["row"]
                        if res["rate_limited"] and task_round < MAX_RATE_LIMIT_ROUNDS:
                            task_requeue.append(row)
                            logger.warning("Rate limited creating task for %s; requeued (round %s).", row.get("Summary", ""), task_round + 1)
                            continue
                        if res["error"]:
                            results.append(_result(row, "", "error", res["error"]))
                            logger.error("Failed to create task for %s: %s", row.get("Summary", ""), res["error"])
                            continue
                        jira_key = res["jira_key"]
                        database.upsert_team_project_jira_mappings(
                            connection,
                            [{"kind": row["_kind"], "asana_gid": row["_asana_gid"], "jira_key": jira_key}],
                            datetime.now(timezone.utc),
                        )
                        created_count += 1
                        results.append(_result(row, jira_key, "created", ASSIGNEE_DROPPED_MESSAGE if res["dropped_assignee"] else ""))
                        if res["dropped_assignee"]:
                            logger.warning("Created task %s for %s without assignee (user not assignable).", jira_key, row.get("Summary", ""))
                        else:
                            logger.info("Created task %s for %s", jira_key, row.get("Summary", ""))
                if not task_requeue:
                    break
                task_round += 1
                time.sleep(RATE_LIMIT_REQUEUE_DELAY * task_round)
                task_queue = task_requeue

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    report_path = Path(output_dir) / f"team_project_jira_create_{timestamp}.csv"
    write_report(results, report_path)
    logger.info(
        "Run complete: %s created, %s skipped, %s error(s). Report: %s",
        sum(1 for r in results if r["status"] in {"created", "dry-run"}),
        sum(1 for r in results if r["status"].startswith("skipped")),
        sum(1 for r in results if r["status"] == "error"),
        report_path,
    )
    return report_path


def _result(row: Dict[str, str], jira_key: str, status: str, message: str) -> Dict[str, str]:
    return {
        "kind": row["_kind"],
        "asana_gid": row["_asana_gid"],
        "summary": row.get("Summary", ""),
        "jira_key": jira_key,
        "status": status,
        "message": message,
    }


def main(argv: Optional[Iterable[str]] = None) -> None:
    args = parse_args(argv)
    try:
        log_level = resolve_log_level(args.log_level)
    except ValueError as exc:  # noqa: BLE001
        raise SystemExit(str(exc)) from exc
    configure_logging(log_level)

    try:
        jira_base_url = resolve_jira_base_url(load_credentials(), prod=args.prod, site_key=args.jira_site)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    try:
        report_path = create_team_project_jira_issues(
            database_path=args.database,
            jira_base_url=jira_base_url,
            jira_project_key=args.jira_project_key,
            output_dir=Path(args.output_dir),
            workspace_gid=args.workspace_gid,
            epic_issue_type=args.epic_issue_type,
            task_issue_type=args.task_issue_type,
            field_map_path=args.field_map,
            map_fields=args.map_fields,
            workers=max(args.workers, 1),
            limit=args.limit,
            reset=args.reset,
            dry_run=args.dry_run,
            assume_yes=args.yes,
            team_gid=args.team_gid,
            pick_team=args.pick_team,
            include_empty_projects=args.include_empty_projects,
        )
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(str(exc)) from exc

    logger.info("Jira team/project issue creation finished: %s", report_path)


if __name__ == "__main__":
    main()
