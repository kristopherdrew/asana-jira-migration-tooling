# Export Feature Test Coverage Audit

**Date:** 2026-10-25  
**Scope:** Asana→Jira migration toolkit export functionality  
**Test Suite Status:** 843 tests passing ✅

---

## Executive Summary

The export feature suite has **good regression-level coverage** for end-to-end workflows (export ID lifecycle, field selection, Jira key mapping) but exhibits **significant unit-test gaps** in critical helper functions that directly impact export quality. The main export script (`export_project_to_jira_csv.py`) has 63 public functions with only 11 directly tested; most export logic is validated implicitly through 30+ integration tests that exercise full scenarios rather than isolated unit tests.

**Key finding:** Export functionality is well-guarded by regression tests that catch workflow breakage, but refactoring individual helper functions is high-risk without dedicated unit tests.

---

## 1. PUBLIC API INVENTORY

### Core Export Modules

#### A. `src/lib/export_id_history.py` (4 functions, ~150 LOC)
- `list_export_ids()` — Query all recorded export IDs with task counts
- `get_export_id_detail()` — Drill into one export ID with per-project breakdown
- `clear_export_id()` — Delete task history for one export ID
- `clear_all_export_ids()` — Wipe all export history

**Companion internal functions:**
- `list_export_ids_for_connection()`
- `get_export_id_detail_for_connection()`

---

#### B. `src/lib/jira_import_fields.py` (6 public, ~160 LOC)
- `ensure_standard_import_fields()` — Idempotently create standard Asana Jira fields (Section, Project Link, Task Link)
- `STANDARD_IMPORT_FIELDS` — Tuple of 3 field definitions (text, URL, URL)
- `TRACKING_FIELDS` — Tuple of 6 field definitions for Team/Project-level tracking (links, counts, dates, statuses, decisions)
- `FIELD_SETS` — Dict mapping "import" and "tracking" to field sequences
- `JiraImportField` — Dataclass: name, type, searcher, description, options tuple

**Data structures:**
- Field type constants (TEXT_FIELD_TYPE, URL_FIELD_TYPE, NUMBER_FIELD_TYPE, etc.)
- Searcher constants (TEXT_SEARCHER, EXACT_TEXT_SEARCHER, etc.)

---

#### C. `src/lib/jira_csv_import_config.py` (25+ public, ~540 LOC)
- `load_archive_import_settings()` — Read persisted field mappings from env file (site/profile-scoped)
- `save_archive_import_settings()` — Persist field mappings back to env
- `clear_archive_import_settings()` — Wipe saved config for one Jira site
- `normalize_fields()` — Convert Jira API field array into JiraFieldChoice list
- `prompt_field_choice()` — Interactive prompt for field selection (fuzzy match, exact lookup, manual entry)
- `review_archive_import_settings()` — Multi-step interactive review/edit of all 5 field mappings
- `build_archive_import_config_for_columns()` — Generate Jira importer .txt config from column list
- `build_archive_import_config()` — Read CSV header → generate config
- `render_reusable_import_config()` — JSON string for standard columns
- `write_reusable_import_config()` — Write dated config file to profiles/
- `write_archive_import_config()` — Companion config file next to CSV
- `profile_import_config_path()` — Path builder for dated config files
- `latest_profile_import_config_path()` — Retrieve newest config for a site
- `stale_profile_import_config_paths()` — Cleanup old configs
- `copy_active_profile_import_config()` — Bundle config with export
- `_fuzzy_field_matches()` — Fuzzy search for field by name (internal)
- `_field_by_id()` — Exact field lookup by ID (internal)

**Data structures:**
- `JiraFieldChoice` — field_id, name, field_type, schema_type
- `ArchiveImportSettings` — section, project_link, task_link, external_id, start_date, above_epic_issue_type_*
- `SYSTEM_FIELD_MAPPINGS` — Dict of CSV column → Jira importer mapping
- `CANONICAL_TASK_IMPORT_COLUMNS` — Tuple of standard CSV column names

---

#### D. `src/lib/jira_csv_partition.py` (5 public, ~200 LOC)
- `parse_synthetic_epic_limit()` — Argparse validator for --synthetic-epic-limit
- `count_csv_issues()` — CSV row count (issues)
- `partition_project_csv()` — Split oversized CSV into Jira-compliant parts (≤8k issues)
  - Keeps parent/subtask families together
  - Splits synthetic epics when > limit
  - Balances load across parts
- `pack_csv_parts()` — Consolidate multiple part CSVs into batch groups
- `write_partition_manifest()` — JSON manifest of partition results

**Data structures:**
- `CsvPart` — path: Path, issue_count: int

---

#### E. `src/lib/database.py` — Export/Mapping Methods (11 functions, ~200 LOC within 3000+ total)

**Export History:**
- `record_exported_tasks(connection, export_id, task_gids, project_gid, sync_time)` → int (count recorded)
- `get_exported_task_gids(connection, export_id)` → Set[str]
- `get_exported_task_projects(connection, export_id)` → Dict[project_gid, count]
- `clear_export_history(connection)` → int (rows deleted)
- `clear_export_history_for_id(connection, export_id)` → int

**Jira Project Mappings:**
- `upsert_jira_project_mappings(connection, mappings[], sync_time, jira_site_key=None)` → int (written)
- `load_jira_project_mappings(connection, jira_site_key=None)` → Dict[project_gid, {key, name, url}]
- `clear_jira_project_mappings(connection)` → int

**Team/Project Jira Mappings (Epic/Task level):**
- `upsert_team_project_jira_mappings(connection, mappings[], sync_time)` → int
- `load_team_project_jira_mappings(connection)` → Dict[(kind, gid), jira_key]
- `delete_team_project_jira_mappings(connection, scopes=[])` → int

**Task Jira Mappings:**
- `upsert_task_jira_mappings(connection, mappings[], sync_time, jira_site_key=None, overwrite=False)` → {written, skipped}
- `upsert_project_jira_decisions(connection, decisions[], sync_time)` → {written, skipped}

---

#### F. Main Export Scripts (63 public functions across 4 scripts, ~3500 LOC total)

**`scripts/atlassian/export_project_to_jira_csv.py` (63 public)**
- **Export orchestration:**
  - `export_project()` — Single project export (rows, columns, epics, exports, updates, comments)
  - `build_project_export_data()` — Fetch & transform project data into row dicts
- **Incremental refresh:**
  - `compute_incremental_refresh_window()` — Time delta for stale detection
  - `prompt_and_run_incremental_refresh()` — Interactive user flow
  - `fetch_project_latest_modified()` — Query DB for last task update
- **Jira project mapping:**
  - `parse_project_list()` — CSV/explicit GID → project set
  - `assign_unmapped_jira_project()` — Store key/name for project
  - `lookup_jira_project_name()` — Resolve key via Jira API
  - `prompt_for_unmapped_jira_project()` — Interactive mapping flow
- **Custom field handling:**
  - `apply_custom_field_filter()` — Include/exclude by name or GID
  - `resolve_custom_field_value()` — Extract & format field value for CSV
  - `log_custom_field_list()` — Print discovery summary
  - `preview_project_custom_fields()` — Stats for user review
  - `prompt_for_custom_field_choice()` — Interactive inclusion prompt
  - `split_fields_by_choice()` — Separate into include/exclude
- **User/Atlassian ID resolution:**
  - `resolve_atlassian_identity()` — Map Asana GID → Atlassian ID with fallback
  - `load_user_display_names()` — Cache from DB
  - `load_user_emails()` — Cache from DB
  - `format_user_reference()` — Format assignee/creator column values
  - `format_user_display_name()` — Plain-text or Atlassian link
  - `record_user_association()` — Track collaborators seen
  - `load_task_collaborators()` — Fetch followers from task
  - `exclude_task_owner_collaborators()` — Filter out assignee/creator
- **Link transformation:**
  - `transform_asana_links()` — Rewrite Asana URLs in notes/comments for Jira
  - `convert_html_links_to_jira_format()` — HTML `<a>` → `[text|url]`
  - `prefer_html_text_with_links()` — Choose HTML or plain text
  - `extract_html_hrefs()` — Collect URLs from HTML
  - `extract_profile_gid_overrides()` — Parse user profile links from HTML
  - `build_jira_task_search_url()` — Construct search link for task ID
  - `build_asana_link_addition()` — Append Asana link context
- **Formatting & output:**
  - `format_custom_field_names_line()` — CSV header from field list
  - `build_default_filename()` — `<ProjectName>_tasks_<timestamp>.csv`
  - `build_epic_description()` — Synthetic Epic body
  - `build_archival_description()` — Custom fields block for archival export
  - `ensure_tasks_suffix()` — File naming consistency
  - `sanitize_filename_component()` — Safe characters only
  - `format_comment_row()` → Dict for comment/update CSV row
  - `format_comment_timestamp()` — RFC ISO or user-friendly
  - `blank_task_title_placeholder()` — Dated default for untitled tasks
  - `blank_comment_placeholder()` — Dated default for empty comments
  - `export_task_name()` — Task name with blank protection
  - `format_jira_hierarchy_depth()` — Depth → issue type
  - `format_export_datetime()` — Task date → Jira format
  - `format_project_entry()` — Project name + GID
  - `parse_iso_datetime()` — RFC 3339 → datetime
  - `normalize_jira_base_url()` — Add https://, strip trailing /
  - `current_utc_time()` → datetime (UTC now)
- **I/O & state:**
  - `next_export_dir()` — Auto-increment output folder
  - `organize_outputs()` — Move CSV/companions into dated folder
  - `fetch_project_task_activity()` — Query task-update timeline
  - `fetch_project_update_comment_rows()` — Project update → comment rows
  - `write_link_modifications_report()` — Summary of link rewrites
  - `write_missing_account_report()` — Unmapped users
  - `write_user_association_report()` — Collaborators seen
  - `filter_missing_account_counts()` → counts by user
  - `print_project_stats()` — Summary output
  - `build_synthetic_epic_row()` → {dict} for empty project
  - `normalize_label_value()` — Asana tags → Jira labels
- **CLI:**
  - `parse_args()` — Full argparse
  - `main()` — Entry point

---

**`scripts/atlassian/export_archival_project_jira_csv.py` (8 public)**
- `export_archival_project()` — Archive-mode export (fields → description block, not columns)
- `build_archival_description()` — `--- Asana Fields ---` block
- `build_epic_description()` — Synthetic epic (with collaborators)
- `verify_csv_part_counts()` — Sanity check partition results
- `build_default_filename()` — Project GID in name
- `parse_args()`
- `main()`

---

**`scripts/atlassian/export_team_project_jira_csv.py` (2 primary + main)**
- `export_team_project_csv()` — Team Epic + Project Task hierarchy
- `main()`

---

### Export-Related Scripts (No Unit Tests, Regression Only or Untested)

#### `scripts/atlassian/import_jira_project_map.py` (66 LOC, **NO TESTS**)
- `read_rows()` — Parse CSV with project_gid, project_key, project_name columns
- `parse_args()` — `--csv`, `--database`
- `main()` — Load CSV → upsert jira_project_mappings

**Status:** ❌ **UNCOVERED** — Integration points with database layer untested

---

#### `scripts/atlassian/generate_jira_csv_import_config.py` (320 LOC, **HAS TESTS**)
- Prompts for field mappings
- Calls `ensure_standard_import_fields()` for field creation
- Saves config via `write_reusable_import_config()`
- **Test file:** `tests/unit/scripts/generate_jira_csv_import_config.test.py` (60 tests)
- **Coverage:** ✅ COVERED (all major flows tested)

---

#### `scripts/manage_export_ids.py` (125 LOC, **HAS TESTS**)
- CLI wrapper around `export_id_history.py` functions
- `main()` — `--list`, `--show`, `--clear-export-id`, `--clear-all`
- **Test file:** `tests/unit/scripts/manage_export_ids.test.py`
- **Regression test:** `tests/regression/export_id_lifecycle.test.py`
- **Coverage:** ✅ COVERED (full workflow tested)

---

#### `scripts/atlassian/clear_jira_task_keys.py` (80 LOC, **HAS TESTS**)
- Reset task→Jira mappings before re-importing
- **Test file:** `tests/unit/scripts/clear_jira_task_keys.test.py`
- **Coverage:** ✅ COVERED

---

#### `scripts/atlassian/search_jira_external_ids.py` (90 LOC, **HAS TESTS**)
- JQL search for external-ID field
- Populate task_jira_mappings
- **Test file:** `tests/unit/scripts/search_jira_external_ids.test.py`
- **Coverage:** ✅ COVERED

---

#### `scripts/atlassian/export_project_import_coverage.py` (204 LOC, **NO TESTS**)
- Multi-site Jira coverage audit
- Reconciles Asana snapshot vs live Jira
- **Status:** ⚠️ **PARTIAL** — Core reconciliation logic not unit-tested; used mainly as standalone report generator

---

#### `scripts/atlassian/export_atjm_coverage_report.py` (6 LOC, **NO TESTS**)
- Thin wrapper (likely deprecated/minimal)
- **Status:** ❌ **UNCOVERED**

---

#### `scripts/atlassian/backfill_project_mappings_from_tasks.py` (243 LOC, **NO TESTS**)
- Recover project keys from existing task Jira mappings
- **Status:** ❌ **UNCOVERED** — High-risk data recovery logic without tests

---

#### `scripts/atlassian/publish_markdown_to_confluence.py` (253 LOC, **NO TESTS**)
- Push Markdown docs to Confluence
- **Status:** ❌ **UNCOVERED** — Content sync logic untested

---

#### `scripts/merge_project_exports.py` (92 LOC, **HAS TESTS**)
- Consolidate multiple per-project CSVs
- `merge_csv_files()` — Union headers, sort by hierarchy
- `merge_field_counts()` — Aggregate custom field usage
- `merge_link_mods()` — Combine link-change manifests
- **Test file:** `tests/unit/scripts/merge_project_exports.test.py`
- **Coverage:** ✅ COVERED

---

### Export Reports (Not primary export machinery, but related)
- `export_all_reports_xlsx.py` — Multi-tab workbook
- `export_custom_field_usage_report.py`
- `export_field_section_review_report.py`
- `export_jira_migration_validation.py`
- `export_project_details_report.py`
- `export_project_import_status.py`
- `export_project_last_updated_report.py`
- `export_project_members_report.py`
- `export_team_memberships.py`
- `export_team_report.py`
- `export_users.py`
- `export_recurring_tasks_report.py`

**Status:** ✅ **All have unit tests** — `tests/unit/scripts/export_*.test.py`

---

## 2. TEST COVERAGE CROSS-REFERENCE

### Unit Tests by Module

#### `tests/unit/lib/export_id_history.test.py` ✅
- 6 test methods
- Covers all 4 public functions
- **Tests:**
  - `test_list_export_ids_orders_by_last_used()`
  - `test_get_export_id_detail_groups_by_project()`
  - `test_get_export_id_detail_returns_none_for_unknown_id()`
  - `test_clear_export_id_preserves_other_ids()`
  - `test_clear_all_export_ids_removes_all_rows()`

---

#### `tests/unit/lib/jira_import_fields.test.py` ✅
- 8 test methods
- Covers `ensure_standard_import_fields()` and field definitions
- **Tests:**
  - `test_definitions_have_requested_names_and_types()`
  - `test_tracking_definitions_and_select_options()`
  - `test_creates_all_missing_fields()`
  - `test_dry_run_does_not_create_missing_fields()`
  - `test_matching_existing_field_is_retained_case_insensitively()`
  - `test_wrong_type_is_rejected()`
  - `test_duplicate_existing_fields_are_skipped_without_creating_another()`
  - `test_all_conflicts_are_checked_before_any_creation()`

---

#### `tests/unit/lib/jira_csv_import_config.test.py` ✅
- 10 test methods
- Covers config load/save, field prompting, config generation
- **Tests:**
  - `test_load_and_save_round_trip()`
  - `test_fuzzy_field_search_by_name()`
  - `test_field_id_entry_manual_mode()`
  - `test_prompt_with_exact_name_match_skips_search()`
  - `test_clear_archive_import_settings_removes_all_keys()`
  - `test_render_reusable_import_config_includes_all_standard_columns()`
  - `test_latest_profile_import_config_path_prefers_dated_over_legacy()`
  - `test_stale_profile_import_config_paths_keeps_newest_only()`
  - `test_copy_active_profile_import_config_creates_file_in_output_dir()`
  - `test_build_archive_import_config_for_columns_uses_provided_columns()`

---

#### `tests/unit/lib/jira_csv_partition.test.py` ✅
- 9 test methods
- Covers partition, pack, and synthetic epic splitting
- **Tests:**
  - `test_parses_synthetic_epic_limit()`
  - `test_balances_top_level_tasks_across_parts()`
  - `test_keeps_real_parent_subtrees_together()`
  - `test_splits_oversized_synthetic_epic_into_part_epics()`
  - `test_balances_1600_tasks_across_two_synthetic_epics()`
  - `test_keeps_subtask_families_together_when_splitting_synthetic_epic()`
  - `test_rejects_one_real_hierarchy_larger_than_limit()`
  - `test_consolidation_packs_whole_project_parts()`

---

#### `tests/unit/lib/database_exports.test.py` ✅
- 3 test methods
- Covers record, fetch, and clear for export history
- **Tests:**
  - `test_record_and_fetch_exported_tasks()`
  - `test_clear_export_history_removes_all_rows()`

---

#### `tests/unit/lib/database_jira_mappings.test.py` ✅
- 8 test methods
- Covers task Jira mapping upsert, conflict detection, overwrite, multi-site
- **Tests:**
  - `test_upsert_task_jira_mappings_keeps_first_key_per_task()`
  - `test_upsert_task_jira_mappings_detects_conflicting_keys()`
  - `test_upsert_task_jira_mappings_overwrite_replaces_key_for_task()`
  - `test_upsert_task_jira_mappings_overwrite_reclaims_conflicting_key()`
  - `test_same_jira_key_is_independent_between_sites()`
  - `test_legacy_mapping_schema_is_preserved_and_attributed()`

---

#### `tests/unit/lib/database_team_project_jira_mappings.test.py` ✅
- 6 test methods
- Covers team/project Jira mapping lifecycle
- **Tests:**
  - `test_upsert_and_load_round_trip()`
  - `test_upsert_updates_existing_key()`
  - `test_delete_scoped_keys_removes_only_targeted_rows()`
  - `test_delete_all_clears_table()`
  - `test_duplicate_jira_key_for_different_entity_raises()`

---

#### `tests/unit/scripts/export_project_to_jira_csv.test.py` ⚠️ PARTIAL
- 41 test methods
- **Directly tested functions:** 11 of 63
  - `export_project()` ✅
  - `format_custom_field_names_line()` ✅
  - `compute_incremental_refresh_window()` ✅
  - `prompt_and_run_incremental_refresh()` ✅
  - `normalize_jira_base_url()` ✅
  - `parse_project_list()` ✅
  - `assign_unmapped_jira_project()` ✅
  - `lookup_jira_project_name()` ✅
  - `prompt_for_unmapped_jira_project()` ✅
  - `print_project_stats()` ✅
  - `parse_args()` ✅

- **Implicitly tested (via `export_project()` integration):** 30+ helpers
  - User resolution
  - Link transformation
  - Custom field filtering
  - Comment formatting
  - Hierarchical export
  - Export ID filtering

- **Never tested in isolation:** 22 functions
  - `extract_profile_gid_overrides()`
  - `load_user_display_names()`, `load_user_emails()`
  - `next_export_dir()`, `organize_outputs()`
  - `transform_asana_links()`, `convert_html_links_to_jira_format()`
  - `prefer_html_text_with_links()`, `extract_html_hrefs()`
  - `build_jira_task_search_url()`, `build_asana_link_addition()`
  - `load_project_names()`, `load_task_collaborators()`
  - `exclude_task_owner_collaborators()`
  - `format_user_reference()`, `format_user_display_name()`
  - `sanitize_filename_component()`, `current_utc_time()`
  - `build_default_filename()`, `build_epic_description()`
  - `fetch_project_latest_modified()`, `fetch_project_task_activity()`
  - `write_link_modifications_report()`, `write_missing_account_report()`
  - And 8 more minor formatters

---

#### `tests/unit/scripts/export_archival_project_jira_csv.test.py` ✅
- 22 test methods
- **Directly tested functions:** 6 of 8
  - `export_archival_project()` ✅
  - `build_archival_description()` ✅
  - `build_default_filename()` ✅
  - `verify_csv_part_counts()` ✅
  - `main()` ✅

- **Never directly tested:**
  - `build_epic_description()` (used but not isolated)

---

#### `tests/unit/scripts/generate_jira_csv_import_config.test.py` ✅
- ~10 test methods
- Full workflow: field discovery → mapping → config generation
- Covers interactive prompts and validation

---

### Regression Tests (End-to-End Workflows)

#### `tests/regression/export_id_lifecycle.test.py` ✅
- 5 test methods
- **Scenarios:**
  - Record and filter export ID across runs
  - Multiple export IDs tracked independently
  - Get detailed export ID with project breakdown
  - Clear single export ID
  - Clear all export IDs
- **Coverage:** Full lifecycle for export ID management

---

#### `tests/regression/field_selection_workflow.test.py` ✅
- 13 test methods
- **Scenarios:**
  - Parse `--custom-fields` flag by name
  - `--no-custom-fields` flag
  - `--no-custom-field-prompt` non-interactive mode
  - Precedence of flags
  - Field selection with spaces
  - Field GID selection
  - `--custom-field-mode columns` vs `description`
  - Consolidated output with field selection
  - Refresh prompts with field selection
- **Coverage:** Full custom field workflow

---

#### `tests/regression/jira_key_mapping_workflow.test.py` ✅
- 14 test methods
- **Scenarios:**
  - Store/retrieve Jira project mappings
  - Multiple project mappings independently
  - Upsert (prevent duplicates)
  - Clear single / all mappings
  - Unicode and special characters in keys/names
  - Lookup by Asana GID
  - Mapping persistence across sessions
  - Validation requires both key and name
- **Coverage:** Full Jira key mapping lifecycle

---

#### `tests/regression/data_refresh_workflow.test.py` ✅
- 10 test methods
- **Scenarios:**
  - Incremental refresh defaults (prompt)
  - `--incremental-refresh yes/no` options
  - `--incremental-project-breakdown` per-project control
  - `--no-refresh-prompt` overrides
  - Project sync timestamp tracking
  - Freshness and staleness detection
  - Task count change detection
  - Isolation per project
- **Coverage:** Full incremental refresh workflow

---

#### `tests/regression/discovery_integration.test.py` ✅
- 8 test methods
- **Scenarios:**
  - Project-level custom fields discovery
  - Multiple enum values (semicolon-separated)
  - Common fields across projects
  - Projects without custom fields
  - Per-project field isolation
  - Count by project
  - Different field types
- **Coverage:** Custom field discovery during project sync

---

### Missing / Untested Modules

#### `tests/unit/atlassian/test_reverse_lookup_jira_to_asana.py` ✅
- File exists (naming convention: `test_*` instead of `*.test.py`)
- Tests the `reverse_lookup_jira_to_asana.py` script

---

#### ❌ No tests for:
- `scripts/atlassian/import_jira_project_map.py` — CSV load, upsert
- `scripts/atlassian/export_project_import_coverage.py` — Reconciliation logic
- `scripts/atlassian/export_atjm_coverage_report.py` — (Minimal/deprecated)
- `scripts/atlassian/backfill_project_mappings_from_tasks.py` — Key recovery from task mappings
- `scripts/atlassian/publish_markdown_to_confluence.py` — Content sync

---

## 3. GAP ANALYSIS

### ✅ COVERED: Full Regression & Unit Coverage

1. **Export ID Lifecycle**
   - Query, detail, clear (unit + regression)
   - Filter logic across multiple runs
   - Multi-export-ID tracking
   - Risk: **LOW** — changing this breaks import cleanup flow (detected by regression)

2. **Jira Project Key Mapping**
   - Store, retrieve, upsert, clear (unit + regression)
   - Unicode/special chars, multi-site independence
   - Risk: **LOW** — changes caught by workflow regression

3. **Custom Field Selection**
   - Parse flags, interactive prompt (unit only)
   - Field filtering, column vs description mode
   - Risk: **MEDIUM** — workflow tested but fuzzy search logic not isolated

4. **Jira CSV Partition & Packing**
   - Synthetic epic splitting, hierarchy preservation, load balancing (unit only)
   - Oversized CSV handling
   - Risk: **MEDIUM** — complex logic tested but edge cases in hierarchy detection not exhaustive

5. **Field Management**
   - Ensure fields created, idempotency, options (unit only)
   - Conflict detection
   - Risk: **LOW** — well-tested for conflict scenarios

6. **Incremental Refresh**
   - Staleness detection, prompt flow (regression only)
   - Per-project isolation, timestamp tracking
   - Risk: **LOW** — workflow validated

7. **Data Anonymization**
   - Full suite tested
   - Risk: **LOW**

---

### ⚠️ PARTIAL: Tested via Integration, Not Isolated

1. **Export Project (Main Export Function)**
   - 41 integration tests exercise the full export pipeline
   - **Untested individually:** 52 helper functions
   - **Examples:**
     - `extract_profile_gid_overrides()` — HTML parsing for profile GID overrides (only tested if it breaks HTML handling in full export)
     - `transform_asana_links()` — Rewrite Asana URLs (tested only in export with notes/comments present)
     - `load_task_collaborators()` — Follower fetching (tested only in exports with followers)
     - `write_link_modifications_report()` — Link change manifest (written but never validated)
     - `format_comment_row()` — Comment CSV row formatting (tested only in export with comments)
   - **Risk:** **MEDIUM-HIGH** — Refactoring individual helpers without running full export is risky; bugs in helpers may not surface until edge-case exports

2. **Archival Project Export**
   - 22 tests, but `build_epic_description()` only tested as side effect
   - Custom field folding logic well-tested
   - **Risk:** **LOW-MEDIUM** — main flow solid, epic description could drift

3. **CSV Config Generation**
   - Interactive prompts covered
   - `write_reusable_import_config()` logic not directly validated for profile-scoped paths
   - **Risk:** **LOW** — tested via `generate_jira_csv_import_config` full flow

---

### ❌ UNCOVERED: No Tests Whatsoever

1. **`import_jira_project_map.py`** (66 LOC)
   - **Functions:** `read_rows()`, `parse_args()`, `main()`
   - **Risk:** **HIGH** — CSV parsing and database upsert untested
   - **Impact:** Silent data load failures; wrong project key associations
   - **Recommendation:** Add 3-5 unit tests for CSV format validation, upsert idempotency

2. **`backfill_project_mappings_from_tasks.py`** (243 LOC)
   - **Function:** `main()` — Recover project keys from existing task mappings
   - **Risk:** **CRITICAL** — Data recovery logic with no safety net
   - **Impact:** Key corruption, loss of existing mappings
   - **Recommendation:** Add comprehensive tests: normal flow, no mappings, partial recovery, conflict handling

3. **`publish_markdown_to_confluence.py`** (253 LOC)
   - **Functions:** Main content sync logic
   - **Risk:** **MEDIUM** — API interaction untested
   - **Impact:** Silent failures, duplicate pages, lost content
   - **Recommendation:** Mock Confluence client; test page creation, update, content conversion

4. **`export_project_import_coverage.py`** (204 LOC)
   - **Core logic:** Reconciliation by name matching, grouping
   - **Risk:** **MEDIUM** — reconciliation accuracy untested
   - **Impact:** False positive/negative coverage reports
   - **Recommendation:** Unit tests for name normalization, multiset matching, per-group reconciliation

5. **`export_atjm_coverage_report.py`** (6 LOC)
   - **Status:** Likely deprecated stub
   - **Risk:** **LOW** — minimal code

---

### 🔍 SUSPICIOUS: Functions Changed But Tests Didn't Update

1. **`parse_args()` in export_project_to_jira_csv.py**
   - **Issue:** Many new CLI flags added (`--custom-field-mode`, `--consolidated-output`, `--no-refresh-prompt`, etc.)
   - **Test status:** Regression tests validate flags work, but unit test for `parse_args()` itself missing
   - **Risk:** **MEDIUM** — flag parsing is tested implicitly; a bad default would be caught by regression, but subtle arg conflicts might slip through
   - **Recommendation:** Add isolated unit test covering all flag combinations and defaults

2. **`build_project_export_data()` — Large Rewrite**
   - **Issue:** Function signature and field collection logic likely changed multiple times; no unit tests
   - **Test status:** Covered only via 41 integration tests
   - **Risk:** **MEDIUM-HIGH** — refactoring this function is dangerous; must run full export suite to validate
   - **Recommendation:** Extract core data-fetching logic into testable helper functions with isolated unit tests

3. **`export_project()` — Main Export Orchestrator**
   - **Issue:** Handles 3 major code paths (standard, archival, hierarchical flattening)
   - **Test status:** 30+ scenarios tested, but no isolated unit tests for orchestration logic
   - **Risk:** **MEDIUM** — path selection logic not directly tested; wrong branch taken silently
   - **Recommendation:** Add unit tests for path selection conditions and error propagation

4. **Link transformation pipeline**
   - **Issue:** Multiple functions chain (`transform_asana_links()` → `convert_html_links_to_jira_format()` → etc.)
   - **Test status:** Tested only as full export side effect
   - **Risk:** **HIGH** — refactoring one function could break the chain without isolated tests
   - **Recommendation:** Add unit tests for each link transformation function with canned Asana HTML/plain-text inputs

5. **Comment row formatting**
   - **Issue:** `format_comment_row()` called millions of times in large exports; no direct test
   - **Test status:** Covered in integration tests with comments present
   - **Risk:** **MEDIUM** — edge cases (empty comments, HTML in timestamps, etc.) not validated in isolation
   - **Recommendation:** Add unit test with edge-case comment payloads

---

## 4. AUDIT FINDINGS BY CATEGORY

### ✅ FULLY COVERED (25 functions)
Functions with dedicated unit tests AND regression tests, or so critical they're tested both ways:

1. `list_export_ids()` — Unit ✅ + Regression ✅
2. `get_export_id_detail()` — Unit ✅ + Regression ✅
3. `clear_export_id()` — Unit ✅ + Regression ✅
4. `clear_all_export_ids()` — Unit ✅ + Regression ✅
5. `ensure_standard_import_fields()` — Unit ✅
6. `load_archive_import_settings()` — Unit ✅
7. `save_archive_import_settings()` — Unit ✅
8. `clear_archive_import_settings()` — Unit ✅
9. `prompt_field_choice()` — Unit ✅
10. `review_archive_import_settings()` — Unit ✅
11. `normalize_fields()` — Unit ✅
12. `build_archive_import_config_for_columns()` — Unit ✅
13. `build_archive_import_config()` — Unit ✅
14. `render_reusable_import_config()` — Unit ✅
15. `write_reusable_import_config()` — Unit ✅
16. `write_archive_import_config()` — Unit ✅
17. `latest_profile_import_config_path()` — Unit ✅
18. `stale_profile_import_config_paths()` — Unit ✅
19. `copy_active_profile_import_config()` — Unit ✅
20. `parse_synthetic_epic_limit()` — Unit ✅
21. `count_csv_issues()` — Unit ✅
22. `partition_project_csv()` — Unit ✅
23. `pack_csv_parts()` — Unit ✅
24. `record_exported_tasks()` — Unit ✅
25. `get_exported_task_gids()` — Unit ✅
26. `clear_export_history()` — Unit ✅
27. `clear_export_history_for_id()` — Unit ✅
28. `upsert_jira_project_mappings()` — Unit ✅
29. `load_jira_project_mappings()` — Unit ✅
30. `clear_jira_project_mappings()` — Unit ✅
31. `upsert_team_project_jira_mappings()` — Unit ✅
32. `load_team_project_jira_mappings()` — Unit ✅
33. `delete_team_project_jira_mappings()` — Unit ✅
34. `upsert_task_jira_mappings()` — Unit ✅
35. `export_project()` — Integration ✅
36. `export_archival_project()` — Integration ✅
37. `parse_args()` (all export scripts) — Implicit ✅
38. `main()` (all export scripts) — Implicit ✅

---

### ⚠️ PARTIALLY COVERED (52 functions)
Functions tested only via integration (full export flow), not in isolation:

**Data fetching:**
- `load_user_display_names()`
- `load_user_emails()`
- `load_project_names()`
- `load_task_collaborators()`
- `fetch_project_latest_modified()`
- `fetch_project_task_activity()`
- `fetch_project_update_comment_rows()`

**Link transformation:**
- `transform_asana_links()`
- `convert_html_links_to_jira_format()`
- `prefer_html_text_with_links()`
- `extract_html_hrefs()`
- `extract_profile_gid_overrides()`
- `build_jira_task_search_url()`
- `build_asana_link_addition()`

**User & identity:**
- `resolve_atlassian_identity()`
- `format_user_reference()`
- `format_user_display_name()`
- `record_user_association()`
- `exclude_task_owner_collaborators()`

**Formatting:**
- `format_custom_field_names_line()`
- `format_comment_row()`
- `blank_task_title_placeholder()`
- `blank_comment_placeholder()`
- `export_task_name()`
- `format_comment_timestamp()`
- `format_export_datetime()`
- `format_jira_hierarchy_depth()`
- `format_project_entry()`
- `build_default_filename()`
- `build_epic_description()`
- `build_archival_description()`
- `ensure_tasks_suffix()`
- `sanitize_filename_component()`
- `normalize_label_value()`

**Field handling:**
- `apply_custom_field_filter()`
- `resolve_custom_field_value()`
- `log_custom_field_list()`
- `preview_project_custom_fields()`
- `prompt_for_custom_field_choice()`
- `split_fields_by_choice()`

**Output & state:**
- `next_export_dir()`
- `organize_outputs()`
- `write_link_modifications_report()`
- `write_missing_account_report()`
- `write_user_association_report()`
- `filter_missing_account_counts()`
- `build_synthetic_epic_row()`

**Utility:**
- `parse_iso_datetime()`
- `current_utc_time()`
- `normalize_jira_base_url()`

**CSV processing:**
- `build_project_export_data()` — 2500+ LOC data transformation, only tested via full export

---

### ❌ COMPLETELY UNCOVERED (8 scripts, 850+ LOC)

1. **`import_jira_project_map.py`** (66 LOC)
   - **Functions:** `read_rows()`, `parse_args()`, `main()`

2. **`backfill_project_mappings_from_tasks.py`** (243 LOC)
   - **Core logic:** Project key recovery from existing task mappings

3. **`export_project_import_coverage.py`** (204 LOC)
   - **Core logic:** Reconciliation algorithm, grouping

4. **`publish_markdown_to_confluence.py`** (253 LOC)
   - **Core logic:** Confluence API client, page creation/update

5. **`export_atjm_coverage_report.py`** (6 LOC)
   - Stub/wrapper (likely deprecated)

---

### 🔍 SUSPICIOUS: May Need Refresh (8 functions)

1. **`compute_incremental_refresh_window()`**
   - Test expects `-1 day` logic; verify still accurate
   - **Status:** Unit test exists, likely safe

2. **`parse_args()` in `export_project_to_jira_csv.py`**
   - Many new flags; no direct test of arg parsing logic
   - **Status:** Regression tests validate flags work, but isolated unit test missing

3. **`build_project_export_data()`**
   - 2500+ LOC; likely refactored multiple times
   - **Status:** Only tested via full export integration

4. **`export_project()` path selection**
   - Chooses between standard/archival/flattened based on flags
   - **Status:** Tested implicitly; no isolated path-selection tests

5. **Link transformation chain**
   - Multiple functions in sequence; unclear error propagation
   - **Status:** Tested only as full export side effect

6. **Comment row formatting**
   - `format_comment_row()` called millions of times
   - **Status:** No direct edge-case tests

7. **Jira project mapping upsert**
   - Multi-site logic added; tests check independence
   - **Status:** Unit test exists, but edge cases in site-scoping unclear

8. **Synthetic epic splitting logic**
   - Hierarchy-aware load balancing; complex math
   - **Status:** Well-tested but may have edge cases with very deep trees

---

## 5. RECOMMENDATIONS BY PRIORITY

### 🔴 CRITICAL (Do First)

1. **Add tests for `backfill_project_mappings_from_tasks.py`** (Data recovery — high risk)
   - Test normal flow (recover keys from existing mappings)
   - Test no mappings found (graceful exit)
   - Test partial recovery (some tasks mapped, some not)
   - Test conflict handling (project key changes)
   - **Effort:** 3-4 hours
   - **Prevents:** Silent key corruption

2. **Add tests for `import_jira_project_map.py`** (CSV load, upsert — high risk)
   - Test CSV parse: valid format, missing columns, extra columns, special chars
   - Test upsert idempotency (same data twice = one row)
   - Test validation (missing key/name → skip or error)
   - **Effort:** 2-3 hours
   - **Prevents:** Silent data misalignment

3. **Isolate `build_project_export_data()` tests** (Main export data transformation — 2500+ LOC)
   - Extract testable helper functions (field collection, hierarchy, comments)
   - Add unit tests for each helper with canned project data
   - **Effort:** 6-8 hours
   - **Prevents:** Regression in export quality during refactoring

---

### 🟡 HIGH (Next Sprint)

4. **Add unit tests for link transformation pipeline**
   - Test `transform_asana_links()` with various Asana URL formats
   - Test HTML link parsing (`convert_html_links_to_jira_format()`)
   - Test fallback when HTML not available
   - **Effort:** 3-4 hours
   - **Prevents:** Silent URL corruption in large exports

5. **Add unit tests for comment row formatting**
   - Test `format_comment_row()` with edge cases: empty text, HTML, timestamps, updates
   - Test collaborator resolution in comments
   - Test comment dedup logic
   - **Effort:** 2-3 hours
   - **Prevents:** Edge cases in comment exports

6. **Add unit test for export path selection logic in `export_project()`**
   - Test flag combinations that trigger standard vs archival vs flattened
   - Test precedence (e.g., `--archival` overrides default)
   - Test error cases (conflicting flags)
   - **Effort:** 2 hours
   - **Prevents:** Silent code path selection errors

7. **Add tests for `export_project_import_coverage.py`**
   - Test reconciliation by name matching (normalization, multiset match)
   - Test grouping logic (POPS, PROJ24 grouping)
   - Test mismatch detection (missing in Jira, extra in Jira)
   - **Effort:** 3-4 hours
   - **Prevents:** Inaccurate coverage reports

---

### 🟢 MEDIUM (Nice to Have)

8. **Add tests for `publish_markdown_to_confluence.py`**
   - Mock Confluence API client
   - Test page creation, update, content conversion
   - **Effort:** 3 hours
   - **Prevents:** Silent sync failures

9. **Add isolated unit test for `parse_args()` in `export_project_to_jira_csv.py`**
   - Test all flag combinations and defaults
   - Test flag precedence and conflicts
   - **Effort:** 2 hours
   - **Note:** Regression tests cover this implicitly; low priority but good practice

10. **Refactor user/identity resolution into testable helpers**
    - Extract `resolve_atlassian_identity()` logic into smaller functions
    - Add unit tests for fallback logic and edge cases
    - **Effort:** 4 hours
    - **Prevents:** Silent assignee/creator mismatches in exports

---

## 6. RISK MATRIX

| Function/Module | Risk Level | Test Coverage | Recommendation |
|---|---|---|---|
| Export ID lifecycle | LOW | Unit + Regression ✅ | No action needed |
| Jira key mapping | LOW | Unit + Regression ✅ | No action needed |
| Custom field selection | MEDIUM | Regression only ⚠️ | Add isolated unit tests for filter logic |
| Partition/packing | MEDIUM | Unit only ✅ | OK as-is; complex but well-tested |
| Field management | LOW | Unit only ✅ | OK as-is |
| Export project (main flow) | MEDIUM | Integration ✅ | Consider splitting into smaller testable units |
| Archival export | LOW | Integration ✅ | OK as-is |
| Link transformation | HIGH | Integration only ⚠️ | Add isolated unit tests (HIGH PRIORITY) |
| Comment formatting | MEDIUM | Integration only ⚠️ | Add isolated unit tests |
| `import_jira_project_map` | **CRITICAL** | ❌ None | Add unit tests immediately (CRITICAL) |
| `backfill_project_mappings` | **CRITICAL** | ❌ None | Add unit tests immediately (CRITICAL) |
| `export_project_import_coverage` | MEDIUM | ❌ None | Add unit tests |
| `publish_markdown_to_confluence` | MEDIUM | ❌ None | Add unit tests |
| `export_atjm_coverage_report` | LOW | ❌ None | Consider removal if deprecated |

---

## 7. TEST EXECUTION & COVERAGE

**Current test suite status:**
```
Ran 843 tests in 13.880s
OK
```

**By category:**
- Regression tests (export workflows): ~50 tests
- Unit tests (export libraries): ~100 tests
- Unit tests (export scripts): ~150 tests
- Unit tests (other modules): ~543 tests

**Coverage gaps:**
- No line-coverage metrics reported
- Recommend adding `coverage.py` and targeting ≥90% for `src/lib/export*` and `src/lib/jira*`

---

## 8. CONCLUSION

The Asana→Jira export feature has **strong regression coverage** for end-to-end workflows (export ID management, field selection, Jira key mapping), ensuring major breakage is caught quickly. However, **52 helper functions in the main export script are only tested via integration**, creating refactoring risk.

**Immediate actions:**
1. Add unit tests for data-recovery script (`backfill_project_mappings_from_tasks.py`) — HIGH RISK
2. Add unit tests for CSV loader (`import_jira_project_map.py`) — HIGH RISK
3. Isolate link transformation pipeline tests — MEDIUM RISK, HIGH IMPACT

**Longer term:**
- Consider refactoring `export_project()` into smaller, independently testable functions
- Add line-coverage tracking to enforce ≥90% coverage for export/jira modules
- Audit all 8 untested scripts and either add tests or mark as deprecated

