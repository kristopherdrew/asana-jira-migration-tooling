"""SQLite helpers used across the Asana export scripts."""

from __future__ import annotations

import logging
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


DEFAULT_DB_PATH = "data/asana.sqlite"


CUSTOM_FIELDS_SCHEMA = """
CREATE TABLE IF NOT EXISTS custom_fields (
    gid TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    resource_type TEXT,
    resource_subtype TEXT,
    description TEXT,
    enabled INTEGER,
    is_global_to_workspace INTEGER,
    workspace_gid TEXT,
    workspace_name TEXT,
    format TEXT,
    precision INTEGER,
    currency_code TEXT,
    custom_label TEXT,
    custom_label_position TEXT,
    is_formula_field INTEGER,
    modified_at TEXT,
    synced_at TEXT NOT NULL
);
"""

CUSTOM_FIELD_ENUM_OPTIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS custom_field_enum_options (
    custom_field_gid TEXT NOT NULL,
    enum_option_gid TEXT NOT NULL,
    name TEXT,
    color TEXT,
    enabled INTEGER,
    position INTEGER,
    synced_at TEXT NOT NULL,
    PRIMARY KEY (custom_field_gid, enum_option_gid),
    FOREIGN KEY (custom_field_gid) REFERENCES custom_fields(gid)
);
"""

TASK_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    gid TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    resource_type TEXT,
    resource_subtype TEXT,
    notes TEXT,
    html_notes TEXT,
    permalink_url TEXT,
    assignee_gid TEXT,
    assignee_status TEXT,
    created_by_gid TEXT,
    completed_by_gid TEXT,
    parent_gid TEXT,
    workspace_gid TEXT,
    workspace_name TEXT,
    assignee_status_updated_at TEXT,
    created_at TEXT,
    modified_at TEXT,
    start_on TEXT,
    start_at TEXT,
    due_on TEXT,
    due_at TEXT,
    completed INTEGER,
    completed_at TEXT,
    actual_time_minutes INTEGER,
    liked INTEGER,
    num_likes INTEGER,
    is_subtask INTEGER,
    is_personal INTEGER DEFAULT 0,
    hierarchy_depth INTEGER,
    synced_at TEXT NOT NULL
);
"""

TASK_HIERARCHY_FLATTENED_SCHEMA = """
CREATE TABLE IF NOT EXISTS task_hierarchy_flattened (
    task_gid TEXT PRIMARY KEY,
    flattened_parent_gid TEXT,
    flattened_depth INTEGER,
    normalized_at TEXT NOT NULL,
    FOREIGN KEY (task_gid) REFERENCES tasks(gid)
);
"""

TASK_PROJECTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS task_projects (
    task_gid TEXT NOT NULL,
    project_gid TEXT NOT NULL,
    is_primary INTEGER,
    section_gid TEXT,
    section_name TEXT,
    synced_at TEXT NOT NULL,
    PRIMARY KEY (task_gid, project_gid),
    FOREIGN KEY (task_gid) REFERENCES tasks(gid),
    FOREIGN KEY (project_gid) REFERENCES projects(gid)
);
"""

TASK_CUSTOM_FIELDS_SCHEMA = """
CREATE TABLE IF NOT EXISTS task_custom_fields (
    task_gid TEXT NOT NULL,
    custom_field_gid TEXT NOT NULL,
    enum_option_gid TEXT,
    text_value TEXT,
    number_value REAL,
    date_value TEXT,
    display_value TEXT,
    is_value_set INTEGER,
    enabled INTEGER,
    synced_at TEXT NOT NULL,
    PRIMARY KEY (task_gid, custom_field_gid),
    FOREIGN KEY (task_gid) REFERENCES tasks(gid),
    FOREIGN KEY (custom_field_gid) REFERENCES custom_fields(gid)
);
"""

TASK_CUSTOM_FIELD_MULTI_ENUM_SCHEMA = """
CREATE TABLE IF NOT EXISTS task_custom_field_multi_enum (
    task_gid TEXT NOT NULL,
    custom_field_gid TEXT NOT NULL,
    enum_option_gid TEXT NOT NULL,
    position INTEGER,
    synced_at TEXT NOT NULL,
    PRIMARY KEY (task_gid, custom_field_gid, enum_option_gid),
    FOREIGN KEY (task_gid) REFERENCES tasks(gid),
    FOREIGN KEY (custom_field_gid) REFERENCES custom_fields(gid)
);
"""

TASK_RELATIONSHIPS_SCHEMA = """
CREATE TABLE IF NOT EXISTS task_relationships (
    task_gid TEXT NOT NULL,
    related_task_gid TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    synced_at TEXT NOT NULL,
    PRIMARY KEY (task_gid, related_task_gid, relation_type),
    FOREIGN KEY (task_gid) REFERENCES tasks(gid)
);
"""

TASK_FOLLOWERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS task_followers (
    task_gid TEXT NOT NULL,
    user_gid TEXT NOT NULL,
    synced_at TEXT NOT NULL,
    PRIMARY KEY (task_gid, user_gid),
    FOREIGN KEY (task_gid) REFERENCES tasks(gid),
    FOREIGN KEY (user_gid) REFERENCES users(gid)
);
"""

TAG_SCHEMA = """
CREATE TABLE IF NOT EXISTS tags (
    gid TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    synced_at TEXT NOT NULL
);
"""

TASK_TAGS_SCHEMA = """
CREATE TABLE IF NOT EXISTS task_tags (
    task_gid TEXT NOT NULL,
    tag_gid TEXT NOT NULL,
    tag_name TEXT,
    synced_at TEXT NOT NULL,
    PRIMARY KEY (task_gid, tag_gid),
    FOREIGN KEY (task_gid) REFERENCES tasks(gid),
    FOREIGN KEY (tag_gid) REFERENCES tags(gid)
);
"""

TASK_COMMENTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS task_comments (
    gid TEXT PRIMARY KEY,
    task_gid TEXT NOT NULL,
    created_at TEXT,
    created_by_gid TEXT,
    text TEXT,
    html_text TEXT,
    is_edited INTEGER,
    is_deleted INTEGER DEFAULT 0,
    synced_at TEXT NOT NULL,
    FOREIGN KEY (task_gid) REFERENCES tasks(gid)
);
"""

TASK_ATTACHMENTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS task_attachments (
    gid TEXT PRIMARY KEY,
    task_gid TEXT NOT NULL,
    parent_gid TEXT,
    parent_resource_type TEXT,
    name TEXT,
    resource_subtype TEXT,
    host TEXT,
    service TEXT,
    size INTEGER,
    created_at TEXT,
    created_by_gid TEXT,
    permanent_url TEXT,
    view_url TEXT,
    is_deleted INTEGER DEFAULT 0,
    synced_at TEXT NOT NULL,
    FOREIGN KEY (task_gid) REFERENCES tasks(gid)
);
"""

JIRA_PROJECT_MAPPINGS_SCHEMA = """
CREATE TABLE IF NOT EXISTS jira_project_mappings (
    project_gid TEXT PRIMARY KEY,
    jira_project_name TEXT,
    jira_project_key TEXT,
    synced_at TEXT NOT NULL
);
"""

USER_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    gid TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    resource_type TEXT,
    email TEXT,
    is_active INTEGER,
    synced_at TEXT NOT NULL
);
"""

TASK_JIRA_MAPPINGS_SCHEMA = """
CREATE TABLE IF NOT EXISTS task_jira_mappings (
    task_gid TEXT NOT NULL,
    jira_key TEXT NOT NULL,
    synced_at TEXT NOT NULL,
    PRIMARY KEY (task_gid, jira_key),
    UNIQUE (jira_key),
    FOREIGN KEY (task_gid) REFERENCES tasks(gid)
);
"""

TEAM_SCHEMA = """
CREATE TABLE IF NOT EXISTS teams (
    gid TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    organization_gid TEXT,
    is_public INTEGER,
    created_at TEXT,
    modified_at TEXT,
    synced_at TEXT NOT NULL
);
"""

PROJECT_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    gid TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    resource_subtype TEXT,
    archived INTEGER,
    permalink_url TEXT,
    created_at TEXT,
    modified_at TEXT,
    start_on TEXT,
    due_on TEXT,
    completed INTEGER,
    completed_at TEXT,
    completed_by_gid TEXT,
    owner_gid TEXT,
    owner_name TEXT,
    team_gid TEXT,
    team_name TEXT,
    privacy_setting TEXT,
    notes TEXT,
    html_notes TEXT,
    num_tasks INTEGER,
    num_incomplete_tasks INTEGER,
    num_completed_tasks INTEGER,
    task_counts_captured_at TEXT,
    is_personal_project INTEGER DEFAULT 0,
    synced_at TEXT NOT NULL
);
"""

PROJECT_CUSTOM_FIELDS_SCHEMA = """
CREATE TABLE IF NOT EXISTS project_custom_fields (
    project_gid TEXT NOT NULL,
    custom_field_gid TEXT NOT NULL,
    name TEXT,
    type TEXT,
    enum_value_gid TEXT,
    enum_value_name TEXT,
    number_value REAL,
    text_value TEXT,
    enabled INTEGER,
    synced_at TEXT NOT NULL,
    PRIMARY KEY (project_gid, custom_field_gid),
    FOREIGN KEY (project_gid) REFERENCES projects(gid)
);
"""

PROJECT_CUSTOM_FIELD_SETTINGS_SCHEMA = """
CREATE TABLE IF NOT EXISTS project_custom_field_settings (
    project_gid TEXT NOT NULL,
    custom_field_gid TEXT NOT NULL,
    custom_field_name TEXT,
    custom_field_type TEXT,
    is_important INTEGER,
    is_global_to_workspace INTEGER,
    synced_at TEXT NOT NULL,
    PRIMARY KEY (project_gid, custom_field_gid),
    FOREIGN KEY (project_gid) REFERENCES projects(gid)
);
"""

VIEW_PROJECT_CUSTOM_FIELD_CONFIGURATIONS = """
CREATE VIEW view_project_custom_field_configurations AS
WITH field_value_counts AS (
    SELECT
        project_gid,
        custom_field_gid,
        COUNT(DISTINCT task_gid) AS value_count
    FROM (
        SELECT
            tp.project_gid,
            tcf.custom_field_gid,
            tcf.task_gid
        FROM task_custom_fields tcf
        JOIN task_projects tp ON tp.task_gid = tcf.task_gid
        WHERE
            tcf.is_value_set = 1
            OR tcf.enum_option_gid IS NOT NULL
            OR tcf.text_value IS NOT NULL
            OR tcf.number_value IS NOT NULL
            OR tcf.date_value IS NOT NULL
            OR tcf.display_value IS NOT NULL
        UNION
        SELECT
            tp.project_gid,
            m.custom_field_gid,
            m.task_gid
        FROM task_custom_field_multi_enum m
        JOIN task_projects tp ON tp.task_gid = m.task_gid
    )
    GROUP BY project_gid, custom_field_gid
)
SELECT
    COALESCE(teams.name, projects.team_name) AS team_name,
    projects.team_gid AS teamId,
    projects.name AS project_name,
    projects.gid AS project_gid,
    projects.num_tasks AS project_num_tasks,
    projects.num_incomplete_tasks AS project_num_incomplete_tasks,
    projects.num_completed_tasks AS project_num_completed_tasks,
    projects.task_counts_captured_at AS project_task_counts_captured_at,
    settings.custom_field_gid AS custom_field_gid,
    settings.custom_field_name AS custom_field_name,
    settings.custom_field_type AS custom_field_type,
    settings.is_global_to_workspace AS is_global_to_workspace,
    settings.is_important AS is_important,
    COALESCE(counts.value_count, 0) AS value_count
FROM project_custom_field_settings AS settings
JOIN projects ON projects.gid = settings.project_gid
LEFT JOIN teams ON teams.gid = projects.team_gid
LEFT JOIN field_value_counts counts
    ON counts.project_gid = settings.project_gid
    AND counts.custom_field_gid = settings.custom_field_gid;
"""

VIEW_PROJECT_TASK_HIERARCHY_COUNTS = """
CREATE VIEW view_project_task_hierarchy_counts AS
WITH task_project_hierarchy AS (
    SELECT
        COALESCE(teams.name, projects.team_name) AS team_name,
        projects.name AS project_name,
        projects.gid AS project_gid,
        projects.num_tasks AS project_num_tasks,
        projects.num_incomplete_tasks AS project_num_incomplete_tasks,
        projects.num_completed_tasks AS project_num_completed_tasks,
        projects.task_counts_captured_at AS project_task_counts_captured_at,
        tasks.gid AS task_gid,
        COALESCE(tasks.hierarchy_depth, 0) AS hierarchy_depth,
        tasks.created_at AS created_at,
        tasks.modified_at AS modified_at
    FROM task_projects
    JOIN projects ON projects.gid = task_projects.project_gid
    JOIN tasks ON tasks.gid = task_projects.task_gid
    LEFT JOIN teams ON teams.gid = projects.team_gid
),
project_aggregates AS (
    SELECT
        team_name,
        project_name,
        project_gid,
        MAX(project_num_tasks) AS project_num_tasks,
        MAX(project_num_incomplete_tasks) AS project_num_incomplete_tasks,
        MAX(project_num_completed_tasks) AS project_num_completed_tasks,
        MAX(project_task_counts_captured_at) AS project_task_counts_captured_at,
        COUNT(task_gid) AS total_task_count,
        MIN(created_at) AS oldest_created_issue,
        MAX(modified_at) AS last_updated_issue,
        SUM(CASE WHEN hierarchy_depth = 0 THEN 1 ELSE 0 END) AS level_0_tasks,
        SUM(CASE WHEN hierarchy_depth = 1 THEN 1 ELSE 0 END) AS level_1_tasks,
        SUM(CASE WHEN hierarchy_depth = 2 THEN 1 ELSE 0 END) AS level_2_tasks,
        SUM(CASE WHEN hierarchy_depth = 3 THEN 1 ELSE 0 END) AS level_3_tasks,
        SUM(CASE WHEN hierarchy_depth = 4 THEN 1 ELSE 0 END) AS level_4_tasks
    FROM task_project_hierarchy
    GROUP BY
        team_name,
        project_name,
        project_gid
)
SELECT
    team_name,
    project_name,
    project_gid,
    project_num_tasks,
    project_num_incomplete_tasks,
    project_num_completed_tasks,
    project_task_counts_captured_at,
    level_0_tasks,
    level_1_tasks,
    level_2_tasks,
    level_3_tasks,
    level_4_tasks,
    total_task_count,
    oldest_created_issue,
    last_updated_issue
FROM project_aggregates;
"""

VIEW_TEAM_MEMBERSHIPS = """
CREATE VIEW view_team_memberships AS
SELECT
    tm.team_gid,
    t.name AS team_name,
    t.is_public AS team_is_public,
    tm.user_gid,
    CASE
        WHEN u.name IS NOT NULL AND TRIM(u.name) != ''
            THEN u.name
        ELSE 'unknown_user ' || tm.user_gid
    END AS user_name,
    u.email AS user_email,
    u.resource_type,
    u.is_active AS user_is_active,
    tm.role,
    tm.is_admin AS is_admin,
    tm.is_active AS membership_is_active,
    tm.synced_at AS membership_synced_at,
    u.synced_at AS user_synced_at,
    t.synced_at AS team_synced_at
FROM team_members tm
INNER JOIN teams t
    ON tm.team_gid = t.gid
LEFT JOIN users u
    ON tm.user_gid = u.gid;
"""

TEAM_MEMBERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS team_members (
    team_gid TEXT NOT NULL,
    user_gid TEXT NOT NULL,
    role TEXT,
    is_admin INTEGER,
    is_active INTEGER,
    synced_at TEXT NOT NULL,
    PRIMARY KEY (team_gid, user_gid),
    FOREIGN KEY (team_gid) REFERENCES teams(gid)
);
"""

USER_WORKSPACES_SCHEMA = """
CREATE TABLE IF NOT EXISTS user_workspaces (
    user_gid TEXT NOT NULL,
    workspace_gid TEXT NOT NULL,
    workspace_name TEXT,
    synced_at TEXT NOT NULL,
    PRIMARY KEY (user_gid, workspace_gid),
    FOREIGN KEY (user_gid) REFERENCES users(gid)
);
"""

USER_ATLASSIAN_ACCOUNTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS user_atlassian_accounts (
    user_gid TEXT PRIMARY KEY,
    atlassian_account_id TEXT NOT NULL,
    atlassian_email TEXT,
    synced_at TEXT NOT NULL
);
"""

PERSONAL_TASK_CURSORS_SCHEMA = """
CREATE TABLE IF NOT EXISTS personal_task_cursors (
    assignee_gid TEXT PRIMARY KEY,
    last_modified_at TEXT,
    synced_at TEXT NOT NULL
);
"""

EXPORTED_TASKS_SCHEMA = """
CREATE TABLE IF NOT EXISTS exported_tasks (
    export_id TEXT NOT NULL,
    task_gid TEXT NOT NULL,
    project_gid TEXT,
    exported_at TEXT NOT NULL,
    PRIMARY KEY (export_id, task_gid)
);
"""


class Database:
    """Lightweight wrapper around sqlite3 for schema management and inserts."""

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        if self.db_path.parent != Path("."):
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self, connection: sqlite3.Connection) -> None:
        connection.executescript(CUSTOM_FIELDS_SCHEMA)
        connection.executescript(CUSTOM_FIELD_ENUM_OPTIONS_SCHEMA)
        connection.executescript(TASK_SCHEMA)
        connection.executescript(TASK_HIERARCHY_FLATTENED_SCHEMA)
        connection.executescript(TASK_PROJECTS_SCHEMA)
        connection.executescript(TASK_CUSTOM_FIELDS_SCHEMA)
        connection.executescript(TASK_CUSTOM_FIELD_MULTI_ENUM_SCHEMA)
        connection.executescript(TASK_RELATIONSHIPS_SCHEMA)
        connection.executescript(TASK_FOLLOWERS_SCHEMA)
        connection.executescript(TASK_COMMENTS_SCHEMA)
        connection.executescript(TASK_ATTACHMENTS_SCHEMA)
        connection.executescript(TASK_JIRA_MAPPINGS_SCHEMA)
        connection.executescript(JIRA_PROJECT_MAPPINGS_SCHEMA)
        connection.executescript(TAG_SCHEMA)
        connection.executescript(TASK_TAGS_SCHEMA)
        connection.executescript(USER_SCHEMA)
        connection.executescript(TEAM_SCHEMA)
        connection.executescript(PROJECT_SCHEMA)
        connection.executescript(PROJECT_CUSTOM_FIELDS_SCHEMA)
        connection.executescript(PROJECT_CUSTOM_FIELD_SETTINGS_SCHEMA)
        connection.executescript(TEAM_MEMBERS_SCHEMA)
        connection.executescript(USER_WORKSPACES_SCHEMA)
        connection.executescript(USER_ATLASSIAN_ACCOUNTS_SCHEMA)
        connection.executescript(PERSONAL_TASK_CURSORS_SCHEMA)
        connection.executescript(EXPORTED_TASKS_SCHEMA)
        connection.commit()
        self._ensure_column(connection, "tasks", "hierarchy_depth", "INTEGER")
        self._ensure_column(connection, "tasks", "is_personal", "INTEGER DEFAULT 0")
        self._ensure_column(connection, "projects", "archived", "INTEGER")
        self._ensure_column(connection, "projects", "num_tasks", "INTEGER")
        self._ensure_column(connection, "projects", "num_incomplete_tasks", "INTEGER")
        self._ensure_column(connection, "projects", "num_completed_tasks", "INTEGER")
        self._ensure_column(connection, "projects", "task_counts_captured_at", "TEXT")
        self._ensure_column(connection, "projects", "is_personal_project", "INTEGER DEFAULT 0")
        self._ensure_column(connection, "task_comments", "is_deleted", "INTEGER DEFAULT 0")
        self._ensure_column(connection, "task_attachments", "is_deleted", "INTEGER DEFAULT 0")
        self._ensure_column(connection, "user_atlassian_accounts", "atlassian_email", "TEXT")
        self._ensure_column(connection, "team_members", "is_admin", "INTEGER")
        self._ensure_task_jira_mapping_schema(connection)
        self._ensure_indexes(connection)
        self._ensure_views(connection)

    def upsert_team(self, connection: sqlite3.Connection, team: Dict[str, Any], synced_at: datetime) -> None:
        organization_gid = _extract_organization_gid(team)
        payload = {
            "gid": team.get("gid"),
            "name": team.get("name"),
            "description": team.get("description"),
            "organization_gid": organization_gid,
            "is_public": _bool_to_int(team.get("is_public")),
            "created_at": team.get("created_at"),
            "modified_at": team.get("modified_at"),
            "synced_at": synced_at.isoformat(),
        }
        connection.execute(
            """
            INSERT INTO teams (
                gid, name, description, organization_gid, is_public,
                created_at, modified_at, synced_at
            )
            VALUES (
                :gid, :name, :description, :organization_gid, :is_public,
                :created_at, :modified_at, :synced_at
            )
            ON CONFLICT(gid) DO UPDATE SET
                name=excluded.name,
                description=excluded.description,
                organization_gid=excluded.organization_gid,
                is_public=excluded.is_public,
                created_at=excluded.created_at,
                modified_at=excluded.modified_at,
                synced_at=excluded.synced_at
            """,
            payload,
        )

    def upsert_project(self, connection: sqlite3.Connection, project: Dict[str, Any], synced_at: datetime) -> None:
        owner = project.get("owner") or {}
        team = project.get("team") or {}
        completed_by = project.get("completed_by") or {}
        payload = {
            "gid": project.get("gid"),
            "name": project.get("name"),
            "resource_subtype": project.get("resource_subtype"),
            "archived": _bool_to_int(project.get("archived")),
            "permalink_url": project.get("permalink_url"),
            "created_at": project.get("created_at"),
            "modified_at": project.get("modified_at"),
            "start_on": project.get("start_on"),
            "due_on": project.get("due_on"),
            "completed": _bool_to_int(project.get("completed")),
            "completed_at": project.get("completed_at"),
            "completed_by_gid": completed_by.get("gid"),
            "owner_gid": owner.get("gid"),
            "owner_name": owner.get("name"),
            "team_gid": team.get("gid"),
            "team_name": team.get("name"),
            "privacy_setting": project.get("privacy_setting"),
            "notes": project.get("notes"),
            "html_notes": project.get("html_notes"),
            "num_tasks": project.get("num_tasks"),
            "num_incomplete_tasks": project.get("num_incomplete_tasks"),
            "num_completed_tasks": project.get("num_completed_tasks"),
            "task_counts_captured_at": project.get("task_counts_captured_at"),
            "is_personal_project": _bool_to_int(project.get("is_personal_project", False)),
            "synced_at": synced_at.isoformat(),
        }
        connection.execute(
            """
            INSERT INTO projects (
                gid, name, resource_subtype, archived, permalink_url,
                created_at, modified_at, start_on, due_on, completed,
                completed_at, completed_by_gid, owner_gid, owner_name,
                team_gid, team_name, privacy_setting, notes, html_notes,
                num_tasks, num_incomplete_tasks, num_completed_tasks, task_counts_captured_at,
                is_personal_project, synced_at
            )
            VALUES (
                :gid, :name, :resource_subtype, :archived, :permalink_url,
                :created_at, :modified_at, :start_on, :due_on, :completed,
                :completed_at, :completed_by_gid, :owner_gid, :owner_name,
                :team_gid, :team_name, :privacy_setting, :notes, :html_notes,
                :num_tasks, :num_incomplete_tasks, :num_completed_tasks, :task_counts_captured_at,
                :is_personal_project, :synced_at
            )
            ON CONFLICT(gid) DO UPDATE SET
                name=excluded.name,
                resource_subtype=excluded.resource_subtype,
                archived=excluded.archived,
                permalink_url=excluded.permalink_url,
                created_at=excluded.created_at,
                modified_at=excluded.modified_at,
                start_on=excluded.start_on,
                due_on=excluded.due_on,
                completed=excluded.completed,
                completed_at=excluded.completed_at,
                completed_by_gid=excluded.completed_by_gid,
                owner_gid=excluded.owner_gid,
                owner_name=excluded.owner_name,
                team_gid=excluded.team_gid,
                team_name=excluded.team_name,
                privacy_setting=excluded.privacy_setting,
                notes=excluded.notes,
                html_notes=excluded.html_notes,
                num_tasks=excluded.num_tasks,
                num_incomplete_tasks=excluded.num_incomplete_tasks,
                num_completed_tasks=excluded.num_completed_tasks,
                task_counts_captured_at=excluded.task_counts_captured_at,
                is_personal_project=excluded.is_personal_project,
                synced_at=excluded.synced_at
            """,
            payload,
        )

        self._replace_project_custom_fields(
            connection,
            project.get("gid"),
            project.get("custom_fields") or [],
            synced_at,
        )
        self._replace_project_custom_field_settings(
            connection,
            project.get("gid"),
            project.get("custom_field_settings") or [],
            synced_at,
        )

    def upsert_team_members(
        self,
        connection: sqlite3.Connection,
        team_gid: str,
        members: Iterable[Dict[str, Any]],
        synced_at: datetime,
    ) -> None:
        rows = [
            {
                "team_gid": team_gid,
                "user_gid": member.get("user", {}).get("gid"),
                "role": member.get("role"),
                "is_admin": _bool_to_int(member.get("is_admin")),
                "is_active": _bool_to_int(member.get("user", {}).get("is_active")),
                "synced_at": synced_at.isoformat(),
            }
            for member in members
        ]
        if not rows:
            return
        connection.executemany(
            """
            INSERT INTO team_members (
                team_gid, user_gid, role, is_admin, is_active, synced_at
            )
            VALUES (
                :team_gid, :user_gid, :role, :is_admin, :is_active, :synced_at
            )
            ON CONFLICT(team_gid, user_gid) DO UPDATE SET
                role=excluded.role,
                is_admin=excluded.is_admin,
                is_active=excluded.is_active,
                synced_at=excluded.synced_at
            """,
            rows,
        )

    def _replace_project_custom_fields(
        self,
        connection: sqlite3.Connection,
        project_gid: Optional[str],
        custom_fields: Iterable[Dict[str, Any]],
        synced_at: datetime,
    ) -> None:
        if not project_gid:
            return
        connection.execute("DELETE FROM project_custom_fields WHERE project_gid = ?", (project_gid,))
        rows = []
        for field in custom_fields:
            if not isinstance(field, dict):
                continue
            enum_value = field.get("enum_value") or {}
            custom_field_gid = field.get("gid")
            if not custom_field_gid:
                continue
            rows.append(
                {
                    "project_gid": project_gid,
                    "custom_field_gid": custom_field_gid,
                    "name": field.get("name"),
                    "type": field.get("type"),
                    "enum_value_gid": enum_value.get("gid"),
                    "enum_value_name": enum_value.get("name"),
                    "number_value": field.get("number_value"),
                    "text_value": field.get("text_value"),
                    "enabled": _bool_to_int(field.get("enabled")),
                    "synced_at": synced_at.isoformat(),
                }
            )
        if rows:
            connection.executemany(
                """
                INSERT INTO project_custom_fields (
                    project_gid, custom_field_gid, name, type,
                    enum_value_gid, enum_value_name, number_value,
                    text_value, enabled, synced_at
                )
                VALUES (
                    :project_gid, :custom_field_gid, :name, :type,
                    :enum_value_gid, :enum_value_name, :number_value,
                    :text_value, :enabled, :synced_at
                )
                """,
            rows,
        )

    def upsert_user(self, connection: sqlite3.Connection, user: Dict[str, Any], synced_at: datetime) -> None:
        payload = {
            "gid": user.get("gid"),
            "name": user.get("name"),
            "resource_type": user.get("resource_type"),
            "email": user.get("email"),
            "is_active": _bool_to_int(user.get("is_active")),
            "synced_at": synced_at.isoformat(),
        }
        connection.execute(
            """
            INSERT INTO users (
                gid, name, resource_type, email, is_active, synced_at
            )
            VALUES (
                :gid, :name, :resource_type, :email, :is_active, :synced_at
            )
            ON CONFLICT(gid) DO UPDATE SET
                name=excluded.name,
                resource_type=excluded.resource_type,
                email=excluded.email,
                is_active=excluded.is_active,
                synced_at=excluded.synced_at
            """,
            payload,
        )
        self._replace_user_workspaces(
            connection,
            user.get("gid"),
            user.get("workspaces") or [],
            synced_at,
        )

    def _replace_user_workspaces(
        self,
        connection: sqlite3.Connection,
        user_gid: Optional[str],
        workspaces: Iterable[Dict[str, Any]],
        synced_at: datetime,
    ) -> None:
        if not user_gid:
            return
        connection.execute("DELETE FROM user_workspaces WHERE user_gid = ?", (user_gid,))
        rows = []
        for workspace in workspaces:
            if not isinstance(workspace, dict):
                continue
            workspace_gid = workspace.get("gid")
            if not workspace_gid:
                continue
            rows.append(
                {
                    "user_gid": user_gid,
                    "workspace_gid": workspace_gid,
                    "workspace_name": workspace.get("name"),
                    "synced_at": synced_at.isoformat(),
                }
            )
        if rows:
            connection.executemany(
                """
                INSERT INTO user_workspaces (
                    user_gid, workspace_gid, workspace_name, synced_at
                )
                VALUES (
                    :user_gid, :workspace_gid, :workspace_name, :synced_at
                )
                """,
                rows,
            )

    def replace_user_atlassian_accounts(
        self,
        connection: sqlite3.Connection,
        mappings: Iterable[Tuple[str, str] | Dict[str, str]],
        synced_at: datetime,
    ) -> int:
        rows = []
        for item in mappings:
            if isinstance(item, dict):
                user_gid = (item.get("gid") or item.get("user_gid") or "").strip()
                account_id = (
                    item.get("atlassianAccountId")
                    or item.get("atlassian_account_id")
                    or ""
                ).strip()
                account_email = (
                    item.get("atlassianEmail")
                    or item.get("atlassian_email")
                    or ""
                ).strip()
            else:
                user_gid = (item[0] or "").strip()
                account_id = (item[1] or "").strip()
                account_email = item[2].strip() if len(item) > 2 and item[2] else ""
            if not user_gid or not account_id:
                continue
            rows.append(
                {
                    "user_gid": user_gid,
                    "atlassian_account_id": account_id,
                    "atlassian_email": account_email or None,
                    "synced_at": synced_at.isoformat(),
                }
            )
        if rows:
            connection.executemany(
                """
                INSERT INTO user_atlassian_accounts (
                    user_gid, atlassian_account_id, atlassian_email, synced_at
                )
                VALUES (:user_gid, :atlassian_account_id, :atlassian_email, :synced_at)
                ON CONFLICT(user_gid) DO UPDATE SET
                    atlassian_account_id=excluded.atlassian_account_id,
                    atlassian_email=excluded.atlassian_email,
                    synced_at=excluded.synced_at
                """,
                rows,
            )
            connection.commit()
        return len(rows)

    def load_atlassian_account_map(self, connection: sqlite3.Connection) -> Dict[str, Dict[str, Optional[str]]]:
        rows = connection.execute(
            "SELECT user_gid, atlassian_account_id, atlassian_email FROM user_atlassian_accounts"
        ).fetchall()
        mapping = {}
        for row in rows:
            user_gid = row["user_gid"]
            if not user_gid:
                continue
            mapping[user_gid] = {
                "account_id": row["atlassian_account_id"] or "",
                "email": row["atlassian_email"] or "",
            }
        return mapping

    def upsert_custom_field(
        self,
        connection: sqlite3.Connection,
        custom_field: Dict[str, Any],
        synced_at: datetime,
    ) -> None:
        workspace = custom_field.get("workspace") or {}
        payload = {
            "gid": custom_field.get("gid"),
            "name": custom_field.get("name"),
            "resource_type": custom_field.get("resource_type"),
            "resource_subtype": custom_field.get("resource_subtype"),
            "description": custom_field.get("description"),
            "enabled": _bool_to_int(custom_field.get("enabled")),
            "is_global_to_workspace": _bool_to_int(custom_field.get("is_global_to_workspace")),
            "workspace_gid": workspace.get("gid"),
            "workspace_name": workspace.get("name"),
            "format": custom_field.get("format"),
            "precision": custom_field.get("precision"),
            "currency_code": custom_field.get("currency_code"),
            "custom_label": custom_field.get("custom_label"),
            "custom_label_position": custom_field.get("custom_label_position"),
            "is_formula_field": _bool_to_int(custom_field.get("is_formula_field")),
            "modified_at": custom_field.get("modified_at"),
            "synced_at": synced_at.isoformat(),
        }
        connection.execute(
            """
            INSERT INTO custom_fields (
                gid, name, resource_type, resource_subtype, description,
                enabled, is_global_to_workspace, workspace_gid, workspace_name,
                format, precision, currency_code, custom_label,
                custom_label_position, is_formula_field, modified_at, synced_at
            )
            VALUES (
                :gid, :name, :resource_type, :resource_subtype, :description,
                :enabled, :is_global_to_workspace, :workspace_gid, :workspace_name,
                :format, :precision, :currency_code, :custom_label,
                :custom_label_position, :is_formula_field, :modified_at, :synced_at
            )
            ON CONFLICT(gid) DO UPDATE SET
                name=excluded.name,
                resource_type=excluded.resource_type,
                resource_subtype=excluded.resource_subtype,
                description=excluded.description,
                enabled=excluded.enabled,
                is_global_to_workspace=excluded.is_global_to_workspace,
                workspace_gid=excluded.workspace_gid,
                workspace_name=excluded.workspace_name,
                format=excluded.format,
                precision=excluded.precision,
                currency_code=excluded.currency_code,
                custom_label=excluded.custom_label,
                custom_label_position=excluded.custom_label_position,
                is_formula_field=excluded.is_formula_field,
                modified_at=excluded.modified_at,
                synced_at=excluded.synced_at
            """,
            payload,
        )
        self._replace_custom_field_enum_options(
            connection,
            custom_field.get("gid"),
            custom_field.get("enum_options") or [],
            synced_at,
        )

    def _replace_custom_field_enum_options(
        self,
        connection: sqlite3.Connection,
        custom_field_gid: Optional[str],
        enum_options: Iterable[Dict[str, Any]],
        synced_at: datetime,
    ) -> None:
        if not custom_field_gid:
            return
        connection.execute(
            "DELETE FROM custom_field_enum_options WHERE custom_field_gid = ?",
            (custom_field_gid,),
        )
        rows = []
        for position, option in enumerate(enum_options):
            if not isinstance(option, dict):
                continue
            option_gid = option.get("gid")
            if not option_gid:
                continue
            rows.append(
                {
                    "custom_field_gid": custom_field_gid,
                    "enum_option_gid": option_gid,
                    "name": option.get("name"),
                    "color": option.get("color"),
                    "enabled": _bool_to_int(option.get("enabled")),
                    "position": position,
                    "synced_at": synced_at.isoformat(),
                }
            )
        if rows:
            connection.executemany(
                """
                INSERT INTO custom_field_enum_options (
                    custom_field_gid, enum_option_gid, name, color,
                    enabled, position, synced_at
                )
                VALUES (
                    :custom_field_gid, :enum_option_gid, :name, :color,
                    :enabled, :position, :synced_at
                )
                """,
                rows,
            )

    def upsert_task(self, connection: sqlite3.Connection, task: Dict[str, Any], synced_at: datetime) -> None:
        task_gid = task.get("gid")
        existing_row: Optional[sqlite3.Row] = None
        existing_parent_gid: Optional[str] = None
        existing_depth: Optional[int] = None
        if task_gid:
            existing_row = connection.execute(
                "SELECT parent_gid, hierarchy_depth FROM tasks WHERE gid = ?",
                (task_gid,),
            ).fetchone()
            if existing_row is not None:
                existing_parent_gid = existing_row["parent_gid"]
                existing_depth = existing_row["hierarchy_depth"]

        assignee = task.get("assignee") or {}
        created_by = task.get("created_by") or {}
        completed_by = task.get("completed_by") or {}
        parent = task.get("parent") or {}
        workspace = task.get("workspace") or {}
        payload = {
            "gid": task.get("gid"),
            "name": task.get("name") or "",
            "resource_type": task.get("resource_type"),
            "resource_subtype": task.get("resource_subtype"),
            "notes": task.get("notes"),
            "html_notes": task.get("html_notes"),
            "permalink_url": task.get("permalink_url"),
            "assignee_gid": assignee.get("gid"),
            "assignee_status": task.get("assignee_status"),
            "created_by_gid": created_by.get("gid"),
            "completed_by_gid": completed_by.get("gid"),
            "parent_gid": parent.get("gid"),
            "workspace_gid": workspace.get("gid"),
            "workspace_name": workspace.get("name"),
            "assignee_status_updated_at": task.get("assignee_status_updated_at"),
            "created_at": task.get("created_at"),
            "modified_at": task.get("modified_at"),
            "start_on": task.get("start_on"),
            "start_at": task.get("start_at"),
            "due_on": task.get("due_on"),
            "due_at": task.get("due_at"),
            "completed": _bool_to_int(task.get("completed")),
            "completed_at": task.get("completed_at"),
            "actual_time_minutes": task.get("actual_time_minutes"),
            "liked": _bool_to_int(task.get("liked")),
            "num_likes": task.get("num_likes"),
            "is_subtask": _bool_to_int(bool(parent.get("gid"))),
            "is_personal": _bool_to_int(task.get("is_personal", False)),
            "synced_at": synced_at.isoformat(),
        }
        connection.execute(
            """
            INSERT INTO tasks (
                gid, name, resource_type, resource_subtype, notes, html_notes,
                permalink_url, assignee_gid, assignee_status, created_by_gid,
                completed_by_gid, parent_gid, workspace_gid, workspace_name,
                assignee_status_updated_at, created_at, modified_at, start_on,
                start_at, due_on, due_at, completed, completed_at,
                actual_time_minutes, liked, num_likes, is_subtask, is_personal, synced_at
            )
            VALUES (
                :gid, :name, :resource_type, :resource_subtype, :notes, :html_notes,
                :permalink_url, :assignee_gid, :assignee_status, :created_by_gid,
                :completed_by_gid, :parent_gid, :workspace_gid, :workspace_name,
                :assignee_status_updated_at, :created_at, :modified_at, :start_on,
                :start_at, :due_on, :due_at, :completed, :completed_at,
                :actual_time_minutes, :liked, :num_likes, :is_subtask, :is_personal, :synced_at
            )
            ON CONFLICT(gid) DO UPDATE SET
                name=excluded.name,
                resource_type=excluded.resource_type,
                resource_subtype=excluded.resource_subtype,
                notes=excluded.notes,
                html_notes=excluded.html_notes,
                permalink_url=excluded.permalink_url,
                assignee_gid=excluded.assignee_gid,
                assignee_status=excluded.assignee_status,
                created_by_gid=excluded.created_by_gid,
                completed_by_gid=excluded.completed_by_gid,
                parent_gid=excluded.parent_gid,
                workspace_gid=excluded.workspace_gid,
                workspace_name=excluded.workspace_name,
                assignee_status_updated_at=excluded.assignee_status_updated_at,
                created_at=excluded.created_at,
                modified_at=excluded.modified_at,
                start_on=excluded.start_on,
                start_at=excluded.start_at,
                due_on=excluded.due_on,
                due_at=excluded.due_at,
                completed=excluded.completed,
                completed_at=excluded.completed_at,
                actual_time_minutes=excluded.actual_time_minutes,
                liked=excluded.liked,
                num_likes=excluded.num_likes,
                is_subtask=excluded.is_subtask,
                is_personal=excluded.is_personal,
                synced_at=excluded.synced_at
            """,
            payload,
        )

        gid = payload["gid"]
        if gid:
            parent_changed = existing_parent_gid != payload["parent_gid"]
            should_recalculate_tree = existing_row is None or parent_changed or existing_depth is None
            if should_recalculate_tree:
                self._recompute_subtree_depths(connection, gid)
            else:
                self._update_single_task_depth(connection, gid)

        parent_gid = (task.get("parent") or {}).get("gid")
        self._replace_task_projects(
            connection,
            task.get("gid"),
            task.get("memberships") or [],
            parent_gid,
            synced_at,
        )
        self._replace_task_custom_fields(connection, task.get("gid"), task.get("custom_fields") or [], synced_at)
        self._replace_task_relationships(
            connection,
            task.get("gid"),
            task.get("dependencies") or [],
            "dependency",
            synced_at,
        )
        self._replace_task_relationships(
            connection,
            task.get("gid"),
            task.get("dependents") or [],
            "dependent",
            synced_at,
        )
        self._replace_task_followers(connection, task.get("gid"), task.get("followers") or [], synced_at)
        self.replace_task_tags(connection, task.get("gid"), task.get("tags") or [], synced_at)

    def _replace_task_projects(
        self,
        connection: sqlite3.Connection,
        task_gid: Optional[str],
        memberships: Iterable[Dict[str, Any]],
        parent_gid: Optional[str],
        synced_at: datetime,
    ) -> None:
        if not task_gid:
            return
        connection.execute("DELETE FROM task_projects WHERE task_gid = ?", (task_gid,))
        rows = []
        for index, membership in enumerate(memberships):
            if not isinstance(membership, dict):
                continue
            project = membership.get("project") or {}
            project_gid = project.get("gid")
            if not project_gid:
                continue
            section = membership.get("section") or {}
            is_primary = membership.get("is_primary")
            if is_primary is None:
                is_primary = 1 if index == 0 else 0
            rows.append(
                {
                    "task_gid": task_gid,
                    "project_gid": project_gid,
                    "is_primary": _bool_to_int(is_primary),
                    "section_gid": section.get("gid"),
                    "section_name": section.get("name"),
                    "synced_at": synced_at.isoformat(),
                }
            )
        if rows:
            connection.executemany(
                """
                INSERT INTO task_projects (
                    task_gid, project_gid, is_primary, section_gid, section_name, synced_at
                )
                VALUES (
                    :task_gid, :project_gid, :is_primary, :section_gid, :section_name, :synced_at
                )
                """,
                rows,
            )
        elif task_gid and parent_gid:
            self._copy_parent_projects(connection, task_gid, parent_gid, synced_at)

    def _copy_parent_projects(
        self,
        connection: sqlite3.Connection,
        task_gid: str,
        parent_gid: str,
        synced_at: datetime,
    ) -> bool:
        parent_rows = connection.execute(
            """
            SELECT project_gid, is_primary, section_gid, section_name
            FROM task_projects
            WHERE task_gid = ?
            """,
            (parent_gid,),
        ).fetchall()
        if not parent_rows:
            return False
        payloads = []
        for parent_row in parent_rows:
            payloads.append(
                {
                    "task_gid": task_gid,
                    "project_gid": parent_row["project_gid"],
                    "is_primary": parent_row["is_primary"],
                    "section_gid": parent_row["section_gid"],
                    "section_name": parent_row["section_name"],
                    "synced_at": synced_at.isoformat(),
                }
            )
        connection.executemany(
            """
            INSERT INTO task_projects (
                task_gid, project_gid, is_primary, section_gid, section_name, synced_at
            )
            VALUES (
                :task_gid, :project_gid, :is_primary, :section_gid, :section_name, :synced_at
            )
            """,
            payloads,
        )
        return True

    def backfill_child_task_projects(
        self,
        connection: sqlite3.Connection,
        synced_at: datetime,
    ) -> int:
        rows = connection.execute(
            """
            SELECT t.gid, t.parent_gid
            FROM tasks t
            WHERE t.parent_gid IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1
                  FROM task_projects tp
                  WHERE tp.task_gid = t.gid
              )
            """
        ).fetchall()
        backfilled = 0
        for row in rows:
            parent_gid = row["parent_gid"]
            if not parent_gid:
                continue
            if self._copy_parent_projects(connection, row["gid"], parent_gid, synced_at):
                backfilled += 1
        return backfilled

    def _replace_task_custom_fields(
        self,
        connection: sqlite3.Connection,
        task_gid: Optional[str],
        custom_fields: Iterable[Dict[str, Any]],
        synced_at: datetime,
    ) -> None:
        if not task_gid:
            return
        connection.execute("DELETE FROM task_custom_fields WHERE task_gid = ?", (task_gid,))
        connection.execute("DELETE FROM task_custom_field_multi_enum WHERE task_gid = ?", (task_gid,))
        single_rows = []
        multi_rows = []
        for custom_field in custom_fields:
            if not isinstance(custom_field, dict):
                continue
            custom_field_gid = custom_field.get("gid")
            if not custom_field_gid:
                continue
            field_type = custom_field.get("type")
            enum_option = custom_field.get("enum_value") or {}
            multi_enum_values = custom_field.get("multi_enum_values") or []
            date_value = custom_field.get("date_value")
            if isinstance(date_value, (dict, list)):
                date_value = json.dumps(date_value, sort_keys=True)
            enabled = custom_field.get("enabled")
            display_value = custom_field.get("display_value")
            is_value_set = _bool_to_int(custom_field.get("is_value_set"))
            single_rows.append(
                {
                    "task_gid": task_gid,
                    "custom_field_gid": custom_field_gid,
                    "enum_option_gid": enum_option.get("gid"),
                    "text_value": custom_field.get("text_value"),
                    "number_value": custom_field.get("number_value"),
                    "date_value": date_value,
                    "display_value": display_value,
                    "is_value_set": is_value_set,
                    "enabled": _bool_to_int(enabled),
                    "synced_at": synced_at.isoformat(),
                }
            )
            if field_type == "multi_enum":
                for position, option in enumerate(multi_enum_values):
                    if not isinstance(option, dict):
                        continue
                    option_gid = option.get("gid")
                    if not option_gid:
                        continue
                    multi_rows.append(
                        {
                            "task_gid": task_gid,
                            "custom_field_gid": custom_field_gid,
                            "enum_option_gid": option_gid,
                            "position": position,
                            "synced_at": synced_at.isoformat(),
                        }
                    )
        if single_rows:
            connection.executemany(
                """
                INSERT INTO task_custom_fields (
                    task_gid, custom_field_gid, enum_option_gid, text_value,
                    number_value, date_value, display_value, is_value_set,
                    enabled, synced_at
                )
                VALUES (
                    :task_gid, :custom_field_gid, :enum_option_gid, :text_value,
                    :number_value, :date_value, :display_value, :is_value_set,
                    :enabled, :synced_at
                )
                """,
                single_rows,
            )
        if multi_rows:
            connection.executemany(
                """
                INSERT INTO task_custom_field_multi_enum (
                    task_gid, custom_field_gid, enum_option_gid, position, synced_at
                )
                VALUES (
                    :task_gid, :custom_field_gid, :enum_option_gid, :position, :synced_at
                )
                """,
                multi_rows,
            )

    def _replace_task_relationships(
        self,
        connection: sqlite3.Connection,
        task_gid: Optional[str],
        related_tasks: Iterable[Dict[str, Any]],
        relation_type: str,
        synced_at: datetime,
    ) -> None:
        if not task_gid:
            return
        connection.execute(
            "DELETE FROM task_relationships WHERE task_gid = ? AND relation_type = ?",
            (task_gid, relation_type),
        )
        rows = []
        for related in related_tasks:
            if not isinstance(related, dict):
                continue
            related_gid = related.get("gid")
            if not related_gid:
                continue
            rows.append(
                {
                    "task_gid": task_gid,
                    "related_task_gid": related_gid,
                    "relation_type": relation_type,
                    "synced_at": synced_at.isoformat(),
                }
            )
        if rows:
            connection.executemany(
                """
                INSERT INTO task_relationships (
                    task_gid, related_task_gid, relation_type, synced_at
                )
                VALUES (
                    :task_gid, :related_task_gid, :relation_type, :synced_at
                )
                """,
                rows,
            )

    def _replace_task_followers(
        self,
        connection: sqlite3.Connection,
        task_gid: Optional[str],
        followers: Iterable[Dict[str, Any]],
        synced_at: datetime,
    ) -> None:
        if not task_gid:
            return
        connection.execute("DELETE FROM task_followers WHERE task_gid = ?", (task_gid,))
        rows = []
        for follower in followers:
            if not isinstance(follower, dict):
                continue
            follower_gid = follower.get("gid")
            if not follower_gid:
                continue
            rows.append(
                {
                    "task_gid": task_gid,
                    "user_gid": follower_gid,
                    "synced_at": synced_at.isoformat(),
                }
            )
        if rows:
            connection.executemany(
                """
                INSERT INTO task_followers (
                    task_gid, user_gid, synced_at
                )
                VALUES (
                    :task_gid, :user_gid, :synced_at
                )
                """,
                rows,
            )

    def replace_task_tags(
        self,
        connection: sqlite3.Connection,
        task_gid: Optional[str],
        tags: Iterable[Dict[str, Any]],
        synced_at: datetime,
    ) -> None:
        if not task_gid:
            return
        connection.execute("DELETE FROM task_tags WHERE task_gid = ?", (task_gid,))
        rows = []
        tag_rows = []
        for tag in tags:
            if not isinstance(tag, dict):
                continue
            tag_gid = tag.get("gid")
            tag_name = tag.get("name")
            if not tag_gid:
                continue
            rows.append(
                {
                    "task_gid": task_gid,
                    "tag_gid": tag_gid,
                    "tag_name": tag_name,
                    "synced_at": synced_at.isoformat(),
                }
            )
            tag_rows.append(
                {
                    "gid": tag_gid,
                    "name": tag_name or "",
                    "synced_at": synced_at.isoformat(),
                }
            )
        if tag_rows:
            connection.executemany(
                """
                INSERT INTO tags (gid, name, synced_at)
                VALUES (:gid, :name, :synced_at)
                ON CONFLICT(gid) DO UPDATE SET
                    name=excluded.name,
                    synced_at=excluded.synced_at
                """,
                tag_rows,
            )
        if rows:
            connection.executemany(
                """
                INSERT INTO task_tags (task_gid, tag_gid, tag_name, synced_at)
                VALUES (:task_gid, :tag_gid, :tag_name, :synced_at)
                """,
                rows,
            )
    def recalculate_all_task_depths(
        self,
        connection: sqlite3.Connection,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        incremental: bool = False,
    ) -> Dict[str, int]:
        """Recompute hierarchy depths for tasks in the database.

        If ``incremental`` is True, only tasks missing a stored hierarchy depth are targeted.
        """

        total = connection.execute("SELECT COUNT(*) AS count FROM tasks").fetchone()["count"]
        if total == 0:
            if progress_callback:
                progress_callback(0, 0)
            return {"total": 0, "known_depth": 0, "unknown_depth": 0}

        missing_rows = connection.execute("SELECT gid FROM tasks WHERE hierarchy_depth IS NULL").fetchall()
        missing_set: Set[str] = {row["gid"] for row in missing_rows if row["gid"]}
        target_total = len(missing_set) if incremental else total

        visited: Set[str] = set()
        processed_count = 0

        def notify_progress() -> None:
            if progress_callback:
                progress_callback(processed_count, target_total)

        if not incremental:
            roots = connection.execute("SELECT gid FROM tasks WHERE parent_gid IS NULL").fetchall()
            for row in roots:
                gid = row["gid"]
                if not gid:
                    continue
                visited.update(self._recompute_subtree_depths(connection, gid))
                processed_count = len(visited)
                connection.commit()
                notify_progress()

        remaining = missing_rows if incremental else connection.execute(
            "SELECT gid FROM tasks WHERE hierarchy_depth IS NULL"
        ).fetchall()
        for row in remaining:
            gid = row["gid"]
            if not gid or gid in visited:
                continue
            subtree_nodes = self._recompute_subtree_depths(connection, gid)
            visited.update(subtree_nodes)
            if incremental:
                newly_processed = missing_set.intersection(subtree_nodes)
                if newly_processed:
                    processed_count += len(newly_processed)
                    missing_set.difference_update(newly_processed)
            else:
                processed_count = len(visited)
            connection.commit()
            notify_progress()

        known = connection.execute(
            "SELECT COUNT(*) AS count FROM tasks WHERE hierarchy_depth IS NOT NULL"
        ).fetchone()["count"]
        unknown = total - known
        notify_progress()
        return {"total": total, "known_depth": known, "unknown_depth": unknown}

    def _update_single_task_depth(self, connection: sqlite3.Connection, task_gid: str) -> None:
        depth = self._compute_depth_to_root(connection, task_gid)
        connection.execute("UPDATE tasks SET hierarchy_depth = ? WHERE gid = ?", (depth, task_gid))

    def _recompute_subtree_depths(self, connection: sqlite3.Connection, root_gid: str) -> Set[str]:
        depth = self._compute_depth_to_root(connection, root_gid)
        connection.execute("UPDATE tasks SET hierarchy_depth = ? WHERE gid = ?", (depth, root_gid))
        descendants = connection.execute(
            """
            WITH RECURSIVE subtree(gid, depth_offset, path) AS (
                SELECT t.gid, 0 AS depth_offset, '>' || t.gid || '>' AS path
                FROM tasks AS t
                WHERE t.gid = ?
                UNION ALL
                SELECT child.gid, subtree.depth_offset + 1, subtree.path || child.gid || '>'
                FROM tasks AS child
                JOIN subtree ON child.parent_gid = subtree.gid
                WHERE instr(subtree.path, '>' || child.gid || '>') = 0
            )
            SELECT gid, depth_offset FROM subtree WHERE gid != ?
            """,
            (root_gid, root_gid),
        ).fetchall()

        visited: Set[str] = {root_gid}
        if descendants:
            if depth is None:
                updates = [(None, row["gid"]) for row in descendants]
            else:
                updates = [(depth + row["depth_offset"], row["gid"]) for row in descendants]
            connection.executemany("UPDATE tasks SET hierarchy_depth = ? WHERE gid = ?", updates)
            visited.update(row["gid"] for row in descendants)
        return visited

    def _compute_depth_to_root(self, connection: sqlite3.Connection, task_gid: str) -> Optional[int]:
        row = connection.execute(
            """
            WITH RECURSIVE ancestors(gid, parent_gid, depth, path) AS (
                SELECT t.gid, t.parent_gid, 0 AS depth, '>' || t.gid || '>' AS path
                FROM tasks AS t
                WHERE t.gid = ?
                UNION ALL
                SELECT parent.gid, parent.parent_gid, ancestors.depth + 1,
                       ancestors.path || parent.gid || '>'
                FROM tasks AS parent
                JOIN ancestors ON parent.gid = ancestors.parent_gid
                WHERE instr(ancestors.path, '>' || parent.gid || '>') = 0
            )
            SELECT gid, parent_gid, depth
            FROM ancestors
            ORDER BY depth DESC
            LIMIT 1
            """,
            (task_gid,),
        ).fetchone()
        if row is None:
            return None
        if row["parent_gid"] is None:
            return row["depth"]
        return None

    def rebuild_flattened_task_hierarchy(
        self,
        connection: sqlite3.Connection,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> Dict[str, int]:
        """Recompute the flattened hierarchy table used for Jira exports."""

        rows = connection.execute(
            "SELECT gid, parent_gid, hierarchy_depth FROM tasks ORDER BY gid"
        ).fetchall()
        total = len(rows)
        if total == 0:
            connection.execute("DELETE FROM task_hierarchy_flattened")
            connection.commit()
            if progress_callback:
                progress_callback(0, 0)
            return {"total": 0, "flattened": 0, "unresolved": 0}

        parent_lookup = {row["gid"]: row["parent_gid"] for row in rows if row["gid"]}
        depth_lookup = {row["gid"]: row["hierarchy_depth"] for row in rows if row["gid"]}

        flattened_count = 0
        unresolved_count = 0
        normalized_at = datetime.now(timezone.utc).isoformat()

        payloads = []
        for index, row in enumerate(rows, start=1):
            task_gid = row["gid"]
            if not task_gid:
                continue
            flattened_parent, flattened_depth, was_flattened, unresolved = self._resolve_flattened_parent_entry(
                task_gid,
                parent_lookup,
                depth_lookup,
            )
            if was_flattened:
                flattened_count += 1
            if unresolved:
                unresolved_count += 1
            payloads.append(
                {
                    "task_gid": task_gid,
                    "flattened_parent_gid": flattened_parent,
                    "flattened_depth": flattened_depth,
                    "normalized_at": normalized_at,
                }
            )
            if progress_callback:
                progress_callback(index, total)

        connection.execute("DELETE FROM task_hierarchy_flattened")
        if payloads:
            connection.executemany(
                """
                INSERT INTO task_hierarchy_flattened (
                    task_gid,
                    flattened_parent_gid,
                    flattened_depth,
                    normalized_at
                ) VALUES (
                    :task_gid,
                    :flattened_parent_gid,
                    :flattened_depth,
                    :normalized_at
                )
                """,
                payloads,
            )
        connection.commit()
        if progress_callback:
            progress_callback(total, total)
        return {"total": total, "flattened": flattened_count, "unresolved": unresolved_count}

    def _resolve_flattened_parent_entry(
        self,
        task_gid: str,
        parent_lookup: Dict[str, Optional[str]],
        depth_lookup: Dict[str, Optional[int]],
    ) -> Tuple[Optional[str], Optional[int], bool, bool]:
        """Return flattened parent info for a task."""

        depth = depth_lookup.get(task_gid)
        parent_gid = parent_lookup.get(task_gid)
        if depth is None:
            return parent_gid, None, False, False
        if depth == 0:
            return None, 0, False, False
        if depth == 1:
            return parent_gid, 1, False, False

        visited: Set[str] = {task_gid}
        ancestor_gid = parent_gid
        while ancestor_gid and ancestor_gid not in visited:
            visited.add(ancestor_gid)
            ancestor_depth = depth_lookup.get(ancestor_gid)
            if ancestor_depth is None:
                break
            if ancestor_depth == 0:
                return ancestor_gid, 1, True, False
            ancestor_gid = parent_lookup.get(ancestor_gid)
        return None, 1, True, True

    def _ensure_column(self, connection: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        info = connection.execute(f"PRAGMA table_info({table})").fetchall()
        if column not in [row[1] for row in info]:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            connection.commit()

    def _ensure_indexes(self, connection: sqlite3.Connection) -> None:
        self._ensure_index(connection, "idx_task_projects_task_gid", "task_projects", "task_gid")
        self._ensure_index(connection, "idx_task_projects_project_gid", "task_projects", "project_gid")
        self._ensure_index(connection, "idx_task_custom_fields_task_gid", "task_custom_fields", "task_gid")
        self._ensure_index(connection, "idx_task_custom_fields_field_gid", "task_custom_fields", "custom_field_gid")
        self._ensure_index(
            connection,
            "idx_task_custom_field_multi_enum_task_gid",
            "task_custom_field_multi_enum",
            "task_gid",
        )
        self._ensure_index(connection, "idx_task_relationships_task_gid", "task_relationships", "task_gid")
        self._ensure_index(connection, "idx_task_followers_task_gid", "task_followers", "task_gid")
        self._ensure_index(connection, "idx_task_comments_task_gid", "task_comments", "task_gid")
        self._ensure_index(connection, "idx_task_attachments_task_gid", "task_attachments", "task_gid")
        self._ensure_index(connection, "idx_task_tags_task_gid", "task_tags", "task_gid")
        self._ensure_index(
            connection,
            "idx_task_hierarchy_flattened_parent_gid",
            "task_hierarchy_flattened",
            "flattened_parent_gid",
        )
        self._ensure_index(
            connection,
            "idx_task_jira_mappings_task_gid",
            "task_jira_mappings",
            "task_gid",
        )
        self._ensure_index(
            connection,
            "idx_task_jira_mappings_jira_key",
            "task_jira_mappings",
            "jira_key",
        )
        self._ensure_index(
            connection,
            "idx_exported_tasks_project_gid",
            "exported_tasks",
            "project_gid",
        )

    @staticmethod
    def _ensure_index(connection: sqlite3.Connection, name: str, table: str, columns: str) -> None:
        connection.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({columns})")
        connection.commit()

    def _ensure_views(self, connection: sqlite3.Connection) -> None:
        connection.execute("DROP VIEW IF EXISTS project_task_hierarchy_counts")
        connection.execute("DROP VIEW IF EXISTS view_project_task_hierarchy_counts")
        connection.execute("DROP VIEW IF EXISTS project_custom_field_configurations")
        connection.execute("DROP VIEW IF EXISTS view_project_custom_field_configurations")
        connection.execute("DROP VIEW IF EXISTS team_memberships")
        connection.execute("DROP VIEW IF EXISTS view_team_memberships")
        connection.executescript(VIEW_PROJECT_TASK_HIERARCHY_COUNTS)
        connection.executescript(VIEW_PROJECT_CUSTOM_FIELD_CONFIGURATIONS)
        connection.executescript(VIEW_TEAM_MEMBERSHIPS)

    def _ensure_task_jira_mapping_schema(self, connection: sqlite3.Connection) -> None:
        info = connection.execute("PRAGMA table_info(task_jira_mappings)").fetchall()
        if not info:
            return
        columns = {row[1] for row in info}
        expected = {"task_gid", "jira_key", "synced_at"}
        if columns != expected:
            logger.warning("Resetting task_jira_mappings schema to the latest format.")
            connection.execute("DROP TABLE IF EXISTS task_jira_mappings")
            connection.executescript(TASK_JIRA_MAPPINGS_SCHEMA)
        connection.commit()

    def get_personal_task_cursor(self, connection: sqlite3.Connection, assignee_gid: str) -> Optional[str]:
        row = connection.execute(
            """
            SELECT last_modified_at
            FROM personal_task_cursors
            WHERE assignee_gid = ?
            """,
            (assignee_gid,),
        ).fetchone()
        if row:
            return row["last_modified_at"]
        return None

    def upsert_personal_task_cursor(
        self,
        connection: sqlite3.Connection,
        assignee_gid: str,
        last_modified_at: Optional[str],
        synced_at: datetime,
    ) -> None:
        connection.execute(
            """
            INSERT INTO personal_task_cursors (assignee_gid, last_modified_at, synced_at)
            VALUES (?, ?, ?)
            ON CONFLICT(assignee_gid) DO UPDATE SET
                last_modified_at = excluded.last_modified_at,
                synced_at = excluded.synced_at
            """,
            (assignee_gid, last_modified_at, synced_at.isoformat()),
        )

    def get_exported_task_gids(self, connection: sqlite3.Connection, export_id: str) -> Set[str]:
        if not export_id:
            return set()
        rows = connection.execute(
            """
            SELECT task_gid
            FROM exported_tasks
            WHERE export_id = ?
            """,
            (export_id,),
        ).fetchall()
        return {row["task_gid"] for row in rows if row["task_gid"]}

    def get_exported_task_projects(self, connection: sqlite3.Connection, export_id: str) -> Dict[str, str]:
        if not export_id:
            return {}
        rows = connection.execute(
            """
            SELECT task_gid, project_gid
            FROM exported_tasks
            WHERE export_id = ?
            """,
            (export_id,),
        ).fetchall()
        return {row["task_gid"]: row["project_gid"] for row in rows if row["task_gid"]}

    def record_exported_tasks(
        self,
        connection: sqlite3.Connection,
        export_id: str,
        task_gids: Iterable[str],
        project_gid: Optional[str],
        exported_at: datetime,
    ) -> int:
        if not export_id:
            return 0
        existing = self.get_exported_task_gids(connection, export_id)
        to_insert = [
            {
                "export_id": export_id,
                "task_gid": gid,
                "project_gid": project_gid,
                "exported_at": exported_at.isoformat(),
            }
            for gid in task_gids
            if gid and gid not in existing
        ]
        if not to_insert:
            return 0
        connection.executemany(
            """
            INSERT OR IGNORE INTO exported_tasks (export_id, task_gid, project_gid, exported_at)
            VALUES (:export_id, :task_gid, :project_gid, :exported_at)
            """,
            to_insert,
        )
        connection.commit()
        return len(to_insert)

    def clear_export_history(self, connection: sqlite3.Connection) -> int:
        deleted = connection.execute("DELETE FROM exported_tasks").rowcount
        connection.commit()
        return deleted

    def clear_export_history_for_id(self, connection: sqlite3.Connection, export_id: str) -> int:
        deleted = connection.execute(
            "DELETE FROM exported_tasks WHERE export_id = ?", (export_id,)
        ).rowcount
        connection.commit()
        return deleted

    def upsert_jira_project_mappings(
        self,
        connection: sqlite3.Connection,
        mappings: Iterable[Dict[str, str]],
        synced_at: datetime,
    ) -> int:
        rows = []
        stamp = synced_at.isoformat()
        for mapping in mappings:
            gid = (mapping.get("project_gid") or "").strip()
            name = (mapping.get("project_name") or "").strip()
            key = (mapping.get("project_key") or "").strip()
            if not gid:
                continue
            rows.append((gid, name, key, stamp))
        if not rows:
            return 0
        connection.executemany(
            """
            INSERT INTO jira_project_mappings (project_gid, jira_project_name, jira_project_key, synced_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(project_gid) DO UPDATE SET
                jira_project_name=excluded.jira_project_name,
                jira_project_key=excluded.jira_project_key,
                synced_at=excluded.synced_at
            """,
            rows,
        )
        connection.commit()
        return len(rows)

    def load_jira_project_mappings(self, connection: sqlite3.Connection) -> Dict[str, Dict[str, str]]:
        rows = connection.execute(
            "SELECT project_gid, jira_project_name, jira_project_key FROM jira_project_mappings"
        ).fetchall()
        return {
            row["project_gid"]: {
                "jira_project_name": row["jira_project_name"] or "",
                "jira_project_key": row["jira_project_key"] or "",
            }
            for row in rows
            if row["project_gid"]
        }

    def clear_jira_project_mappings(self, connection: sqlite3.Connection) -> int:
        deleted = connection.execute("DELETE FROM jira_project_mappings").rowcount
        connection.commit()
        return deleted

    def replace_task_comments(
        self,
        connection: sqlite3.Connection,
        task_gid: Optional[str],
        comments: Iterable[Dict[str, Any]],
        synced_at: datetime,
    ) -> Dict[str, int]:
        if not task_gid:
            return {"analyzed": 0, "created": 0, "updated": 0, "deleted": 0}

        existing_rows = connection.execute(
            "SELECT gid, is_deleted FROM task_comments WHERE task_gid = ?",
            (task_gid,),
        ).fetchall()
        existing_gids = {row["gid"] for row in existing_rows}
        connection.execute(
            "UPDATE task_comments SET is_deleted = 1 WHERE task_gid = ?",
            (task_gid,),
        )
        rows = []
        created = 0
        updated = 0
        new_gids: Set[str] = set()
        for comment in comments:
            if not isinstance(comment, dict):
                continue
            comment_gid = comment.get("gid")
            if not comment_gid:
                continue
            created_by = comment.get("created_by") or {}
            if comment_gid in existing_gids:
                updated += 1
            else:
                created += 1
            new_gids.add(comment_gid)
            rows.append(
                {
                    "gid": comment_gid,
                    "task_gid": task_gid,
                    "created_at": comment.get("created_at"),
                    "created_by_gid": created_by.get("gid"),
                    "text": comment.get("text"),
                    "html_text": comment.get("html_text"),
                    "is_edited": _bool_to_int(comment.get("is_edited")),
                    "is_deleted": 0,
                    "synced_at": synced_at.isoformat(),
                }
            )
        if rows:
            connection.executemany(
                """
                INSERT INTO task_comments (
                    gid, task_gid, created_at, created_by_gid, text,
                    html_text, is_edited, is_deleted, synced_at
                )
                VALUES (
                    :gid, :task_gid, :created_at, :created_by_gid, :text,
                    :html_text, :is_edited, :is_deleted, :synced_at
                )
                ON CONFLICT(gid) DO UPDATE SET
                    task_gid=excluded.task_gid,
                    created_at=excluded.created_at,
                    created_by_gid=excluded.created_by_gid,
                    text=excluded.text,
                    html_text=excluded.html_text,
                    is_edited=excluded.is_edited,
                    is_deleted=excluded.is_deleted,
                    synced_at=excluded.synced_at
                """,
                rows,
            )
        newly_deleted = len(existing_gids - new_gids)
        return {
            "analyzed": len(comments),
            "created": created,
            "updated": updated,
            "deleted": newly_deleted,
        }

    def replace_task_attachments(
        self,
        connection: sqlite3.Connection,
        task_gid: Optional[str],
        attachments: Iterable[Dict[str, Any]],
        synced_at: datetime,
    ) -> Dict[str, int]:
        if not task_gid:
            return {"analyzed": 0, "created": 0, "updated": 0, "deleted": 0}
        existing_rows = connection.execute(
            "SELECT gid, is_deleted FROM task_attachments WHERE task_gid = ?",
            (task_gid,),
        ).fetchall()
        existing_gids = {row["gid"] for row in existing_rows}
        connection.execute(
            "UPDATE task_attachments SET is_deleted = 1 WHERE task_gid = ?",
            (task_gid,),
        )
        rows = []
        created = 0
        updated = 0
        new_gids: Set[str] = set()
        for attachment in attachments:
            if not isinstance(attachment, dict):
                continue
            attachment_gid = attachment.get("gid")
            if not attachment_gid:
                continue
            parent = attachment.get("parent") or {}
            created_by = attachment.get("created_by") or {}
            if attachment_gid in existing_gids:
                updated += 1
            else:
                created += 1
            new_gids.add(attachment_gid)
            rows.append(
                {
                    "gid": attachment_gid,
                    "task_gid": task_gid,
                    "parent_gid": parent.get("gid"),
                    "parent_resource_type": parent.get("resource_type"),
                    "name": attachment.get("name"),
                    "resource_subtype": attachment.get("resource_subtype"),
                    "host": attachment.get("host"),
                    "service": attachment.get("service"),
                    "size": attachment.get("size"),
                    "created_at": attachment.get("created_at"),
                    "created_by_gid": created_by.get("gid"),
                    "permanent_url": attachment.get("permanent_url"),
                    "view_url": attachment.get("view_url"),
                    "is_deleted": 0,
                    "synced_at": synced_at.isoformat(),
                }
            )
        if rows:
            connection.executemany(
                """
                INSERT INTO task_attachments (
                    gid, task_gid, parent_gid, parent_resource_type, name,
                    resource_subtype, host, service, size, created_at,
                    created_by_gid, permanent_url, view_url, is_deleted, synced_at
                )
                VALUES (
                    :gid, :task_gid, :parent_gid, :parent_resource_type, :name,
                    :resource_subtype, :host, :service, :size, :created_at,
                    :created_by_gid, :permanent_url, :view_url, :is_deleted, :synced_at
                )
                ON CONFLICT(gid) DO UPDATE SET
                    task_gid=excluded.task_gid,
                    parent_gid=excluded.parent_gid,
                    parent_resource_type=excluded.parent_resource_type,
                    name=excluded.name,
                    resource_subtype=excluded.resource_subtype,
                    host=excluded.host,
                    service=excluded.service,
                    size=excluded.size,
                    created_at=excluded.created_at,
                    created_by_gid=excluded.created_by_gid,
                    permanent_url=excluded.permanent_url,
                    view_url=excluded.view_url,
                    is_deleted=excluded.is_deleted,
                    synced_at=excluded.synced_at
                """,
                rows,
            )
        newly_deleted = len(existing_gids - new_gids)
        return {
            "analyzed": len(attachments),
            "created": created,
            "updated": updated,
            "deleted": newly_deleted,
        }

    def upsert_task_jira_mappings(
        self,
        connection: sqlite3.Connection,
        mappings: Iterable[Dict[str, str]],
        synced_at: datetime,
    ) -> Dict[str, int]:
        entries = []
        jira_keys: List[str] = []
        seen_pairs: Set[Tuple[str, str]] = set()
        timestamp = synced_at.isoformat()
        for mapping in mappings:
            task_gid = (mapping.get("task_gid") or "").strip()
            jira_key = (mapping.get("jira_key") or "").strip()
            if not task_gid or not jira_key:
                continue
            pair = (task_gid, jira_key)
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            entries.append(
                {
                    "task_gid": task_gid,
                    "jira_key": jira_key,
                    "synced_at": timestamp,
                }
            )
            jira_keys.append(jira_key)

        if jira_keys:
            placeholders = ",".join("?" for _ in jira_keys)
            conflicts = connection.execute(
                f"""
                SELECT jira_key, task_gid
                FROM task_jira_mappings
                WHERE jira_key IN ({placeholders})
                """,
                tuple(jira_keys),
            ).fetchall()
            conflict_map = {row["jira_key"]: row["task_gid"] for row in conflicts}
            for entry in entries:
                existing_task = conflict_map.get(entry["jira_key"])
                if existing_task and existing_task != entry["task_gid"]:
                    raise ValueError(
                        "Jira key '%s' already mapped to task %s"
                        % (entry["jira_key"], existing_task)
                    )

        if entries:
            connection.executemany(
                """
                INSERT INTO task_jira_mappings (
                    task_gid, jira_key, synced_at
                )
                VALUES (
                    :task_gid, :jira_key, :synced_at
                )
                ON CONFLICT(task_gid, jira_key) DO UPDATE SET
                    synced_at=excluded.synced_at
                """,
                entries,
            )
        connection.commit()
        return {"written": len(entries)}

    def _replace_project_custom_field_settings(
        self,
        connection: sqlite3.Connection,
        project_gid: Optional[str],
        settings: Iterable[Dict[str, Any]],
        synced_at: datetime,
    ) -> None:
        if not project_gid:
            return
        connection.execute("DELETE FROM project_custom_field_settings WHERE project_gid = ?", (project_gid,))
        rows = []
        for setting in settings:
            if not isinstance(setting, dict):
                continue
            custom_field = setting.get("custom_field") or {}
            custom_field_gid = custom_field.get("gid")
            if not custom_field_gid:
                continue
            rows.append(
                {
                    "project_gid": project_gid,
                    "custom_field_gid": custom_field_gid,
                    "custom_field_name": custom_field.get("name"),
                    "custom_field_type": custom_field.get("type"),
                    "is_important": _bool_to_int(setting.get("is_important")),
                    "is_global_to_workspace": _bool_to_int(custom_field.get("is_global_to_workspace")),
                    "synced_at": synced_at.isoformat(),
                }
            )
        if rows:
            connection.executemany(
                """
                INSERT INTO project_custom_field_settings (
                    project_gid, custom_field_gid, custom_field_name,
                    custom_field_type, is_important, is_global_to_workspace, synced_at
                )
                VALUES (
                    :project_gid, :custom_field_gid, :custom_field_name,
                    :custom_field_type, :is_important, :is_global_to_workspace, :synced_at
                )
                """,
                rows,
            )


def synced_at_timestamp(override: Optional[datetime] = None) -> datetime:
    """Return a timezone-aware timestamp for the current sync."""
    if override:
        return override.astimezone(timezone.utc)
    return datetime.now(tz=timezone.utc)


def _bool_to_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    return 1 if bool(value) else 0


def _extract_organization_gid(team: Dict[str, Any]) -> Optional[str]:
    organization = team.get("organization") or team.get("workspace") or {}
    if isinstance(organization, dict):
        gid = organization.get("gid")
        if gid:
            return gid
    fallback_keys = ("organization_gid", "workspace_gid")
    for key in fallback_keys:
        gid = team.get(key)
        if gid:
            return gid
    return None
