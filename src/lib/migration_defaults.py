"""Client-specific Team/Project Jira settings, stored in the active profile env file.

Unset keys fall back to neutral defaults, so a profile without overrides produces
generic wording and no deadline. The interactive CLI prompts for these values on
first use and records confirmation via ``MIGRATION_SETTINGS_CONFIRMED``.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Dict, Optional

from src.lib.config import read_profile_values, write_profile_values

CONFIRMED_KEY = "MIGRATION_SETTINGS_CONFIRMED"
EPIC_DEADLINE_KEY = "MIGRATION_EPIC_DEADLINE"
ATLASSIAN_TEAM_NAME_KEY = "MIGRATION_ATLASSIAN_TEAM_NAME"
ARCHIVE_READ_ONLY_LABEL_KEY = "MIGRATION_ARCHIVE_READ_ONLY_LABEL"
ARCHIVE_PRESERVATION_LABEL_KEY = "MIGRATION_ARCHIVE_PRESERVATION_LABEL"
LAST_WORKING_DAY_FIELD_ID_KEY = "MIGRATION_LAST_WORKING_DAY_FIELD_ID"
DECISION_FIELD_NAME_KEY = "MIGRATION_DECISION_FIELD_NAME"
JIRA_PROJECT_KEY_KEY = "MIGRATION_JIRA_PROJECT_KEY"

_CONFIRMED_VALUE = "true"


@dataclass(frozen=True)
class MigrationSettings:
    epic_deadline: Optional[str] = None
    atlassian_team_name: str = "Atlassian"
    archive_read_only_label: str = "Archive-Read Only"
    archive_preservation_label: str = "Archive-Preservation Only"
    last_working_day_field_id: str = "customfield_11475"
    decision_field_name: str = "Asana Migration Decision"
    jira_project_key: Optional[str] = None


DEFAULT_SETTINGS = MigrationSettings()


def load_migration_settings(env_path: str | Path) -> MigrationSettings:
    values = read_profile_values(
        env_path,
        [
            EPIC_DEADLINE_KEY,
            ATLASSIAN_TEAM_NAME_KEY,
            ARCHIVE_READ_ONLY_LABEL_KEY,
            ARCHIVE_PRESERVATION_LABEL_KEY,
            LAST_WORKING_DAY_FIELD_ID_KEY,
            DECISION_FIELD_NAME_KEY,
            JIRA_PROJECT_KEY_KEY,
        ],
    )
    return replace(
        DEFAULT_SETTINGS,
        epic_deadline=values.get(EPIC_DEADLINE_KEY) or None,
        atlassian_team_name=values.get(ATLASSIAN_TEAM_NAME_KEY) or DEFAULT_SETTINGS.atlassian_team_name,
        archive_read_only_label=values.get(ARCHIVE_READ_ONLY_LABEL_KEY) or DEFAULT_SETTINGS.archive_read_only_label,
        archive_preservation_label=values.get(ARCHIVE_PRESERVATION_LABEL_KEY)
        or DEFAULT_SETTINGS.archive_preservation_label,
        last_working_day_field_id=values.get(LAST_WORKING_DAY_FIELD_ID_KEY)
        or DEFAULT_SETTINGS.last_working_day_field_id,
        decision_field_name=values.get(DECISION_FIELD_NAME_KEY) or DEFAULT_SETTINGS.decision_field_name,
        jira_project_key=values.get(JIRA_PROJECT_KEY_KEY) or None,
    )


def is_migration_settings_confirmed(env_path: str | Path) -> bool:
    return read_profile_values(env_path, [CONFIRMED_KEY]).get(CONFIRMED_KEY) == _CONFIRMED_VALUE


def save_migration_settings(env_path: str | Path, settings: MigrationSettings) -> None:
    values: Dict[str, Optional[str]] = {
        CONFIRMED_KEY: _CONFIRMED_VALUE,
        EPIC_DEADLINE_KEY: settings.epic_deadline,
        ATLASSIAN_TEAM_NAME_KEY: settings.atlassian_team_name,
        ARCHIVE_READ_ONLY_LABEL_KEY: settings.archive_read_only_label,
        ARCHIVE_PRESERVATION_LABEL_KEY: settings.archive_preservation_label,
        LAST_WORKING_DAY_FIELD_ID_KEY: settings.last_working_day_field_id,
        DECISION_FIELD_NAME_KEY: settings.decision_field_name,
        JIRA_PROJECT_KEY_KEY: settings.jira_project_key,
    }
    write_profile_values(env_path, values)
