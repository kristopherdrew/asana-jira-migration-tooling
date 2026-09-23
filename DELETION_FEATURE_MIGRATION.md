# Task Deletion Feature - Export Script Migration Guide

This document outlines the changes needed to support the new `is_deleted` flag across all export scripts in the Asana→Jira migration toolkit.

## Overview

The `is_deleted` column on the `tasks` table tracks tasks that are no longer found during syncs. By default, all export scripts should exclude deleted tasks. An optional `--include-deleted` flag allows including them when needed.

## Completed Changes

✅ Schema: Added `is_deleted` (INTEGER, DEFAULT 0) and `deleted_at` (TEXT) columns to `tasks` table  
✅ Sync Scripts:
  - `scripts/get_tasks.py` - Marks project tasks as deleted if not found in fresh sync
  - `scripts/get_personal_tasks.py` - Marks personal tasks as deleted if not found
  - `scripts/get_incremental_updates.py` - Skipped (incremental-only, can't reliably detect deletions)

✅ Helper Function: `build_task_deletion_filter_clause(include_deleted: bool)` in `src/lib/database.py`

## Export Scripts Requiring Updates

### Priority 1: Main Exporters (Most Critical)
These are the scripts users directly invoke for migrations:

- [ ] `scripts/atlassian/export_project_to_jira_csv.py` - Main per-project CSV export
- [ ] `scripts/atlassian/export_team_project_jira_csv.py` - Team→Epic/Project→Task hierarchy
- [ ] `scripts/create_team_project_jira_issues.py` - Direct Jira API creation
- [ ] `scripts/export_all_reports_xlsx.py` - Multi-tab workbook combining all reports

### Priority 2: Task-Counting Reports
These aggregate task counts and must track active vs. deleted separately:

- [ ] `scripts/export_project_details_report.py` - Task counts per project
- [ ] `scripts/export_project_last_updated_report.py` - Last task update per project
- [ ] `scripts/export_team_report.py` - Task counts per team
- [ ] `scripts/export_custom_field_usage_report.py` - Custom field usage across tasks
- [ ] `scripts/export_field_section_review_report.py` - Section/field usage per project
- [ ] `scripts/export_jira_migration_validation.py` - Jira coverage validation

### Priority 3: Supplementary Exporters
These read task data but are less frequently used:

- [ ] `scripts/export_archival_project_jira_csv.py` - Simplified CSV for archival projects
- [ ] `scripts/export_users.py` - Task activity counts per user
- [ ] `scripts/download_asana_attachments.py` - Attachment download (scoped to tasks)
- [ ] `scripts/upload_jira_attachments.py` - Attachment upload (keyed by task mappings)
- [ ] `scripts/export_recurring_tasks_report.py` - Recurring task instances
- [ ] `scripts/export_project_members_report.py` - Project membership (limited task dependency)
- [ ] `scripts/export_team_memberships.py` - Team membership (no direct task dependency)
- [ ] `scripts/export_portfolio_report.py` - Portfolio items (limited task dependency)
- [ ] `scripts/merge_project_exports.py` - Merge helper (transparent to deletion)
- [ ] `scripts/atlassian/search_jira_external_ids.py` - External ID lookup (not task-dependent)
- [ ] `scripts/manage_export_ids.py` - Export ID management (scoped to tasks)
- [ ] `scripts/clear_jira_task_keys.py` - Clear mappings (indifferent to deletion)

## Implementation Pattern

For each script, follow this pattern:

### 1. Add CLI Flag
```python
def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    # ... existing args ...
    parser.add_argument(
        "--include-deleted",
        action="store_true",
        help="Include deleted tasks in the export (default: exclude).",
    )
    return parser.parse_args(argv)
```

### 2. Update SQL Queries
Use `build_task_deletion_filter_clause()` to filter tasks:

```python
from src.lib.database import build_task_deletion_filter_clause

# In your export function:
deletion_filter = build_task_deletion_filter_clause(include_deleted)
query = f"""
    SELECT t.gid, t.name FROM tasks t
    WHERE {deletion_filter}
    AND t.project_gid = ?
"""
```

### 3. For Interactive CLI
Add a prompt to ask the user:

```python
if args.team or args.all_teams or some_interactive_mode:
    include_deleted = input("Include deleted tasks? (y/n) [default: n]: ").lower() == 'y'
else:
    include_deleted = args.include_deleted
```

### 4. For Task-Count Reports
Track active and deleted separately:

```python
query = """
    SELECT
        COUNT(CASE WHEN is_deleted = 0 THEN 1 END) as task_count,
        COUNT(CASE WHEN is_deleted = 1 THEN 1 END) as deleted_task_count,
        COUNT(*) as total_task_count
    FROM tasks WHERE project_gid = ?
"""
# Include task_count in the report, deleted_task_count in a separate column
```

## Testing

After updating a script, verify:

1. **Default behavior excludes deleted tasks:**
   ```bash
   python3 -m scripts.export_project_to_jira_csv --project-gid proj_1
   # Should not include tasks where is_deleted = 1
   ```

2. **--include-deleted flag includes all tasks:**
   ```bash
   python3 -m scripts.export_project_to_jira_csv --project-gid proj_1 --include-deleted
   # Should include tasks where is_deleted = 1
   ```

3. **Task counts are accurate:**
   - Verify task_count (without deleted) + deleted_task_count = total

## Notes

- **Backward compatibility:** The `is_deleted` column defaults to 0, so existing exports work unchanged (all tasks appear active).
- **Soft delete philosophy:** Deleted tasks remain in the database with `deleted_at` timestamp for audit trails and potential recovery.
- **Incremental sync:** The `get_incremental_updates.py` script intentionally does NOT mark tasks as deleted (incremental-only, can't reliably detect deletions). Use full syncs for accurate deletion detection.

## Related Files

- `src/lib/database.py` - `Database.mark_tasks_as_deleted()` and `build_task_deletion_filter_clause()`
- `src/lib/table_metadata.py` - Updated `tasks` table metadata documenting new columns
- `docs/data-model.md` - Auto-generated, will be updated when schema is regenerated
- `docs/anonymization-map.md` - Auto-generated, may need updates if new columns are anonymized
