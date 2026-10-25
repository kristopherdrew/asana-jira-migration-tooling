# Export Features Test Audit
**Date:** 2026-10-25  
**Scope:** Existing features only (post-revert baseline for commit de10c34)  
**Goal:** Identify test coverage gaps for working export features to prevent regression

---

## Feature Inventory

### 1. `export_team_project_jira_csv.py` — Team/Project Hierarchy Export

**Purpose:** Export Asana teams and projects into Jira Epic/Task CSV for import.

**Available CLI Flags:**
- `--database` — SQLite database path (required, defaults to active profile DB)
- `--output-dir` — Export directory (default: `exports/`)
- `--filename` — Custom CSV filename (defaults to timestamp-based)
- `--workspace-gid` — Optional, for building project URLs
- `--include-empty-projects` — Include projects with zero synced tasks
- `--log-level` — Logging verbosity

**Key Features:**
1. ✅ **Team→Epic Mapping** — Each team becomes an Epic with structured description
2. ✅ **Project→Task Mapping** — Each project becomes a Task, parents to its team Epic
3. ✅ **No-Team Grouping** — Team-less projects rolled into "No Team" Epic
4. ✅ **Effective Owner Resolution** — Multi-source owner detection (team admin → project owner → task creator)
5. ✅ **Atlassian Account Mapping** — Team/project owners resolved to Atlassian IDs + email
6. ✅ **Task Count Rollups** — Projects aggregated to team level; task counts surfaced
7. ✅ **Description Rich ADF** — Structured descriptions with Primary/Secondary/Implied Details sections
8. ✅ **URL Building** — Asana team/project URLs included in CSV
9. ✅ **Project-Level Custom Fields** — Custom field values included in project (Task row) description
10. ✅ **Instructions Field** — EPIC_INSTRUCTIONS / TASK_INSTRUCTIONS rendered in CSV rows
11. ✅ **Last Working Day** — Most recent task modified_at carried to project Task row
12. ✅ **Project Status Labels** — Owner source (team_admin/project_owner) + project type (team_project) + last_active year as separate Labels columns

---

### 2. `export_project_to_jira_csv.py` — Task-Level Export

**Purpose:** Export a project's tasks into a task-level Jira import CSV.

**Available CLI Flags:**
- **Scope Selection:**
  - `--project-gid` — Single project (mutually exclusive with --project-gids/--team-gid)
  - `--project-gids` — Comma-separated project GIDs (mutually exclusive with --project-gid/--team-gid)
  - `--project-gids-file` — File with newline/comma-separated GIDs
  - `--team-gid` — All projects in team (mutually exclusive with --project-gid/--project-gids)
  - `--task-gids` — Specific task GIDs to export
  - `--task-gids-file` — File with task GIDs

- **Field Selection:**
  - `--custom-fields` — Comma-separated custom field names/GIDs to include
  - `--no-custom-fields` — Skip all custom fields (overrides --custom-fields)
  - `--no-custom-field-prompt` — Skip interactive custom field prompt

- **Export ID Management:**
  - `--export-id` — Optional identifier to track exported tasks and skip re-exports
  - `--clear-export-ids` — Delete all recorded export_id/task mappings (clears DB and exits)
  - `--clear-export-id EXPORT_ID` — Delete specific export ID mappings (clears DB and exits)

- **Jira Project Mapping:**
  - `--map-unmapped-to-jira-project-key KEY` — Assign Jira key to unmapped projects (requires --map-unmapped-to-jira-project-name)
  - `--map-unmapped-to-jira-project-name NAME` — Assign Jira name to unmapped projects (requires --map-unmapped-to-jira-project-key)
  - `--clear-jira-project-map` — Delete all stored Jira project associations (clears DB and exits)

- **Data Refresh:**
  - `--incremental-refresh` — Control refresh: prompt (default), yes, no
  - `--no-refresh-prompt` — Skip refresh prompts
  - `--incremental-project-breakdown` — Pass-through to get_incremental_updates (prompt/yes/no)

- **Output & Format:**
  - `--database` — SQLite path (required, defaults to active profile DB)
  - `--output-dir` — Export directory (default: `exports/`)
  - `--filename` — Custom CSV filename
  - `--stats-only` — Skip CSV, emit only summary CSVs (sections/fields/missing accounts)
  - `--consolidated-output` — Single CSV for multiple projects instead of one per project
  - `--consolidated-filename` — Optional name for consolidated CSV
  - `--also-per-project` — With --consolidated-output, also write per-project CSVs
  - `--archived-scope` — Filter projects by archived state (active/archived/all; default: active)

- **Hierarchy & Epics:**
  - `--add-epics` — Synthetic project-level Epic, re-parent top-level tasks
  - `--add-section-epics` — Synthetic Epic per section, re-parent tasks to first section
  - `--above-epic-issue-type TYPE` — Create synthetic issue type (e.g. Deliverable) per project with section-based Epics beneath
  - `--synthetic-epic-limit N` — Max descendant issues per synthetic Epic before splitting (default: 250)

- **Custom Field Mode:**
  - `--custom-field-mode` — Export custom fields as 'columns' (default) or folded into 'description'

- **Fallback & Special Cases:**
  - `--fallback-atlassian-id` — Atlassian account ID for users missing from mapping tables
  - `--prod` — Use production Jira URL (default: sandbox)
  - `--jira-site SITE_KEY` — Named Jira site key from active profile

- **Logging:**
  - `--log-level` — Logging verbosity

**Key Features:**
1. ✅ **Multi-Project Scope** — Team-wide or explicit project/task list selection
2. ✅ **Incremental Data Refresh** — Prompt user to refresh/resync Asana data before export
3. ✅ **Custom Field Selection** — Interactive prompt to choose which custom fields to include
4. ✅ **Export ID Tracking** — Record exported task GIDs, skip already-exported tasks in re-runs
5. ✅ **Jira Project Mapping** — Store Asana project GID → Jira project key association
6. ✅ **Synthetic Epics** — Optional project-level or section-level Epic creation with hierarchy
7. ✅ **Custom Field Modes** — Export fields as columns (default) or fold into Description
8. ✅ **Consolidated Output** — Multiple projects into single CSV or per-project files
9. ✅ **Archive Filtering** — Scope by project archived state
10. ✅ **Stats-Only Mode** — Print export stats without writing main CSV
11. ✅ **Atlassian Account Resolution** — Map assignee/creator to Atlassian account IDs
12. ✅ **Fallback Assignee** — Default Atlassian ID when user mapping missing
13. ✅ **HTML-to-Jira Link Conversion** — Parse HTML in task descriptions, convert to Jira wiki link format
14. ✅ **Task Hierarchy Preservation** — Parent/child links maintained (depth column)
15. ✅ **Section Export** — Project sections exported as taxonomy
16. ✅ **URL Rewriting** — Asana task URLs converted to project context URLs

---

### 3. `manage_export_ids.py` — Export ID Lifecycle Management

**Purpose:** View, show details, or clear export ID history from database.

**Available CLI Flags:**
- `--database` — SQLite path (required, defaults to active profile DB)
- `--log-level` — Logging verbosity
- **Mutually exclusive actions:**
  - `--list` — List all recorded export IDs with summary stats
  - `--show EXPORT_ID` — Show details for one export ID (task/project breakdown)
  - `--clear-export-id EXPORT_ID` — Delete recorded task history for one export ID
  - `--clear-all` — Delete all recorded export ID task history

**Key Features:**
1. ✅ **Export ID Listing** — View all recorded export IDs with task/project counts and usage dates
2. ✅ **Detailed Inspection** — Per-export-ID breakdown of projects and task counts
3. ✅ **Selective Clearing** — Delete one export ID's history or all history
4. ✅ **Database Persistence** — Queries `exported_tasks` table for history

---

## Test Coverage Analysis

### `export_team_project_jira_csv.test.py` (1190 lines)

**Tests Present:**
- ✅ `test_exports_single_csv_with_epics_first_and_project_parent_links` (500+ lines)
  - Validates Epic rows (team-level) with owner resolution, description structure, instruction text
  - Validates Task rows (project-level) with parent Epic links, custom fields, URLs, last working day
  - Validates "No Team" Epic grouping
  - Validates effective owner resolution logic
  - Validates Atlassian account mapping
  - Validates task counts aggregation
  - **Coverage:** ✅ Happy path only (single team + multiple projects + no-team grouping)

**Tests Missing:**
- ❌ Multiple teams with cross-team owners
- ❌ Empty team (no projects)
- ❌ Empty project (zero tasks synced)
- ❌ Archived project behavior
- ❌ Personal projects (is_personal_project flag)
- ❌ Project-level custom fields integration into Epic description
- ❌ Missing Atlassian account mapping (fallback behavior)
- ❌ Missing user in database (graceful degradation)
- ❌ Deactivated Atlassian accounts
- ❌ Task creator selection when no owner
- ❌ Edge case: team with only archived projects
- ❌ URL building with missing workspace GID
- ❌ CSV filename generation / output directory creation

**Coverage Gap:** ~40% of features tested; edge cases and error paths untested

---

### `export_project_to_jira_csv.test.py` (1809 lines)

**Tests Present:**
- ✅ `test_export_preserves_project_structure_and_text_formatting`
- ✅ `test_export_with_custom_fields_prompt_skipped_preserves_all_fields`
- ✅ `test_parse_project_list_from_file_handles_various_separators`
- ✅ `test_export_tasks_with_consolidated_output`
- ✅ `test_prompt_for_incremental_refresh_allows_user_to_skip`
- ✅ `test_export_id_filtering_and_recording`
- ✅ `test_custom_field_mode_description_folds_fields_into_notes`
- ✅ `test_custom_field_selection_filtering`
- ✅ Various task hierarchy, section, and formatting tests

**Partial Coverage:**
- ✅ **Export ID Recording** — Existing task filtering + recording new exports
- ✅ **Custom Field Selection** — Prompt + filtering logic
- ✅ **Data Refresh** — Prompt logic (mocked)
- ⚠️ **Jira Project Mapping** — Stored but NOT tested end-to-end
- ⚠️ **Incremental Refresh** — Prompt logic tested, but actual refresh NOT tested
- ⚠️ **Consolidated Output** — Basic test exists, edge cases missing
- ⚠️ **Synthetic Epics** — Some coverage, but complex hierarchy cases missing

**Tests Missing:**
- ❌ **Export ID Selection before export** — Interactive selection/review of which export ID to use
- ❌ **Jira Project Key Association** — Storing, retrieving, and using Jira project mappings
- ❌ **Jira Project Key Validation** — Querying live Jira to resolve project key metadata
- ❌ **Clear Export IDs** — --clear-export-ids / --clear-export-id flag behavior
- ❌ **Clear Jira Project Map** — --clear-jira-project-map flag behavior
- ❌ **Map Unmapped Projects** — --map-unmapped-to-jira-project-key/name flow
- ❌ **Multiple projects incremental refresh** — Per-project refresh logic in loop
- ❌ **Incremental refresh actual data pull** — Mock calls to get_incremental_updates
- ❌ **Archive filtering** — --archived-scope (active/archived/all) behavior
- ❌ **Fallback Atlassian ID** — --fallback-atlassian-id when user missing
- ❌ **Missing Atlassian accounts** — Graceful handling with empty assignment
- ❌ **Deactivated Atlassian accounts** — Visible in export but can't assign
- ❌ **Task GID scope parsing** — --task-gids and --task-gids-file
- ❌ **Stats-only mode** — --stats-only flag output validation
- ❌ **HTML link parsing edge cases** — Malformed HTML, nested anchors
- ❌ **Custom field mode switching** — Edge cases between 'columns' vs 'description'
- ❌ **Output filename edge cases** — Special characters, existing files, directory creation
- ❌ **Synthetic epic partitioning** — Large task counts with --synthetic-epic-limit
- ❌ **Section-based epics** — --add-section-epics with multi-section projects
- ❌ **Above-epic issue type** — Hierarchical issue type creation
- ❌ **Jira site resolution** — --jira-site / --prod flag behavior
- ❌ **CLI argument parsing** — All permutations of mutually exclusive flags

**Coverage Gap:** ~65% of features tested; critical interactive features (Export ID selection, Jira key mapping) completely untested

---

### `manage_export_ids.test.py` (85 lines)

**Tests Present:**
- ✅ Minimal basic structure setup

**Tests Missing:**
- ❌ `--list` action (show all export IDs)
- ❌ `--show EXPORT_ID` action (detailed inspection)
- ❌ `--clear-export-id EXPORT_ID` action (selective clear)
- ❌ `--clear-all` action (full clear)
- ❌ Non-existent export ID handling
- ❌ Empty export ID history handling
- ❌ Date formatting in output
- ❌ Project breakdown aggregation

**Coverage Gap:** ~95% untested

---

### Interactive CLI Integration Tests

**Location:** `tests/unit/scripts/interactive_cli.test.py`

**Current Coverage:**
- ⚠️ General menu structure testing (minimal export-specific)
- ❌ Export menu options
- ❌ Export ID selection workflow
- ❌ Field selection workflow
- ❌ Jira project key assignment workflow
- ❌ Data refresh workflow prompts
- ❌ Team/project scope resolution via CLI

**Coverage Gap:** ~100% of export-specific CLI paths untested

---

## Working Features Regression Test Checklist

These 5 features are critical and currently working. Each needs end-to-end test coverage to prevent regression:

### 1. **Export ID Management** ❌ UNTESTED (end-to-end)
- **Current:** Flag `--export-id` accepts string, records exported tasks in DB
- **Missing Test:**
  - Record export ID on first run
  - Filter out already-exported tasks on second run with same export ID
  - Clear export ID via `--clear-export-ids` or `--clear-export-id`
  - Verify `manage_export_ids.py --list` shows recorded IDs
  - Verify `manage_export_ids.py --show` shows task breakdown

### 2. **Field Selection** ⚠️ PARTIALLY TESTED
- **Current:** `--custom-fields` flag + interactive prompt filter exported fields
- **Missing Test:**
  - Interactive prompt (currently mocked)
  - Actual field filtering logic (has tests, but edge cases missing)
  - `--no-custom-fields` behavior (skip all fields)
  - `--no-custom-field-prompt` (non-interactive mode)
  - Edge case: Requested field doesn't exist
  - Edge case: All fields requested but none exist

### 3. **Jira Project Key Association** ❌ COMPLETELY UNTESTED
- **Current:** `--map-unmapped-to-jira-project-key KEY --map-unmapped-to-jira-project-name NAME` stores mapping in DB
- **Missing Test:**
  - Store mapping in `team_project_jira_mappings` table
  - Retrieve stored mapping on subsequent exports
  - Prevent duplicate mappings
  - `--clear-jira-project-map` clears all mappings
  - Validation that key and name are both provided (mutually dependent)
  - CSV export includes Jira key in output

### 4. **Data Refresh Option** ⚠️ PARTIALLY TESTED
- **Current:** `--incremental-refresh (prompt/yes/no)` can trigger pre-export sync
- **Missing Test:**
  - Actual call to `get_incremental_updates` (mocked in tests)
  - Per-project refresh prompting (only generic prompt tested)
  - `--no-refresh-prompt` skips prompts
  - Refresh failure handling
  - Refresh completion verification

### 5. **Discovery Infrastructure** ✅ TESTED (indirectly)
- **Current:** `inspect_project_data.py` catalogs project-level custom fields
- **Tests:** Implicit in `export_team_project_jira_csv` custom field inclusion
- **Gap:** `inspect_project_data.py` itself has no dedicated test

---

## Test Structure Recommendations

### Unit Tests (src/lib tests)
Keep these focused on pure logic:
- `export_id_history.py` — List/get/clear operations
- Custom field filtering and resolution
- Owner resolution logic
- Atlassian account mapping fallback
- HTML link parsing edge cases
- Project scope resolution

### Integration Tests (scripts tests)
End-to-end CLI + DB scenarios:
- `export_team_project_jira_csv.test.py` — Add edge case tests
- `export_project_to_jira_csv.test.py` — Add export ID + jira key mapping tests
- `manage_export_ids.test.py` — Add full action tests
- `interactive_cli_export.test.py` — NEW: Add CLI workflow tests (export ID selection, field prompt, etc.)

### Regression Test Suite (ensures no regression on reverts)
5 tests covering the working features:
```
tests/regression/
  - export_id_lifecycle.test.py       # Record → filter → clear
  - field_selection_workflow.test.py   # Interactive + flag combinations
  - jira_key_mapping_workflow.test.py  # Store → retrieve → clear
  - data_refresh_workflow.test.py      # Prompt decision → actual refresh
  - discovery_integration.test.py      # inspect_project_data + export integration
```

---

## Coverage Target & Completion Criteria

**Goal:** ≥90% statement coverage on all export code (src/lib/export*, scripts/export*, scripts/atlassian/export*)

**Baseline (current):**
- `export_team_project_jira_csv` — ~70% coverage (need edge cases + error paths)
- `export_project_to_jira_csv` — ~40% coverage (need interactive features + mappings)
- `manage_export_ids` — ~5% coverage (need all action tests)
- Interactive CLI — ~0% export-specific coverage (need workflow tests)

**Target Distribution:**
- Unit tests: 60% (pure logic)
- Integration tests: 30% (CLI + DB)
- Regression tests: 10% (critical workflows)

**Success Criteria:**
1. All 5 working features have end-to-end tests
2. CLI argument parsing validated (all flags + mutually exclusive combos)
3. Edge cases covered: empty scope, missing data, deactivated accounts, etc.
4. Error paths tested: missing files, permission errors, DB constraint violations
5. All tests pass before merging any new export features

---

## Audit Summary

**Total Features Inventoried:** 45+ features across 3 scripts  
**Fully Tested:** ~12 features (27%)  
**Partially Tested:** ~8 features (18%)  
**Untested:** ~25 features (55%)

**Highest-Risk Gaps:**
1. 🔴 **Export ID Management** — Core feature, zero end-to-end tests
2. 🔴 **Jira Project Key Mapping** — No tests for store/retrieve/clear workflow
3. 🔴 **manage_export_ids.py** — 95% untested
4. 🔴 **Interactive CLI Export Workflows** — 100% untested
5. 🟡 **Data Refresh Integration** — Prompt tested, actual refresh not tested

**Next Steps:**
1. Write regression tests for 5 working features (1-2 days)
2. Expand export_team_project_jira_csv.test.py with edge cases (1 day)
3. Expand export_project_to_jira_csv.test.py with missing features (2 days)
4. Implement manage_export_ids.test.py full suite (1 day)
5. Add interactive_cli_export.test.py for workflow testing (1-2 days)
6. Run full test suite and verify ≥90% coverage (1 day)

**Estimated effort:** ~5-7 days to achieve 90%+ coverage on all export features

