# Phase 3 Implementation Guide: Project-Level Fields in Task Export

## Overview

This guide completes the implementation of including project-level custom fields in individual task exports (`export_project_to_jira_csv`).

**Status:** Foundation in place (helper functions, CLI argument). Integration needed.

## What's Complete ✓

1. **Helper Functions** (lines 50-84 in export_project_to_jira_csv.py):
   - `_fetch_project_custom_fields_for_task_export()` - retrieves project fields for a project
   - `_discover_project_custom_fields_for_projects()` - discovers unique fields across projects

2. **CLI Argument** (parse_args function):
   - `--include-project-fields` - with "prompt" (default), "yes", or "no" options

3. **Function Signature**:
   - `export_project()` now accepts `include_project_fields: bool` parameter

## What Needs Implementation

### Step 1: Handle Include Project Fields in export_project (lines 2140+)

After the function signature and before `database = Database()`, add:

```python
include_project_fields_decision = False
if include_project_fields == "yes":
    include_project_fields_decision = True
elif include_project_fields == "prompt":
    include_project_fields_decision = prompt(
        "Include project-level custom fields in task export? (yes/no): "
    ).lower() == "yes"
```

### Step 2: Fetch Project Fields and Discover Field Names (after line 2163)

After `jira_project_map = database.load_jira_project_mappings()`, add:

```python
project_custom_fields = {}
all_project_field_names = []

if include_project_fields_decision:
    project_custom_fields = _fetch_project_custom_fields_for_task_export(connection, project_gid)
    all_project_field_names = sorted(project_custom_fields.keys())
    logger.info(f"Including {len(all_project_field_names)} project-level custom fields in export")
```

### Step 3: Add Project Fields to CSV Output (before CSV write at line 3361)

When building the CSV, after creating the fieldnames list, add project field columns:

```python
# Append project-level custom field columns to fieldnames
if include_project_fields_decision and all_project_field_names:
    fieldnames.extend([f"Project: {name}" for name in all_project_field_names])
```

### Step 4: Populate Project Fields in CSV Rows (in the row-building loop)

Wherever task rows are appended to the output list, add:

```python
# Add project-level custom field values
if include_project_fields_decision and all_project_field_names:
    for field_name in all_project_field_names:
        row[f"Project: {field_name}"] = project_custom_fields.get(field_name, "")
```

### Step 5: Thread Parameter Through Function Calls

Find all calls to `export_project()` and add the `include_project_fields` parameter:

**In main() function (lines 3808+):**

```python
# After parsing include_project_fields argument
include_project_fields_setting = args.include_project_fields  # already defined by argparse

# When calling export_project():
export_project(
    database_path=args.database,
    project_gid=project_gid,
    output_dir=output_dir,
    ...
    include_project_fields=include_project_fields_setting,  # Add this line
)
```

### Step 6: Handle Multiple Projects (consolidated export)

When exporting multiple projects:

```python
# Discover fields across ALL projects being exported
if include_project_fields_decision and all_project_gids:
    all_project_field_names = _discover_project_custom_fields_for_projects(
        connection, all_project_gids
    )
```

Then in each project's export, fetch and map its specific fields.

## Testing Checklist

- [ ] Run with `--include-project-fields no` - should work as before
- [ ] Run with `--include-project-fields yes` - should include all project fields
- [ ] Run with `--include-project-fields prompt` - should prompt user
- [ ] Verify consolidated output includes all project fields
- [ ] Test with Travel Tracker projects to verify fields like "Stage", "Risk Tier", "Cost" are included
- [ ] Run full test suite: `python3 -m unittest discover tests`

## Verification

After implementation, verify with:

```bash
# Export with project fields
ASANA_JIRA_DB_PATH=/path/to/asana_client_roblox.sqlite \
python3 -m scripts.atlassian.export_project_to_jira_csv \
  --project-gids 1212721646108927 \
  --include-project-fields yes \
  --output-dir exports

# Check headers include project fields
head -1 exports/*.csv | grep "Project:"
```

Expected output should show columns like:
- `Project: Stage`
- `Project: Risk Tier`
- `Project: Type of Travel`
- `Project: Cost`
- etc.

## Files Modified

- `scripts/atlassian/export_project_to_jira_csv.py` - main implementation
- `scripts/inspect_project_data.py` - minor fix to argument handling (already done)

## Related Documentation

- See `export_team_project_jira_csv.py` for similar implementation approach (Phase 2)
- Full project field list in discovery: `inspect_project_data.py --database data/asana_client_roblox.sqlite`

## Notes

- Project fields are the same across ALL tasks within a project (unlike task-level custom fields)
- Column names prefixed with "Project:" to distinguish from task-level fields
- Multi-value fields (people, multi-enum) are semicolon-delimited
- Empty projects will have empty values in all project field columns
