# 🧭 Project Brief: Asana Data Export & Transformation for Jira Import

**Date:** 2025-11-04

---

## 1. Project Goal
Maintain a modular Python pipeline that extracts critical Asana workspace data, normalizes it into a local SQLite database, and produces Jira-ready CSV exports. The toolkit enables reproducible, audit-friendly migrations or archival pulls while supporting incremental refreshes.

---

## 2. Scope & Objectives

**In Scope:**
- Capture teams, projects, users, custom fields, tasks, comments, and attachments required for Jira migrations.
- Preserve full task/subtask hierarchy via `parent_gid` references, fetching subtasks recursively.
- Support incremental refreshes using `modified_at` windows.
- Produce normalized records in SQLite and project-scoped Jira CSV outputs.
- Run all scripts from the repo root with `python3 -m scripts.<name>` to guarantee package imports resolve.

**Out of Scope:**
- Direct automation of Jira imports (handled manually after CSV generation).
- Cross-workspace deduplication or merge logic.
- Real-time synchronization; the workflow remains batch oriented.

---

## 3. Primary Deliverables
1. **SQLite schema** capturing teams, projects, users, custom fields, tasks, relationships, comments, and attachments.
2. **Python CLI scripts** (7 core + 1 optional incremental refresher) for ingestion and export.
3. **Jira CSV exporter** that pivots custom fields and emits one file per project.
4. **Documentation & runbooks** covering execution order, configuration, and outstanding follow-ups.

---

## 4. Key Considerations

| Category | Consideration |
|-----------|----------------|
| **Rate Limits** | Paid Asana domains allow ~1500 requests/min (search calls capped at 60/min). HTTP client honors `Retry-After` with exponential backoff. |
| **Data Volume** | Current workspace est. ~180k tasks across ~2k projects. Scripts segment work by project/team and commit progress every 25 tasks. |
| **Subtasks** | Subtasks inherit project context from parents; exports keep `parent_gid`. Need downstream confirmation on representing hierarchies deeper than one level. |
| **User Mapping** | Users are stored with email addresses to aid manual Jira account reconciliation. Unknown users discovered mid-sync are backfilled automatically. |
| **Custom Fields** | Global definitions live in `custom_fields`; project associations are recorded via `project_custom_field_settings` and refreshed per run. |
| **Incremental Updates** | `get_incremental_updates.py` sweeps by `modified_since`, replaying task + extras sync for entities touched within the requested window. |
| **Recovery** | Upserts are idempotent; rerunning a scope safely reconciles state without separate checkpoint tables. Logs stream to stdout for capture when needed. |

---

## 5. Complications & Mitigations

| Complication | Mitigation |
|---------------|-------------|
| Deeper subtask nesting | Fetch logic handles arbitrary depth; Jira export retains `parent_gid`. Pending decision on flattening for >1 sub-level. |
| Large comment/attachment volumes | Paginated fetches with commit intervals and retry-aware HTTP client prevent losing progress mid-run. |
| Rate-limit bursts | Shared client inspects `Retry-After` headers and backs off using exponential jitter. |
| User mismatches between Asana & Atlassian | Maintain authoritative user directory in SQLite; provide CSV slices for manual reconciliation before Jira load. |
| Field variability per project | Store per-project custom field settings to drive accurate CSV pivots; missing definitions trigger on-demand fetches. |

---

## 6. Expected Outputs

1. **Normalized SQLite database** at `data/asana.sqlite` containing all synced entities.
2. **Project-scoped Jira CSV** generated via `python3 -m scripts.export_project_to_jira_csv --project-gid <gid>` with custom field columns expanded.
3. **Console logs** (INFO by default, DEBUG when requested) summarizing API usage, pagination progress, and retry events.
4. **Incremental refresh summaries** detailing analyzed, created, updated, and deleted artifacts when running delta syncs.

---

## 7. Script Inventory & Functional Breakdown

| # | Script Name | Purpose | Detailed Expectations |
|---|--------------|----------|------------------------|
| **1** | `get_teams.py` | Retrieve workspace teams | Populates `teams` and `team_members` tables; optional memberships gated by CLI flags. |
| **2** | `get_projects.py` | Fetch project metadata | Stores core project fields plus `project_custom_field_settings` for downstream pivots. |
| **3** | `get_users.py` | Build user directory | Captures `gid`, `name`, `email`, `is_active`, and workspace memberships for later joins. |
| **4** | `get_custom_fields.py` | Sync custom field definitions | Persists metadata and enum catalogs; supplements project syncs discovering new field IDs. |
| **5** | `get_tasks.py` | Sync tasks and subtasks | Merges task listings with detail fetches, backfills users/custom fields, and writes normalized relationship tables. |
| **6** | `get_task_extras.py` | Sync comments & attachments | Re-fetches stories/attachments per task, marking missing rows as deleted while preserving authored metadata. |
| **7** | `export_project_to_jira_csv.py` | Produce Jira-ready CSV | Pivots custom fields, includes subtasks as rows, and outputs to `exports/` with optional filename overrides. |
| **8 (optional)** | `get_incremental_updates.py` | Delta refresh pipeline | Replays task + extras sync for items modified within the requested timeframe and surfaces per-team summaries. |

All scripts honor shared flags (`--project-gid`, `--team`, `--team-gids`, `--all-teams`, `--exclude-team-gids`, `--workers`, `--log-level`) and expect to be run via `python3 -m scripts.<module>`.

---

## 8. Execution Order

1. `python3 -m scripts.get_teams`
2. `python3 -m scripts.get_projects`
3. `python3 -m scripts.get_users`
4. `python3 -m scripts.get_custom_fields`
5. `python3 -m scripts.get_tasks`
6. `python3 -m scripts.get_task_extras`
7. `python3 -m scripts.export_project_to_jira_csv`
8. *(Optional)* `python3 -m scripts.get_incremental_updates`

Runs can be repeated as needed; upserts keep the database current without truncation.

---

## 9. Next Steps & Open Questions

1. ✅ Confirmed: schema coverage, retry strategy, credential handling, and export conventions.
2. ✅ README & runbook now document module-based execution and script options.
3. 🔲 Determine Jira import expectations for multi-level subtasks (flatten vs. retain hierarchy) and adjust export logic accordingly.
4. 🔲 Capture repeatable process for reconciling unmatched user emails prior to Jira loads (document workflow once finalized).

---
