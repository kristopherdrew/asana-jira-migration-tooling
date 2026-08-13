# Phase 4: Prepare Jira

## Purpose

Prepare Jira identities and project-level hierarchy before migrating task-level
work. This phase creates or records the destinations that later migration waves use.

## Key Decision: CSV or Direct API

Choose one creation path for the Team-to-Epic and Project-to-Task hierarchy:

- The **CSV path** exports a hierarchy file, relies on Jira's external-import
  workflow, and then imports the resulting project associations.
- The **direct API path** previews or creates the Jira hierarchy and persists its
  mappings automatically.

Do not run both paths for the same scope unless you are intentionally reconciling
existing Jira work.

## Menu Actions and Scripts

### Update Atlassian user mappings

Runs `scripts.atlassian.update_atlassian_user_map`. It maps Asana users to Atlassian
account IDs so Jira assignee and owner fields can be populated correctly.

### Export Team -> Epic / Project -> Task CSV

Runs `scripts.atlassian.export_team_project_jira_csv`. Import the resulting file
through Jira, then record the created project associations with the next action.

### Import Jira project associations

Runs `scripts.atlassian.import_jira_project_map`. It loads the Jira keys produced by
the hierarchy CSV import and maps them back to Asana teams and projects in SQLite.

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
Jira project-level destination, and the chosen hierarchy creation path is recorded
consistently in SQLite.

## Detailed Script Reference

See [Atlassian / Jira Integration](../jira-integration.md) for standalone commands,
input formats, field mapping, dry-run behavior, and recovery flags.
