# Phase 6: Maintain & Validate

## Purpose

Keep the migration snapshot current and validate saved migration state before,
between, and after migration waves. This phase combines non-mutating readiness checks
with focused refresh and reconciliation actions.

## Menu Actions and Scripts

### Show migration readiness summary

This CLI-native, read-only action displays row counts and available freshness for
Asana projects and tasks, Atlassian user mappings, Team/Project Jira mappings,
migration decisions, task Jira mappings, attachment archives, and Jira uploads. It is
advisory and does not block later actions.

### Run incremental Asana refresh

Runs `scripts.get_incremental_updates` so recently changed Asana work is reflected in
SQLite. Use the same project or team scope that is being validated.

### Pull migration decisions from Jira

Runs `scripts.atlassian.get_jira_migration_decisions` to refresh the local copy of
project-level migration decisions.

### Recover or reconcile Jira task keys by External ID

Runs `scripts.atlassian.search_jira_external_ids`. A dry run shows what mappings were
found before any local upserts are performed.

### Update project Last working day in Jira

Runs `scripts.atlassian.set_project_last_updated_jira_field` to refresh the mapped
project issue's date from current Asana task data.

### Export project last-updated report

Runs `scripts.export_project_last_updated_report` to identify stale, active, or empty
projects and support post-migration review.

## Expected Result

The local snapshot is fresh enough for the intended validation, required mapping
tables are populated, recoverable gaps have been reconciled, and remaining failures
or exceptions are explicit. Treat the readiness summary as evidence, not as a
complete sign-off checklist.

## Detailed Script References

- [Incremental Updates](../sync-commands.md#incremental-updates)
- [Atlassian / Jira Integration](../jira-integration.md)
- [Project Last-Updated Report](../reports.md#project-last-updated-report)
- [Database Query Cookbook](../db-queries.md)
