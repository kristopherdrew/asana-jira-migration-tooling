"""Resolve parent Jira keys and prepare Phase 2 task export with parent linking."""

from __future__ import annotations

import logging
import sqlite3
from typing import Dict, List, Optional

from src.teams.epic_mappings import resolve_parent_epic_keys_batch

logger = logging.getLogger(__name__)


def resolve_task_parents(
    connection: sqlite3.Connection,
    task_rows: List[Dict],
    path: str = "hierarchical",
    jira_site_key: Optional[str] = None,
) -> List[Dict]:
    """Resolve parent Jira keys for tasks and inject into jira_parent_gid field.

    For PATH 1 (simple): parent = project Jira key
    For PATH 2 (hierarchical): parent = section task_gid (will be resolved after section import)

    Args:
        task_rows: List of task dicts from export
        path: "simple" or "hierarchical"
        jira_site_key: Jira site key for lookups

    Returns:
        Updated task_rows with jira_parent_gid populated
    """
    logger.info("Resolving parent keys for %d task(s) (path=%s)", len(task_rows), path)

    # Group tasks by project_gid to batch lookup parent keys
    tasks_by_project = {}
    for task in task_rows:
        project_gid = task.get("_project_gid")
        if not project_gid:
            logger.warning("Task %s has no project_gid", task.get("task_gid", "?"))
            continue

        if project_gid not in tasks_by_project:
            tasks_by_project[project_gid] = []
        tasks_by_project[project_gid].append(task)

    # Batch lookup parent Jira keys from Phase 1 captures
    project_gids = list(tasks_by_project.keys())
    captured_parent_keys = resolve_parent_epic_keys_batch(
        connection, project_gids, jira_site_key
    )

    logger.info("Found parent keys for %d/%d projects", len(captured_parent_keys), len(project_gids))

    # Inject parent keys into task rows
    resolved_count = 0
    orphan_count = 0

    for task in task_rows:
        project_gid = task.get("_project_gid")
        if not project_gid:
            continue

        parent_key = captured_parent_keys.get(project_gid)
        if parent_key:
            task["jira_parent_gid"] = parent_key
            resolved_count += 1
        else:
            logger.debug("No parent key for project %s (task %s)", project_gid, task.get("task_gid"))
            orphan_count += 1

    logger.info("Resolved %d task parent(s), %d orphan(s)", resolved_count, orphan_count)
    return task_rows


def add_section_epic_rows(
    connection: sqlite3.Connection,
    project_gids: List[str],
    task_rows: List[Dict],
    path: str,
    jira_site_key: Optional[str] = None,
) -> tuple[List[Dict], List[Dict]]:
    """For hierarchical path, build section Epic rows and inject into task export.

    Args:
        project_gids: Projects to export
        task_rows: Existing task rows
        path: "simple" or "hierarchical"

    Returns:
        (section_rows, updated_task_rows) for PATH 2, or ([], task_rows) for PATH 1
    """
    if path == "simple":
        return [], task_rows

    logger.info("Building section Epic rows for %d project(s)", len(project_gids))

    # Get parent keys for projects (to link sections to projects)
    parent_keys = resolve_parent_epic_keys_batch(connection, project_gids, jira_site_key)

    # Fetch sections for each project
    section_rows = []
    for project_gid in project_gids:
        sections = connection.execute(
            """
            SELECT DISTINCT section_name
            FROM task_projects tp
            JOIN tasks t ON t.gid = tp.task_gid
            WHERE tp.project_gid = ? AND tp.section_name IS NOT NULL
            ORDER BY section_name
            """,
            (project_gid,),
        ).fetchall()

        parent_key = parent_keys.get(project_gid)
        if not parent_key:
            logger.debug("No parent key for project %s, skipping sections", project_gid)
            continue

        for section in sections:
            section_name = section["section_name"]
            section_gid = f"{project_gid}::above_epic_section::{section_name}"

            # Build synthetic section row
            section_row = {
                "task_gid": section_gid,
                "External issue ID": section_gid,
                "task_name": section_name,
                "Issue Type": "Epic",
                "jira_parent_gid": parent_key,  # Parent = project Jira key
                "_project_gid": project_gid,
                "_is_section": True,
            }
            section_rows.append(section_row)
            logger.debug("Built section Epic: %s (parent=%s)", section_name, parent_key)

    # Update task rows to reference section parent (by section GID)
    for task in task_rows:
        project_gid = task.get("_project_gid")
        section_name = task.get("Section")
        if project_gid and section_name:
            # Parent = section's task_gid
            section_gid = f"{project_gid}::above_epic_section::{section_name}"
            task["jira_parent_gid"] = section_gid
            logger.debug("Task %s parent → section %s", task.get("task_gid"), section_gid)

    logger.info("Built %d section Epic row(s)", len(section_rows))
    return section_rows, task_rows


def build_phase_2_rows(
    connection: sqlite3.Connection,
    task_rows: List[Dict],
    path: str = "hierarchical",
    jira_site_key: Optional[str] = None,
) -> tuple[List[Dict], List[Dict]]:
    """Build complete Phase 2 export rows: sections + tasks with parent keys.

    Args:
        task_rows: Task rows from export
        path: "simple" (task-only) or "hierarchical" (section + task)

    Returns:
        (section_rows, final_task_rows) where section_rows is empty for PATH 1
    """
    # Resolve task parent keys first
    resolved_tasks = resolve_task_parents(connection, task_rows, path, jira_site_key)

    # For hierarchical path, build section Epics
    if path == "hierarchical":
        project_gids = list(set(t.get("_project_gid") for t in resolved_tasks if t.get("_project_gid")))
        section_rows, final_tasks = add_section_epic_rows(
            connection, project_gids, resolved_tasks, path, jira_site_key
        )
        return section_rows, final_tasks

    return [], resolved_tasks
