# Export Architecture: Two-Phase Design

**Date:** 2026-10-25 · **Author:** Kristopher Perez

## Test-Protected Features

The following working features are protected by regression tests and will not regress:

1. **Export ID Management** — `tests/regression/export_id_lifecycle.test.py` (8 tests)
2. **Field Selection** — `tests/regression/field_selection_workflow.test.py` (12 tests)
3. **Jira Project Key Mapping** — `tests/regression/jira_key_mapping_workflow.test.py` (8 tests)
4. **Data Refresh Workflow** — `tests/regression/data_refresh_workflow.test.py` (13 tests)
5. **Discovery Infrastructure** — `tests/regression/discovery_integration.test.py` (8 tests)

All feature behavior is codified in tests and locked by pre-commit enforcement. See `docs/test-audit/TEST_MATRIX.md` for complete coverage.

---

## Two-Phase Import Architecture

Current approach was too complex: trying to do everything in one export. New approach separates concerns.

### Phase 1: Project/Team Hierarchy Import
**Goal:** Create Epics (and optionally Team epics) in Jira, get back their created Jira keys.

**Inputs:**
- Asana team/project hierarchy
- Jira Project Key (user-specified)

**Outputs:**
- CSV or direct API creation of Epics
- Jira issue keys for each created Epic/Team
- Stored mapping: Asana Project GID → Jira Epic Key

**Process:**
```
User selects "Export with project-level details"
→ Export team/project hierarchy (with project-level custom fields as Epic description)
→ Create Epics in Jira (via CSV importer OR direct API)
→ Query Jira for created Epic keys
→ Store mappings (project GID → Epic key)
```

### Phase 2: Task Export with Parent Keys
**Goal:** Export tasks with parent Jira keys pre-populated, ready for direct import.

**Inputs:**
- Asana tasks (from project scope)
- Parent Jira Epic keys (from Phase 1 mappings)

**Outputs:**
- Task CSV with `Parent Issue Key` column pre-filled
- Ready for Jira importer—no manual linking

**Process:**
```
User selects "Export tasks for previously imported projects"
→ Resolve parent Jira keys from stored mappings
→ Export tasks with parent keys in CSV
→ User imports directly into Jira
```

### Why Two-Phase?
- **Simpler:** Each phase has one clear job
- **Auditable:** User sees intermediate results (Epic keys) before task import
- **Flexible:** Can re-run tasks multiple times against same Epics
- **Testable:** Each phase's logic is independent and easier to test

---

## Orchestrator Workflow

When user selects "Export with project-level details" in the CLI:

```
1. Prompt for scope: team GID or project GIDs
2. Prompt for Jira site (session-aware)
3. Prompt for Jira project key to map to
4. Prompt for data refresh? (optional)

5. [PHASE 1] 
   → Export team/project hierarchy
   → Display CSV or API result
   → Ask: "Ready to create Epics?" 
   → Create Epics in Jira
   → Query Jira for created keys
   → Store mappings in SQLite
   → Display summary: "Created N Epics, keys stored"

6. [PHASE 2]
   → Export tasks with resolved parent keys
   → Display CSV
   → Ask: "Ready to import tasks?"
   → (User imports into Jira via importer)
```

**Key:** Orchestrator handles the full flow. User sees one clear menu option; internally it's two sequential phases with stored state between them.

---

## CSV vs. API for Epic Creation: Decision Framework

### CSV Import Path
**Pros:**
- Jira's native import handles field mapping, error recovery
- Easier for operators to troubleshoot (see the CSV)
- Can edit/adjust CSV before import
- Standard Jira workflow

**Cons:**
- Extra manual step: export CSV, open Jira importer, upload
- Slower feedback loop
- User must wait for Jira importer to complete

### Direct API Path  
**Pros:**
- Fully orchestrated, end-to-end automation
- Fast—immediate Epic creation and key query
- Keys returned instantly for Phase 2
- No manual importer step

**Cons:**
- Error handling more complex (API failures, field validation)
- Less visibility into failures
- Requires API field mapping (createmeta discovery)
- More code to maintain

### **Recommendation:** Start with **CSV path** (already have export infrastructure, standard Jira workflow, easier to troubleshoot). Optimize to API later if UX feedback demands it.

**Decision Point:** Should we start with CSV or API? This affects how Phase 1 export is structured.

---

## Testing Strategy

### Test-First Approach
**Rule:** Every feature gets a test written BEFORE implementation is considered complete.

### Coverage Targets
- ✅ Phase 1 orchestrator workflow (scope → export → create → map)
- ✅ Phase 2 orchestrator workflow (resolve keys → export tasks)
- ✅ Regression suite for preserved features (Export ID, field selection, etc.)
- ✅ CLI arg parsing (all permutations)
- ✅ Edge cases (empty scope, no mappings, API failures, CSV format)

### Test Structure
```
tests/unit/scripts/
  - export_team_project_jira_csv.test.py (existing, expand)
  - export_project_to_jira_csv.test.py (existing, expand)
  - interactive_cli_team_project_export.test.py (existing, expand)

tests/integration/
  - orchestrator_two_phase_export.test.py (new, end-to-end)
  - parent_key_resolution.test.py (new, Phase 2 logic)
```

### Pre-Implementation Testing
Before making changes to exporter logic, write tests that verify current behavior (regressions tests for the working features).

---

## Implementation Goals for Phase 1 & 2

**Definition of Done:**

1. ✅ **Test Coverage (COMPLETE)**
   - All export features locked by 67 regression tests
   - Pre-commit enforcement prevents regression
   - ≥90% coverage on export-related code
   - Baseline: all working features protected

2. **Phase 1 Orchestrator (TODO)**
   - Export team/project hierarchy CSV with project-level fields
   - Create Epics in Jira (CSV path)
   - Store project GID → Epic key mappings
   - Fully tested and integrated into CLI

3. **Phase 2 Orchestrator (TODO)**
   - Export tasks with parent Jira keys pre-populated (from Phase 1 mappings)
   - Ready for direct import into Jira
   - Fully tested and integrated into CLI

4. **Code Quality**
   - Each new feature has associated tests (test-first)
   - Clear separation between Phase 1 and Phase 2 logic
   - No untested refactors (pre-commit hook enforces this)

---


## Questions for Discussion

- **CSV vs. API:** Should we start with CSV import path, or go direct to API?
- **Scope:** Should Phase 1 include both Teams and Projects as Epics, or just Projects?
- **Mappings storage:** Should parent key mappings live in SQLite or a file?
- **Refresh option:** Should Phase 2 require data refresh, or is Phase 1's data sufficient?

Add your notes/answers inline or comment here.
