"""Query Jira for imported project issues and capture their keys via External ID field."""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


def capture_epic_keys_from_jira(
    jira_client,
    jira_project_key: str,
    project_gids: List[str],
    path: str = "hierarchical",
) -> Dict[str, str]:
    """Query Jira for imported project issues and extract their keys.

    Queries by External Issue ID to find newly created issues from Phase 1 import.

    Args:
        jira_client: Jira HTTP client (from src.lib.jira_client)
        jira_project_key: Target Jira project key (e.g., 'EST')
        project_gids: List of Asana project GIDs to look up
        path: "simple" or "hierarchical" (affects External ID format)

    Returns:
        Dict mapping {project_gid: jira_key} for found issues

    Raises:
        ValueError: If Jira query fails or keys can't be extracted
    """
    if not project_gids:
        return {}

    result = {}

    # Build External ID list - use plain project GID for query
    # (Jira may store with or without suffix, but query uses base GID)
    external_ids = list(project_gids)

    logger.info("Querying Jira for %d project(s) in project %s", len(project_gids), jira_project_key)

    # Query Jira for issues with matching External IDs
    # JQL: project=EST and "External issue ID" in (gid1, gid2, ...)
    external_id_values = ", ".join(external_ids)
    jql = f'project = {jira_project_key} and "External issue ID" in ({external_id_values})'

    logger.debug("JQL query: %s", jql)

    try:
        response = jira_client.search_issues(jql, max_results=len(project_gids) + 100)
        issues = response.get("issues", [])
    except Exception as e:
        raise ValueError(f"Failed to query Jira for project keys: {e}") from e

    logger.info("Found %d issue(s) in Jira", len(issues))

    # Extract keys and map back to project GIDs
    for issue in issues:
        external_id = issue.get("fields", {}).get("customfield_10025")  # External Issue ID field
        if not external_id:
            logger.warning("Issue %s has no External Issue ID", issue.get("key"))
            continue

        # Strip the suffix (::above_epic or ::above_epic_section::...) to get back to project_gid
        if path == "hierarchical" and "::above_epic" in external_id:
            project_gid = external_id.split("::above_epic")[0]
        else:
            project_gid = external_id

        if project_gid in project_gids:
            jira_key = issue.get("key")
            result[project_gid] = jira_key
            logger.debug("Mapped project %s → Jira key %s", project_gid, jira_key)
        else:
            logger.debug("Ignoring external ID %s (not in requested projects)", external_id)

    # Report orphans
    orphans = set(project_gids) - set(result.keys())
    if orphans:
        logger.warning("Could not find Jira keys for %d project(s): %s", len(orphans), orphans)

    return result


def query_jira_project_keys(
    jira_client,
    jira_project_key: str,
    jira_site_key: Optional[str] = None,
) -> Dict[str, str]:
    """Query Jira for all project issues created during Phase 1 import.

    Returns: {project_gid: jira_key}
    """
    logger.info("Querying Jira project %s for all project/section issues", jira_project_key)

    try:
        # Query for all Deliverable and Epic issues in the project
        # (Deliverable for Phase 2 hierarchical, Epic for simple path or sections)
        jql = f'project = "{jira_project_key}" AND type IN (Deliverable, Epic)'
        response = jira_client.search_issues(jql, max_results=1000)
        issues = response.get("issues", [])
    except Exception as e:
        raise ValueError(f"Failed to query Jira for issues: {e}") from e

    result = {}
    for issue in issues:
        external_id = issue.get("fields", {}).get("customfield_10025")  # External Issue ID field
        if external_id:
            # Remove suffix if present (::above_epic, etc.)
            project_gid = external_id.split("::")[0]
            jira_key = issue.get("key")
            result[project_gid] = jira_key

    logger.info("Found %d project/section issues in Jira", len(result))
    return result
