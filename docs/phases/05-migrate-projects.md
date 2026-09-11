# Phase 5: Migrate Projects

## Purpose

Execute a controlled migration wave for task-level work and attachments. This phase
creates Jira work from Asana tasks, restores Jira keys to the local mapping tables,
and transfers attachment files to their resolved destinations.

## Recommended Flow

Use **Guided project migration** for a normal wave. It keeps CSV import and direct API
creation separate, offers the prerequisite user-map refresh, and guides key import
and attachment upload. Use individual actions when resuming a partially completed
wave or performing a focused repair.

## Menu Actions and Scripts

### Guided project migration

For the CSV path, the workflow can update user mappings and project associations,
runs the active project export, pauses for Jira's external import, and optionally
imports Jira task keys. For the direct API path, it updates mappings and invokes the
Team/Project issue creator. Both paths can finish by uploading a durable attachment
archive.

### Export active project tasks for Jira CSV import

Runs `scripts.atlassian.export_project_to_jira_csv`. It exports task hierarchy,
comments, custom fields, users, and migration identifiers. Multi-project CLI runs
also merge the per-project outputs into a consolidated import file.

### Export archival project tasks for Jira CSV import

Runs `scripts.atlassian.export_archival_project_jira_csv`. This simplified format
folds populated custom fields into the description while retaining native migration
columns and Asana links.

### Manage export IDs

Runs `scripts.manage_export_ids` against the active profile database. Use it to
review existing export IDs, inspect per-project task counts for an ID, or clear one
or all recorded export-ID histories before a migration wave. New export IDs are
still created by the task export flow when you provide `--export-id`.

### Associate Jira Task Keys with Asana Task Keys

Runs `scripts.atlassian.search_jira_external_ids`. Use it when Jira contains the
external Asana identifiers but the local task-key mappings are missing or incomplete.
Its Jira scope is derived from the stored Asana Project → Jira Space associations.

### Validate Jira task migration coverage

Runs `scripts.export_jira_migration_validation` to compare the selected Asana task
scope with its recovered Jira task mappings and report missing Jira keys.

### Download durable Asana attachment archive

Runs `scripts.download_asana_attachments`. It downloads Asana-hosted files into a
resumable archive with manifests, byte counts, and checksums. This can happen before
Jira task mappings exist.

### Upload attachment archive to Jira

Runs `scripts.upload_jira_attachments`. It verifies the archive, resolves current
task mappings at upload time, uploads ready files, and records per-destination outcomes.
Attachments that failed to download remain skipped in a partial archive instead of
blocking the ready files from uploading.

## Expected Result

Every intended Asana task has either been imported or has a documented skip/failure,
Jira task keys are present in SQLite, and attachment outcomes are recorded. Preserve
the export files and archive manifests until validation is complete.

## Detailed Script Reference

See [Atlassian / Jira Integration](../jira-integration.md) for exact inputs, outputs,
flags, re-run behavior, and attachment verification rules.
