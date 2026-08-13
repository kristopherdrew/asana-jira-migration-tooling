# Phase 2: Discover & Refresh Asana

## Purpose

Build a reliable local snapshot of the Asana workspace. Reports and migration
exports read from SQLite, so their quality depends on completing the appropriate
sync layers in the correct order.

## Recommended Flow

For a new client or a comprehensive refresh, use **Guided full Asana refresh**. It
runs workspace metadata first, then tasks, and then offers task-derived resources.
Use individual actions for focused reruns or use the incremental refresh after the
baseline exists.

## Menu Actions and Scripts

### Guided full Asana refresh

Orchestrates the metadata and task actions below. It then offers task extras,
project status updates, portfolios, and personal tasks. This is the preferred
starting point because it preserves dependencies.

### Refresh workspace metadata

Runs these scripts in order:

1. `scripts.get_teams` captures teams and memberships.
2. `scripts.get_users` captures users and workspace membership.
3. `scripts.atlassian.add_service_user_to_teams` ensures the service user can see
   team-scoped data.
4. `scripts.get_projects` captures projects and task-count snapshots.
5. `scripts.get_custom_fields` captures workspace custom-field definitions.

### Sync project tasks

Runs `scripts.get_tasks` for a selected team, project list, or all teams. It captures
tasks, subtasks, memberships, relationships, followers, tags, and field values. If a
project was expected to contain tasks but returned none, the flow can run
`scripts.atlassian.add_service_user_to_projects` and retry the affected projects.

### Sync task comments and attachment metadata

Runs `scripts.get_task_extras` after tasks exist. It captures task comments and
attachment metadata; it does not download attachment bytes.

### Sync project status updates

Runs `scripts.get_project_status_updates` to capture project-level updates and their
comments separately from task comments.

### Sync portfolios

Runs `scripts.get_portfolios` to capture portfolios, nested portfolios, items,
memberships, custom-field settings, and status updates.

### Sync personal tasks

Runs `scripts.get_personal_tasks` for one user or all users. Personal tasks are
represented through synthetic personal projects in the local data model.

### Run incremental Asana refresh

Runs `scripts.get_incremental_updates` to refresh recently modified project and
personal tasks. Use this after the baseline sync; it is not a substitute for missing
workspace metadata.

## Expected Result

The active profile's SQLite database contains current metadata and the task layers
needed for the intended reports or migration scope. Review warnings about missing
service-user access before treating zero-task projects as empty.

## Detailed Script Reference

See [Core Sync Commands](../sync-commands.md) for flags, scope modes, API behavior,
and standalone examples for every script in this phase.
