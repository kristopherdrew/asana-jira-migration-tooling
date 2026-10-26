"""Orchestrate Phase 1 export + Jira import + key capture workflow."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

from src.lib.database import Database
from scripts.export_team_project_epic_jira_csv import export_team_project_epic_jira_csv
from src.teams.jira_key_capture import capture_epic_keys_from_jira
from src.teams.epic_mappings import store_epic_keys

logger = logging.getLogger(__name__)


def run_phase_1_export(
    database_path: str,
    output_dir: Path,
    path: str = "hierarchical",
    jira_site_key: Optional[str] = None,
    workspace_gid: Optional[str] = None,
) -> tuple[Path, int, int]:
    """Execute Phase 1 export: projects/sections to CSV.

    Args:
        path: "simple" (Project→Epic) or "hierarchical" (Project→Issue, Section→Epic)
        jira_site_key: For querying existing mappings (upsert capability)

    Returns:
        (csv_path, project_count, comment_count)
    """
    logger.info("=== PHASE 1 EXPORT ===")
    logger.info("Path: %s | Output: %s", path, output_dir)

    csv_path, project_count, comment_count = export_team_project_epic_jira_csv(
        database_path=database_path,
        output_dir=output_dir,
        path=path,
        workspace_gid=workspace_gid,
        jira_site_key=jira_site_key,
    )

    logger.info("✓ Export complete: %s", csv_path)
    logger.info("  Projects: %d | Comments: %d", project_count, comment_count)
    return csv_path, project_count, comment_count


def run_phase_1_capture_keys(
    database_path: str,
    jira_client,
    jira_project_key: str,
    project_gids: list[str],
    path: str = "hierarchical",
    jira_site_key: Optional[str] = None,
) -> Dict[str, str]:
    """Execute Phase 1 key capture: query Jira for created issue keys.

    Call this AFTER user imports the Phase 1 CSV into Jira.

    Args:
        project_gids: List of Asana project GIDs to look up
        path: Matches the path used in Phase 1 export
        jira_site_key: Site key for storing mappings

    Returns:
        {project_gid: jira_key} for successfully captured projects
    """
    logger.info("=== PHASE 1 KEY CAPTURE ===")
    logger.info("Jira Project: %s | Projects: %d", jira_project_key, len(project_gids))

    # Query Jira for created issues by External ID
    captured_keys = capture_epic_keys_from_jira(
        jira_client=jira_client,
        jira_project_key=jira_project_key,
        project_gids=project_gids,
        path=path,
    )

    if not captured_keys:
        logger.warning("No project keys captured from Jira!")
        return {}

    # Store in database for Phase 2
    database = Database(db_path=database_path)
    with database.connect() as connection:
        database.initialize(connection)
        stored_count = store_epic_keys(connection, captured_keys, jira_site_key)

    logger.info("✓ Captured %d project key(s)", len(captured_keys))
    logger.info("  Stored in database: %d", stored_count)
    return captured_keys


def run_phase_2_export(
    database_path: str,
    output_dir: Path,
    path: str = "hierarchical",
    project_gids: Optional[list[str]] = None,
    jira_site_key: Optional[str] = None,
) -> tuple[Path, int]:
    """Execute Phase 2 export: tasks/sections to CSV with parent keys resolved.

    Args:
        path: Matches the path used in Phase 1
        project_gids: Optional scope (defaults to all projects with Phase 1 mappings)

    Returns:
        (csv_path, row_count)
    """
    from scripts.export_project_to_jira_csv import export_project_to_jira_csv

    logger.info("=== PHASE 2 EXPORT ===")
    logger.info("Path: %s | Output: %s", path, output_dir)

    # For now, delegate to existing export
    # Phase 2-specific logic (sections for hierarchical path) to be added
    csv_path = export_project_to_jira_csv(
        database_path=database_path,
        output_dir=output_dir,
        project_gids=project_gids,
        with_parent_keys=True,
        jira_site_key=jira_site_key,
    )

    logger.info("✓ Phase 2 export complete: %s", csv_path)
    return csv_path, 0  # row_count to be computed
