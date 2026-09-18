"""Jira CSV importer configuration discovery, persistence, and generation."""

from __future__ import annotations

import csv
from difflib import SequenceMatcher
import json
import re
import shutil
import uuid
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence

from src.lib.config import (
    profile_slug,
    read_profile_name,
    read_profile_values,
    resolve_credentials_path,
    slugify,
    write_profile_values,
)


@dataclass(frozen=True)
class JiraFieldChoice:
    field_id: str
    name: str
    field_type: str
    schema_type: str


@dataclass(frozen=True)
class ArchiveImportSettings:
    section: Optional[JiraFieldChoice] = None
    project_link: Optional[JiraFieldChoice] = None
    task_link: Optional[JiraFieldChoice] = None
    external_id: Optional[JiraFieldChoice] = None
    start_date: Optional[JiraFieldChoice] = None
    above_epic_issue_type_name: Optional[str] = None
    above_epic_issue_type_id: Optional[str] = None


FIELD_SPECS = (
    ("section", "Asana Section", "SECTION"),
    ("project_link", "Asana Project Link", "PROJECT_LINK"),
    ("task_link", "Asana Task Link", "TASK_LINK"),
    ("external_id", "External Issue ID", "EXTERNAL_ID"),
    ("start_date", "Start Date", "START_DATE"),
)


def _site_prefix(site_key: str) -> str:
    normalized = re.sub(r"[^A-Z0-9_]", "_", site_key.upper())
    return f"JIRA_SITE_{normalized}_CSV_IMPORT_"


def _legacy_site_prefix(site_key: str) -> str:
    normalized = re.sub(r"[^A-Z0-9_]", "_", site_key.upper())
    return f"JIRA_SITE_{normalized}_CSV_ARCHIVE_"


def _field_type(field: Dict[str, object]) -> tuple[str, str]:
    schema = field.get("schema") if isinstance(field.get("schema"), dict) else {}
    schema_type = str(schema.get("type") or "unknown")
    custom = str(schema.get("custom") or "")
    human = {
        "string": "Text",
        "date": "Date",
        "datetime": "Date/time",
        "number": "Number",
        "option": "Select list",
        "array": "Multi-value",
        "user": "User",
    }.get(schema_type, schema_type.replace("_", " ").title())
    if custom:
        subtype = custom.rsplit(":", 1)[-1].replace("-", " ").title()
        if subtype and subtype.casefold() not in human.casefold():
            human = f"{human} ({subtype})"
    return human, schema_type


def normalize_fields(fields: Iterable[Dict[str, object]]) -> List[JiraFieldChoice]:
    choices = []
    for field in fields:
        field_id = str(field.get("id") or field.get("fieldId") or "").strip()
        name = str(field.get("name") or "").strip()
        if not field_id or not name:
            continue
        human, schema_type = _field_type(field)
        choices.append(JiraFieldChoice(field_id, name, human, schema_type))
    return sorted(choices, key=lambda item: (item.name.casefold(), item.field_id))


def load_archive_import_settings(env_path: Path, site_key: str) -> ArchiveImportSettings:
    prefix = _site_prefix(site_key)
    legacy_prefix = _legacy_site_prefix(site_key)
    keys = []
    for _attribute, _label, suffix in FIELD_SPECS:
        keys.extend(f"{prefix}{suffix}_{part}" for part in ("FIELD_ID", "FIELD_NAME", "FIELD_TYPE", "SCHEMA_TYPE"))
        keys.extend(
            f"{legacy_prefix}{suffix}_{part}"
            for part in ("FIELD_ID", "FIELD_NAME", "FIELD_TYPE", "SCHEMA_TYPE")
        )
    keys.extend([f"{prefix}ABOVE_EPIC_ISSUE_TYPE_NAME", f"{prefix}ABOVE_EPIC_ISSUE_TYPE_ID"])
    values = read_profile_values(env_path, keys)

    def field(suffix: str) -> Optional[JiraFieldChoice]:
        selected_prefix = prefix
        field_id = values.get(f"{selected_prefix}{suffix}_FIELD_ID")
        if not field_id:
            selected_prefix = legacy_prefix
            field_id = values.get(f"{selected_prefix}{suffix}_FIELD_ID")
        if not field_id:
            return None
        return JiraFieldChoice(
            field_id,
            values.get(f"{selected_prefix}{suffix}_FIELD_NAME", field_id),
            values.get(f"{selected_prefix}{suffix}_FIELD_TYPE", "Unknown"),
            values.get(f"{selected_prefix}{suffix}_SCHEMA_TYPE", "unknown"),
        )

    return ArchiveImportSettings(
        section=field("SECTION"),
        project_link=field("PROJECT_LINK"),
        task_link=field("TASK_LINK"),
        external_id=field("EXTERNAL_ID"),
        start_date=field("START_DATE"),
        above_epic_issue_type_name=values.get(f"{prefix}ABOVE_EPIC_ISSUE_TYPE_NAME") or None,
        above_epic_issue_type_id=values.get(f"{prefix}ABOVE_EPIC_ISSUE_TYPE_ID") or None,
    )


def save_archive_import_settings(
    env_path: Path,
    site_key: str,
    settings: ArchiveImportSettings,
) -> None:
    prefix = _site_prefix(site_key)
    values: Dict[str, Optional[str]] = {}
    values[f"{prefix}ABOVE_EPIC_ISSUE_TYPE_NAME"] = settings.above_epic_issue_type_name or None
    values[f"{prefix}ABOVE_EPIC_ISSUE_TYPE_ID"] = settings.above_epic_issue_type_id or None
    for attribute, _label, suffix in FIELD_SPECS:
        choice = getattr(settings, attribute)
        values.update(
            {
                f"{prefix}{suffix}_FIELD_ID": choice.field_id if choice else None,
                f"{prefix}{suffix}_FIELD_NAME": choice.name if choice else None,
                f"{prefix}{suffix}_FIELD_TYPE": choice.field_type if choice else None,
                f"{prefix}{suffix}_SCHEMA_TYPE": choice.schema_type if choice else None,
            }
        )
    write_profile_values(env_path, values)


def clear_archive_import_settings(env_path: Path, site_key: str) -> None:
    """Remove all saved CSV import settings for one site from the profile env file."""
    prefix = _site_prefix(site_key)
    legacy_prefix = _legacy_site_prefix(site_key)
    keys_to_clear: Dict[str, Optional[str]] = {}
    keys_to_clear[f"{prefix}ABOVE_EPIC_ISSUE_TYPE_NAME"] = None
    keys_to_clear[f"{prefix}ABOVE_EPIC_ISSUE_TYPE_ID"] = None
    for _attribute, _label, suffix in FIELD_SPECS:
        for part in ("FIELD_ID", "FIELD_NAME", "FIELD_TYPE", "SCHEMA_TYPE"):
            keys_to_clear[f"{prefix}{suffix}_{part}"] = None
            keys_to_clear[f"{legacy_prefix}{suffix}_{part}"] = None
    write_profile_values(env_path, keys_to_clear)


def stale_profile_import_config_paths(
    env_path: Path, site_key: Optional[str] = None
) -> List[Path]:
    """Return all dated config files for one site except the newest one."""
    basename = _profile_config_basename(env_path)
    site_segment = f"_{site_key}" if site_key else ""
    matches = sorted(
        env_path.parent.glob(f"{basename}{site_segment}_????-??-??_jira_config.txt")
    )
    return matches[:-1] if len(matches) > 1 else []


def _field_by_id(fields: Sequence[JiraFieldChoice], raw_id: str) -> Optional[JiraFieldChoice]:
    normalized = raw_id.strip()
    if normalized.isdigit():
        normalized = f"customfield_{normalized}"
    return next((field for field in fields if field.field_id.casefold() == normalized.casefold()), None)


def _fuzzy_field_matches(
    fields: Sequence[JiraFieldChoice],
    query: str,
) -> List[JiraFieldChoice]:
    normalized_query = query.strip().casefold()
    if not normalized_query:
        return []

    substring_matches = [
        field for field in fields if normalized_query in field.name.casefold()
    ]
    if substring_matches:
        return sorted(
            substring_matches,
            key=lambda field: (
                len(field.name) - len(normalized_query),
                field.name.casefold(),
                field.field_id,
            ),
        )

    scored = [
        (SequenceMatcher(None, normalized_query, field.name.casefold()).ratio(), field)
        for field in fields
    ]
    return [
        field
        for match_score, field in sorted(
            scored,
            key=lambda item: (-item[0], item[1].name.casefold(), item[1].field_id),
        )
        if match_score >= 0.6
    ][:10]


def prompt_field_choice(
    label: str,
    fields: Sequence[JiraFieldChoice],
    current: Optional[JiraFieldChoice],
    *,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> tuple[Optional[JiraFieldChoice], bool]:
    custom_fields = [
        field for field in fields if field.field_id.casefold().startswith("customfield_")
    ]
    exact = [field for field in custom_fields if field.name.casefold() == label.casefold()]
    matches: List[JiraFieldChoice] = []
    searching = False

    def show_choices() -> None:
        output_fn(f"\nSelect the Jira field for {label!r}:")
        if searching:
            if not matches:
                output_fn("No matching custom fields found.")
            for index, field in enumerate(matches, start=1):
                output_fn(
                    f"[{index}] {field.name} — {field.field_id} — {field.field_type}"
                )
            output_fn("[F] Search again")
        elif exact:
            field = exact[0]
            output_fn(f"[1] Use {field.name} — {field.field_id} — {field.field_type}")
            output_fn("[2] Search custom fields by name")
        else:
            output_fn("No exact field-name match found.")
            output_fn("[1] Search custom fields by name")
        output_fn("[M] Enter a field ID manually")
        output_fn("[S] Skip this mapping")
        if current:
            output_fn("[X] Clear the saved mapping")

    show_choices()
    while True:
        choice = input_fn("Choice: ").strip()
        if not searching and exact and choice == "1":
            selected = exact[0]
            output_fn(
                f"Found: {selected.name} — {selected.field_id} — "
                f"{selected.field_type} (schema: {selected.schema_type})"
            )
            return selected, True
        if searching and choice.isdigit() and 1 <= int(choice) <= len(matches):
            selected = matches[int(choice) - 1]
            output_fn(
                f"Found: {selected.name} — {selected.field_id} — "
                f"{selected.field_type} (schema: {selected.schema_type})"
            )
            return selected, True
        wants_search = (
            (not searching and exact and choice == "2")
            or (not searching and not exact and choice == "1")
            or (searching and choice.casefold() == "f")
        )
        if wants_search:
            query = input_fn("Custom field name search: ").strip()
            if not query:
                output_fn("Enter at least one character to search.")
                continue
            matches = _fuzzy_field_matches(custom_fields, query)
            searching = True
            show_choices()
            continue
        if choice.casefold() == "m":
            entered = input_fn("Jira custom field ID: ").strip()
            selected = _field_by_id(fields, entered)
            if not selected:
                output_fn(f"Jira did not return a field with ID {entered!r}.")
                continue
            output_fn(
                f"Found: {selected.name} — {selected.field_id} — "
                f"{selected.field_type} (schema: {selected.schema_type})"
            )
            return selected, True
        if choice.casefold() == "s":
            return None, False
        if choice.casefold() == "x" and current:
            return None, True
        output_fn("Choose a listed option, M, S, or X.")


def _choice_text(choice: Optional[object]) -> str:
    if isinstance(choice, JiraFieldChoice):
        return f"{choice.name} ({choice.field_id}, {choice.field_type})"
    return "Skipped"


def review_archive_import_settings(
    saved: ArchiveImportSettings,
    fields: Sequence[JiraFieldChoice],
    site_name: str,
    preview_fn: Callable[[ArchiveImportSettings], str],
    *,
    force_configure: bool = False,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> Optional[tuple[ArchiveImportSettings, ArchiveImportSettings]]:
    current = saved
    changed_saved = saved
    if force_configure:
        attributes = [spec[0] for spec in FIELD_SPECS]
    else:
        attributes = [
            attribute
            for attribute, _label, _suffix in FIELD_SPECS
            if getattr(saved, attribute) is None
        ]

    def edit(attribute: str) -> None:
        nonlocal current, changed_saved
        label = next(label for name, label, _suffix in FIELD_SPECS if name == attribute)
        selected, persist = prompt_field_choice(
            label, fields, getattr(changed_saved, attribute), input_fn=input_fn, output_fn=output_fn
        )
        current = replace(current, **{attribute: selected})
        if persist:
            changed_saved = replace(changed_saved, **{attribute: selected})

    for attribute in attributes:
        edit(attribute)

    while True:
        output_fn(f"\nArchive Jira CSV configuration — {site_name}")
        for index, (attribute, label, _suffix) in enumerate(FIELD_SPECS, start=1):
            output_fn(f"[{index}] {label}: {_choice_text(getattr(current, attribute))}")
        output_fn("[V] View generated configuration")
        output_fn("[G] Save settings and generate")
        output_fn("[C] Cancel configuration generation")
        choice = input_fn("Choice: ").strip().casefold()
        if choice.isdigit() and 1 <= int(choice) <= len(FIELD_SPECS):
            edit(FIELD_SPECS[int(choice) - 1][0])
        elif choice == "v":
            output_fn("\n" + preview_fn(current))
        elif choice == "g":
            return current, changed_saved
        elif choice == "c":
            return None
        else:
            output_fn(f"Choose 1-{len(FIELD_SPECS)}, V, G, or C.")


SYSTEM_FIELD_MAPPINGS = {
    "task_name": {"jira.field": "summary", "userChanged": "false", "manualMapping": "false"},
    "Comment": {"jira.field": "comment", "userChanged": "false", "manualMapping": "false"},
    "notes": {"jira.field": "description", "userChanged": "false", "manualMapping": "false"},
    "jira_hierarchy_depth": {"jira.field": "issuetype", "userChanged": "false", "manualMapping": "false"},
    "modified_section": {"jira.field": "status", "userChanged": "false", "manualMapping": "true"},
    "jira_project_name": {"jira.field": "project.name", "userChanged": "false", "manualMapping": "false"},
    "due_date": {"jira.field": "duedate", "userChanged": "false", "manualMapping": "false"},
    "created_at": {"jira.field": "created", "userChanged": "false", "manualMapping": "false"},
    "jira_project_key": {"jira.field": "project.key", "userChanged": "false", "manualMapping": "false"},
    "completed_at": {"jira.field": "resolutiondate", "userChanged": "false", "manualMapping": "false"},
    "jira_project_type": {"jira.field": "project.type", "userChanged": "false", "manualMapping": "false"},
    "jira_parent_gid": {"jira.field": "parent", "userChanged": "false", "manualMapping": "false"},
    "task_gid": {"jira.field": "issue-id", "userChanged": "false", "manualMapping": "false"},
    "creator_atlassian_id": {"jira.field": "reporter", "userChanged": "false", "manualMapping": "false"},
    "assignee_atlassian_id": {"jira.field": "assignee", "userChanged": "false", "manualMapping": "false"},
    "collaborator_atlassian_id": {"jira.field": "watches", "userChanged": "false", "manualMapping": "false"},
    "Tags": {"jira.field": "labels", "userChanged": "false", "manualMapping": "false"},
    "Resolution": {"jira.field": "resolution", "userChanged": "false", "manualMapping": "false"},
}

CANONICAL_TASK_IMPORT_COLUMNS = tuple(SYSTEM_FIELD_MAPPINGS) + (
    "Section",
    "Asana Project Link",
    "Asana Task Link",
    "External issue ID",
    "start_on",
    "completed",
)


def _profile_config_basename(env_path: Path) -> str:
    profile_name = read_profile_name(env_path)
    if profile_name:
        return slugify(profile_name)
    return profile_slug(env_path) or "default"


def profile_import_config_path(
    env_path: Path,
    generated_on: Optional[date] = None,
    site_key: Optional[str] = None,
) -> Path:
    """Return the reusable importer-config path owned by one client profile."""
    generated_date = generated_on or date.today()
    basename = _profile_config_basename(env_path)
    site_segment = f"_{site_key}" if site_key else ""
    return env_path.with_name(
        f"{basename}{site_segment}_{generated_date.isoformat()}_jira_config.txt"
    )


def latest_profile_import_config_path(
    env_path: Path, site_key: Optional[str] = None
) -> Optional[Path]:
    """Return the newest dated importer config for one profile, if present."""
    basename = _profile_config_basename(env_path)
    site_segment = f"_{site_key}" if site_key else ""
    matches = sorted(
        env_path.parent.glob(f"{basename}{site_segment}_????-??-??_jira_config.txt")
    )
    if matches:
        return matches[-1]
    legacy = env_path.with_name(f"{env_path.stem}_jira_config.txt")
    return legacy if legacy.is_file() else None


def copy_active_profile_import_config(
    output_dir: Path, site_key: Optional[str] = None
) -> Optional[Path]:
    """Copy the active profile's saved config into an export bundle when available."""
    source = latest_profile_import_config_path(resolve_credentials_path(), site_key=site_key)
    if source is None:
        return None
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / source.name
    shutil.copy2(source, destination)
    return destination


def build_archive_import_config_for_columns(
    file_name: str,
    columns: Sequence[str],
    settings: ArchiveImportSettings,
) -> Dict[str, object]:
    header = set(columns)
    mappings = {
        column: dict(mapping)
        for column, mapping in SYSTEM_FIELD_MAPPINGS.items()
        if column in header
    }
    custom = (
        ("Section", settings.section),
        ("Asana Project Link", settings.project_link),
        ("Asana Task Link", settings.task_link),
        ("External issue ID", settings.external_id),
        ("start_on", settings.start_date),
    )
    for column, choice in custom:
        if column in header and choice:
            numeric_id = choice.field_id.removeprefix("customfield_")
            mappings[column] = {
                "userChanged": "false",
                "manualMapping": "false",
                "existing.custom.field": numeric_id,
            }
    value_mappings: Dict[str, object] = {}
    return {
        "config.version": "2.0",
        "config.project.from.csv": "true",
        "config.encoding": "UTF-8",
        "config.file.id": str(uuid.uuid4()),
        "config.email.suffix": "@",
        "config.file.name": file_name,
        "config.field.mappings": mappings,
        "config.csv.file.id": None,
        "config.value.mappings": value_mappings,
        "config.delimiter": ",",
        "config.project": {
            "project.type": "",
            "project.key": "",
            "project.description": None,
            "project.url": None,
            "project.name": "",
            "project.lead": None,
        },
        "config.date.format": "dd/MMM/yy h:mm a",
    }


def build_archive_import_config(
    csv_path: Path,
    settings: ArchiveImportSettings,
) -> Dict[str, object]:
    with csv_path.open("r", newline="", encoding="utf-8") as handle:
        header = next(csv.reader(handle), [])
    return build_archive_import_config_for_columns(csv_path.name, header, settings)


def render_reusable_import_config(
    settings: ArchiveImportSettings,
    file_name: str = "jira_tasks.csv",
) -> str:
    config = build_archive_import_config_for_columns(
        file_name,
        CANONICAL_TASK_IMPORT_COLUMNS,
        settings,
    )
    return json.dumps(config, indent=2) + "\n"


def write_reusable_import_config(
    output_path: Path,
    settings: ArchiveImportSettings,
    file_name: str = "jira_tasks.csv",
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        render_reusable_import_config(settings, file_name),
        encoding="utf-8",
    )
    return output_path


def render_archive_import_config(csv_path: Path, settings: ArchiveImportSettings) -> str:
    return json.dumps(build_archive_import_config(csv_path, settings), indent=2) + "\n"


def write_archive_import_config(csv_path: Path, settings: ArchiveImportSettings) -> Path:
    output = csv_path.with_name(f"{csv_path.stem}_jira_config.txt")
    output.write_text(render_archive_import_config(csv_path, settings), encoding="utf-8")
    return output
