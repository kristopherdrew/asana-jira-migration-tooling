# Phase 7: Advanced & Recovery

## Purpose

Perform exceptional recovery and state-management operations that fall outside the
normal lifecycle. Most actions here alter saved migration state and can cause data to
be recreated or re-imported on later runs.

## Safety Checks

Before a reset, confirm the active client, database, Jira environment, and intended
scope. Export or inspect the affected mappings when recovery depends on being able to
reconstruct them. The CLI asks for confirmation, but that prompt does not replace an
operator recovery plan.

## Menu Actions and Scripts

### Clear Jira task-key mappings

Runs `scripts.atlassian.clear_jira_task_keys`. It removes stored task-to-Jira key
mappings for the selected scope or for all tasks. It does not delete Jira issues.

### Clear Jira project associations

Runs `scripts.atlassian.export_project_to_jira_csv --clear-jira-project-map`. It
removes saved project associations used by Jira exports. Confirm how those mappings
will be re-imported before clearing them.

### Clear export-ID history

Runs `scripts.atlassian.export_project_to_jira_csv --clear-export-ids`. It removes
the history used to skip tasks already recorded under export IDs; later exports may
include those tasks again.

### Reset direct-API Team/Project mappings

Runs `scripts.atlassian.create_team_project_jira_issues --reset` for the selected
scope. Clearing these mappings can cause a later creation run to recreate hierarchy
issues that still exist in Jira.

### Migrate tokens from an environment file to macOS Keychain

Moves supported secret tokens into the profile-scoped Keychain accounts while
retaining non-secret configuration in the profile file. This is credential storage
maintenance rather than migration-data recovery.

### Legacy technical menus

The Initialization, Tasks, Atlassian, and Reports menus expose the previous technical
organization during the transition to lifecycle phases. They call the same scripts
as the phase menus and should not be treated as a separate workflow.

## Expected Result

The specific recovery objective is complete, the resulting mapping state is
understood, and the next reconstruction or rerun step is known. Do not clear several
mapping layers together unless the entire migration wave is intentionally being
rebuilt.

## Detailed References

- [Clear Jira Task Keys](../jira-integration.md#clear-jira-task-keys)
- [Export history and project-map resets](../jira-integration.md#export-project-tasks-to-jira-csv)
- [Direct API reset behavior](../jira-integration.md#create-teamproject-jira-issues-direct-api)
- [Requirements and Keychain behavior](../../README.md#requirements)
