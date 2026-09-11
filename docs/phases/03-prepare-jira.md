# Phase 3: Prepare Jira

## Purpose

Prepare Jira identities and project-level hierarchy before migrating task-level
work. This phase creates or records the destinations that later migration waves use.

The interactive workflow uses the direct API to preview or create the Team-to-Epic
and Project-to-Task hierarchy. The legacy hierarchy CSV export remains available
under **Advanced & Recovery** for exceptional recovery work.

## Menu Actions and Scripts

### Create Jira fields for task-import mappings

Runs `scripts.atlassian.create_jira_import_fields` for the task-import field set.
Jira administrator permission is required.

### Create Jira fields for Team/Project tracking

Runs `scripts.atlassian.create_jira_import_fields` for the Team/Project tracking
field set used by the direct-API hierarchy flow.

### Update Atlassian user mappings

Runs `scripts.atlassian.update_atlassian_user_map`. It maps Asana users to Atlassian
account IDs so Jira assignee and owner fields can be populated correctly. The guided
CLI asks for a raw Atlassian user export and matches its users to Asana users by email.

### Import/update Jira project associations

Runs `scripts.atlassian.import_jira_project_map` for a straight CSV import, or the
guided overwrite flow for a reviewed CSV scope. Use it to load existing Jira project
keys, or to overwrite selected Asana project GIDs with one validated Jira project key
after a Jira project was recreated or a migration scope changed.

### Preview or create Team/Project Jira hierarchy (API)

Runs `scripts.atlassian.create_team_project_jira_issues`. Start with dry-run output,
confirm field resolution and scope, and only then create issues in the intended Jira
environment. Successful creation persists mappings directly.

### Set project Last working day in Jira

Runs `scripts.atlassian.set_project_last_updated_jira_field`. It writes each
project's most recent Asana task-update date to its mapped Jira project issue. Use a
dry run before writes, especially in Production.

## Expected Result

Users have the required Atlassian mappings, each in-scope Asana project has a known
Jira project-level destination, and the hierarchy mappings are recorded consistently
in SQLite.

## Detailed Script Reference

See [Atlassian / Jira Integration](../jira-integration.md) for standalone commands,
input formats, field mapping, dry-run behavior, and recovery flags.
