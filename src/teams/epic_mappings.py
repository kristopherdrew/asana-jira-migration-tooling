"""Store and retrieve project GID → Jira Epic key mappings."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Dict, Optional


def store_epic_keys(
    connection: sqlite3.Connection,
    project_gid_to_jira_key: Dict[str, str],
    jira_site_key: Optional[str] = None,
) -> int:
    """Store a batch of project GID → Jira Epic key mappings.

    Args:
        connection: SQLite database connection
        project_gid_to_jira_key: Dict mapping {project_gid: jira_key}
        jira_site_key: Jira site key (defaults to env var or 'legacy_unknown')

    Returns:
        Number of rows inserted/updated
    """
    from src.lib.database import Database

    db = Database()
    return db.upsert_epic_keys(connection, project_gid_to_jira_key, jira_site_key)


def resolve_parent_epic_key(
    connection: sqlite3.Connection,
    project_gid: str,
    jira_site_key: Optional[str] = None,
) -> Optional[str]:
    """Resolve a project GID to its Jira Epic key.

    Args:
        connection: SQLite database connection
        project_gid: Asana project GID
        jira_site_key: Jira site key (defaults to env var)

    Returns:
        Jira Epic key (e.g., 'PROJ-100') or None if not mapped
    """
    from src.lib.database import Database

    db = Database()
    return db.get_project_epic_key(connection, project_gid, jira_site_key)


def resolve_parent_epic_keys_batch(
    connection: sqlite3.Connection,
    project_gids: list[str],
    jira_site_key: Optional[str] = None,
) -> Dict[str, str]:
    """Resolve multiple project GIDs to their Jira Epic keys.

    Args:
        connection: SQLite database connection
        project_gids: List of Asana project GIDs
        jira_site_key: Jira site key (defaults to env var)

    Returns:
        Dict mapping {project_gid: jira_key} for found mappings
    """
    from src.lib.database import Database

    db = Database()
    return db.get_epic_keys_by_project_gids(connection, project_gids, jira_site_key)
