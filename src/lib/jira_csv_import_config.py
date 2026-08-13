"""Jira CSV importer configuration discovery, persistence, and generation."""

from __future__ import annotations

import csv
import json
import re
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence

from src.lib.config import read_profile_values, write_profile_values


@dataclass(frozen=True)
class JiraFieldChoice:
    field_id: str
    name: str
    field_type: str
    schema_type: str


@dataclass(frozen=True)
class JiraResolutionChoice:
    resolution_id: str
    name: str


@dataclass(frozen=True)
class ArchiveImportSettings:
    section: Optional[JiraFieldChoice] = None
    external_id: Optional[JiraFieldChoice] = None
    start_date: Optional[JiraFieldChoice] = None
    completed_resolution: Optional[JiraResolutionChoice] = None


FIELD_SPECS = (
    ("section", "Section", "SECTION"),
    ("external_id", "External Issue ID", "EXTERNAL_ID"),
    ("start_date", "Start Date", "START_DATE"),
)


def _site_prefix(site_key: str) -> str:
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


def normalize_resolutions(resolutions: Iterable[Dict[str, object]]) -> List[JiraResolutionChoice]:
    choices = [
        JiraResolutionChoice(str(item.get("id") or "").strip(), str(item.get("name") or "").strip())
        for item in resolutions
        if item.get("id") and item.get("name")
    ]
    return sorted(choices, key=lambda item: item.name.casefold())


def load_archive_import_settings(env_path: Path, site_key: str) -> ArchiveImportSettings:
    prefix = _site_prefix(site_key)
    keys = []
    for _attribute, _label, suffix in FIELD_SPECS:
        keys.extend(f"{prefix}{suffix}_{part}" for part in ("FIELD_ID", "FIELD_NAME", "FIELD_TYPE", "SCHEMA_TYPE"))
    keys.extend((f"{prefix}RESOLUTION_ID", f"{prefix}RESOLUTION_NAME"))
    values = read_profile_values(env_path, keys)

    def field(suffix: str) -> Optional[JiraFieldChoice]:
        field_id = values.get(f"{prefix}{suffix}_FIELD_ID")
        if not field_id:
            return None
        return JiraFieldChoice(
            field_id,
            values.get(f"{prefix}{suffix}_FIELD_NAME", field_id),
            values.get(f"{prefix}{suffix}_FIELD_TYPE", "Unknown"),
            values.get(f"{prefix}{suffix}_SCHEMA_TYPE", "unknown"),
        )

    resolution_id = values.get(f"{prefix}RESOLUTION_ID")
    resolution = None
    if resolution_id:
        resolution = JiraResolutionChoice(
            resolution_id, values.get(f"{prefix}RESOLUTION_NAME", resolution_id)
        )
    return ArchiveImportSettings(
        section=field("SECTION"),
        external_id=field("EXTERNAL_ID"),
        start_date=field("START_DATE"),
        completed_resolution=resolution,
    )


def save_archive_import_settings(
    env_path: Path,
    site_key: str,
    settings: ArchiveImportSettings,
) -> None:
    prefix = _site_prefix(site_key)
    values: Dict[str, Optional[str]] = {}
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
    resolution = settings.completed_resolution
    values[f"{prefix}RESOLUTION_ID"] = resolution.resolution_id if resolution else None
    values[f"{prefix}RESOLUTION_NAME"] = resolution.name if resolution else None
    write_profile_values(env_path, values)


def _field_by_id(fields: Sequence[JiraFieldChoice], raw_id: str) -> Optional[JiraFieldChoice]:
    normalized = raw_id.strip()
    if normalized.isdigit():
        normalized = f"customfield_{normalized}"
    return next((field for field in fields if field.field_id.casefold() == normalized.casefold()), None)


def prompt_field_choice(
    label: str,
    fields: Sequence[JiraFieldChoice],
    current: Optional[JiraFieldChoice],
    *,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> tuple[Optional[JiraFieldChoice], bool]:
    exact = [field for field in fields if field.name.casefold() == label.casefold()]
    matches = exact or [field for field in fields if label.casefold() in field.name.casefold()]
    output_fn(f"\nSelect the Jira field for {label!r}:")
    for index, field in enumerate(matches, start=1):
        output_fn(f"[{index}] {field.name} — {field.field_id} — {field.field_type}")
    output_fn("[M] Enter a field ID manually")
    output_fn("[S] Skip this mapping for this export")
    if current:
        output_fn("[X] Clear the saved mapping")
    while True:
        choice = input_fn("Choice: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(matches):
            selected = matches[int(choice) - 1]
            output_fn(
                f"Found: {selected.name} — {selected.field_id} — "
                f"{selected.field_type} (schema: {selected.schema_type})"
            )
            return selected, True
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
        output_fn("Choose a listed number, M, S, or X.")


def prompt_resolution_choice(
    resolutions: Sequence[JiraResolutionChoice],
    current: Optional[JiraResolutionChoice],
    *,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> tuple[Optional[JiraResolutionChoice], bool]:
    output_fn("\nSelect the Jira resolution for completed Asana tasks:")
    for index, resolution in enumerate(resolutions, start=1):
        output_fn(f"[{index}] {resolution.name} — {resolution.resolution_id}")
    output_fn("[S] Skip completed → resolution for this export")
    if current:
        output_fn("[X] Clear the saved resolution")
    while True:
        choice = input_fn("Choice: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(resolutions):
            return resolutions[int(choice) - 1], True
        if choice.casefold() == "s":
            return None, False
        if choice.casefold() == "x" and current:
            return None, True
        output_fn("Choose a listed number, S, or X.")


def _choice_text(choice: Optional[object]) -> str:
    if isinstance(choice, JiraFieldChoice):
        return f"{choice.name} ({choice.field_id}, {choice.field_type})"
    if isinstance(choice, JiraResolutionChoice):
        return f"{choice.name} ({choice.resolution_id})"
    return "Skipped for this export"


def review_archive_import_settings(
    saved: ArchiveImportSettings,
    fields: Sequence[JiraFieldChoice],
    resolutions: Sequence[JiraResolutionChoice],
    site_name: str,
    preview_fn: Callable[[ArchiveImportSettings], str],
    *,
    force_configure: bool = False,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> Optional[tuple[ArchiveImportSettings, ArchiveImportSettings]]:
    current = saved
    changed_saved = saved
    if force_configure or not any((saved.section, saved.external_id, saved.start_date, saved.completed_resolution)):
        attributes = [spec[0] for spec in FIELD_SPECS] + ["completed_resolution"]
    else:
        attributes = []

    def edit(attribute: str) -> None:
        nonlocal current, changed_saved
        if attribute == "completed_resolution":
            selected, persist = prompt_resolution_choice(
                resolutions, changed_saved.completed_resolution, input_fn=input_fn, output_fn=output_fn
            )
        else:
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
        output_fn(f"[1] Section: {_choice_text(current.section)}")
        output_fn(f"[2] External Issue ID: {_choice_text(current.external_id)}")
        output_fn(f"[3] Start Date: {_choice_text(current.start_date)}")
        output_fn(f"[4] Completed resolution: {_choice_text(current.completed_resolution)}")
        output_fn("[V] View generated configuration")
        output_fn("[G] Save settings and generate")
        output_fn("[C] Cancel configuration generation")
        choice = input_fn("Choice: ").strip().casefold()
        if choice in {"1", "2", "3", "4"}:
            edit({"1": "section", "2": "external_id", "3": "start_date", "4": "completed_resolution"}[choice])
        elif choice == "v":
            output_fn("\n" + preview_fn(current))
        elif choice == "g":
            return current, changed_saved
        elif choice == "c":
            return None
        else:
            output_fn("Choose 1-4, V, G, or C.")


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
}


def build_archive_import_config(
    csv_path: Path,
    settings: ArchiveImportSettings,
) -> Dict[str, object]:
    with csv_path.open("r", newline="", encoding="utf-8") as handle:
        header = next(csv.reader(handle), [])
    mappings = {
        column: dict(mapping)
        for column, mapping in SYSTEM_FIELD_MAPPINGS.items()
        if column in header
    }
    custom = (
        ("Section", settings.section),
        ("External issue ID", settings.external_id),
        ("start_at", settings.start_date),
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
    if "completed" in header and settings.completed_resolution:
        mappings["completed"] = {
            "jira.field": "resolution",
            "userChanged": "true",
            "manualMapping": "true",
        }
        value_mappings["completed"] = {
            "No": "<<blank>>",
            "Yes": settings.completed_resolution.resolution_id,
        }
    return {
        "config.version": "2.0",
        "config.project.from.csv": "true",
        "config.encoding": "UTF-8",
        "config.file.id": str(uuid.uuid4()),
        "config.email.suffix": "@",
        "config.file.name": csv_path.name,
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


def render_archive_import_config(csv_path: Path, settings: ArchiveImportSettings) -> str:
    return json.dumps(build_archive_import_config(csv_path, settings), indent=2) + "\n"


def write_archive_import_config(csv_path: Path, settings: ArchiveImportSettings) -> Path:
    output = csv_path.with_name(f"{csv_path.stem}_jira_config.txt")
    output.write_text(render_archive_import_config(csv_path, settings), encoding="utf-8")
    return output
