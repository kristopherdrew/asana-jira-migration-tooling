"""Single source of truth for what counts as an *active* Atlassian account.

The owner/admin/assignee fallback logic across the Team→Epic / Project→Task
creation path all needs to agree on when a mapped user is still usable in Jira.
Historically each module carried its own copy of the predicate; route every
caller through this helper so the definition stays unified: a blank, unknown, or
any non-"active" status is treated as NOT active, so the caller falls back to
another candidate.
"""

from __future__ import annotations

from typing import Optional


def is_active_atlassian_status(status: Optional[str]) -> bool:
    """Return True only when the Atlassian status is exactly "active".

    The comparison is whitespace- and case-insensitive. Empty or unknown
    statuses are inactive by design.
    """
    return (status or "").strip().casefold() == "active"
