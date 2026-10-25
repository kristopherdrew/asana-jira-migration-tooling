# Export Experience Handover & Two-Phase Architecture

**Date:** 2026-10-25 · **Author:** Kristopher Perez

## Context: State After Revert

Reverted to commit `de10c34` (feat: expose project-level custom fields in Jira team/project export) plus `bab8b44` (feat: discovery: add project data inspection script). This revert discarded 11 newer commits (from today) that introduced problematic foundation and optimization work.

**What was removed:** Commits 1-10 attempted to refactor field-discovery logic and optimize it, but in the process **regressed critical CLI features**:
- Export ID selection
- Field selection  
- Jira Project Key mapping
- Data refresh options

**Why the revert:** Export experience was "completely messed up":
- UX features vanished during the refactor
- Project-level custom fields were never properly integrated into the Epic level
- Rather than debug 11 interdependent commits, reverting to a known-good state with discovery infrastructure intact provides a clean slate

**Database state:** No schema changes in any commit—no cleanup needed. Files already at correct state.

---

## Working Features to Preserve

The following UX/features are currently working (in commits 13 & 12) and **must not regress** during future work:

1. **Export ID Management**
   - Ability to select/manage Export IDs before export
   - Export IDs track which data has been exported and prevent re-imports
   - Status: ✅ Working

2. **Field Selection**
   - Users can choose which fields to include in the export
   - Reduces noise and customizes export to scope
   - Status: ✅ Working

3. **Jira Project Key Association**
   - User specifies which Jira project each Asana export maps to
   - Export is pre-configured and ready for import (no manual mapping needed in Jira importer)
   - Status: ✅ Working

4. **Data Refresh Option**
   - Users can refresh/resync Asana data before exporting
   - Ensures fresh snapshot is exported
   - Status: ✅ Working

5. **Discovery Infrastructure** (commits 13 & 12)
   - `scripts/inspect_project_data.py` — diagnostic tool to catalog project-level custom fields
   - Identifies which Asana projects have custom fields that should be migrated
   - Status: ✅ Working, foundation for next phase

---

## Test Coverage Audit Plan

**Scope:** Comprehensive review of ALL export-related features to identify test coverage gaps

**Approach:**

1. **Feature Inventory** — List every CLI option and UX path in the export commands:
   - `export_team_project_jira_csv.py`
   - `export_project_to_jira_csv.py`
   - Interactive CLI export menu paths

2. **Test Mapping** — For each feature, check if tests exist:
   - Unit tests for business logic
   - Integration tests for CLI arg parsing
   - End-to-end tests for UX workflows

3. **Gap Analysis** — Identify which features lack tests:
   - Export ID selection logic
   - Field selection/filtering
   - Jira key mapping
   - Data refresh workflows
   - Project-level field discovery

4. **Coverage Target** — Ensure ≥90% statement coverage with:
   - All features represented
   - Happy path + edge cases
   - CLI argument parsing verification
   - Error handling

**Why this matters:** Commits 11 & 10 broke features we didn't have tests for. Comprehensive coverage prevents regression.

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

## Success Criteria

**Definition of Done:**

1. ✅ **Comprehensive Test Coverage**
   - All export features documented
   - ≥90% coverage on export-related code
   - All features + edge cases tested
   - Regression tests for Phase 1 & 2 working

2. ✅ **No UX Regression**
   - Export ID selection works end-to-end
   - Field selection works end-to-end
   - Jira key mapping works end-to-end
   - Data refresh option works end-to-end
   - All tests pass before merging

3. ✅ **Two-Phase Architecture**
   - Phase 1: Project/Team hierarchy exports, Epics created (CSV path), keys stored
   - Phase 2: Tasks export with parent keys pre-populated
   - Orchestrator handles full workflow seamlessly
   - Mappings persist across phases

4. ✅ **Code Quality**
   - No untested refactors
   - Each new feature has associated tests
   - Clear separation between Phase 1 and Phase 2 logic
   - Documented decision (CSV vs. API) and why

---

## Next Steps

1. **Review this document** — Confirm you're aligned on two-phase architecture and CSV path
2. **Run comprehensive test audit** — Identify all test gaps
3. **Write regression tests** — Lock in current working behavior
4. **Implement Phase 1 orchestrator** — Export team/project hierarchy, test thoroughly
5. **Implement Phase 2 orchestrator** — Export tasks with parent keys, test thoroughly
6. **Validate end-to-end** — Run full two-phase flow and verify Jira import works

---

## Questions for Discussion

- **CSV vs. API:** Should we start with CSV import path, or go direct to API?
- **Scope:** Should Phase 1 include both Teams and Projects as Epics, or just Projects?
- **Mappings storage:** Should parent key mappings live in SQLite or a file?
- **Refresh option:** Should Phase 2 require data refresh, or is Phase 1's data sufficient?

Add your notes/answers inline or comment here.
