# Test Coverage Matrix

**Last Updated:** 2025-10-25  
**Total Tests:** 907  
**Coverage:** ✅ All passing

This matrix documents test requirements for all features. New features must meet these requirements before merging.

## Export Features (Core Pipeline)

### Critical Features (100% test coverage required)

| Feature | Unit Tests | Integration | Regression | Location | Status | Notes |
|---------|-----------|-------------|-----------|----------|--------|-------|
| **Export ID Management** | ✅ 4 | ✅ 4 | ✅ 8 | `src/lib/export_id_history.py` | ✅ Complete | Deduplication, history tracking, clearing |
| **Custom Field Selection** | ✅ 12 | ✅ 12 | ✅ 12 | `scripts/export_project_to_jira_csv.py` | ✅ Complete | CLI flags, modes, defaults |
| **Data Refresh Workflow** | ✅ 10 | ✅ 10 | ✅ 13 | `scripts/export_project_to_jira_csv.py` | ✅ Complete | Incremental, staleness, prompts |
| **Jira Key Mapping** | ✅ 8 | ✅ 8 | ✅ 8 | `src/lib/database.py` | ✅ Complete | Store, retrieve, clear, upsert |
| **Custom Field Discovery** | ✅ 6 | ✅ 6 | ✅ 8 | `src/lib/database.py` | ✅ Complete | Per-project isolation, union |
| **Export Project Orchestration** | ✅ 40 | ✅ 40 | ✅ 8 | `scripts/export_project_to_jira_csv.py` | ✅ Complete | ID filtering, hierarchy, epics |
| **Data Collection Pipeline** | ✅ 35 | ✅ 35 | ✅ 11 | `scripts/export_project_to_jira_csv.py` | ✅ Complete | Chunked queries, maps, comments |

**Subtotal: 19 regression tests** ✅

---

### High-Priority Features (≥95% test coverage required)

| Feature | Unit Tests | Integration | Regression | Location | Status | Notes |
|---------|-----------|-------------|-----------|----------|--------|-------|
| **Team/Project Hierarchy** | ✅ 6 | ✅ 6 | ✅ 9 | `scripts/export_team_project_jira_csv.py` | ✅ Complete | Owner fallback, task counts, custom fields |
| **Asana Link Transformation** | ✅ 10 | ✅ 10 | ✅ 17 | `scripts/export_project_to_jira_csv.py` | ✅ Complete | URL detection, Jira syntax, boundaries |
| **Link Addition Building** | ✅ 8 | ✅ 8 | ✅ 0 | `scripts/export_project_to_jira_csv.py` | ⚠️ Partial | Attachments, tasks, profiles, projects |

**Subtotal: 26 regression tests** ✅

---

### Medium-Priority Features (≥90% test coverage required)

| Feature | Unit Tests | Integration | Regression | Location | Status | Notes |
|---------|-----------|-------------|-----------|----------|--------|-------|
| **Custom Field Value Resolution** | ✅ 12 | ✅ 12 | ✅ 10 | `scripts/export_project_to_jira_csv.py` | ✅ Complete | Type dispatch, enums, fallback |
| **Comment Formatting** | ✅ 15 | ✅ 15 | ✅ 12 | `scripts/export_project_to_jira_csv.py` | ✅ Complete | Author tracking, timestamps, HTML |

**Subtotal: 22 regression tests** ✅

---

## Report Features

| Feature | Unit Tests | Regression | Status | Notes |
|---------|-----------|-----------|--------|-------|
| Portfolio export | ✅ 8 | ❌ 0 | ⚠️ Partial | Unit tests only |
| Custom field usage | ✅ 6 | ❌ 0 | ⚠️ Partial | Unit tests only |
| Project status import | ✅ 4 | ❌ 0 | ⚠️ Partial | Unit tests only |
| Team membership export | ✅ 5 | ❌ 0 | ⚠️ Partial | Unit tests only |
| Project details report | ✅ 8 | ❌ 0 | ⚠️ Partial | Unit tests only |

**Status:** Phase 2 planned (next sprint)

---

## API/Sync Scripts

| Feature | Unit Tests | Regression | Status | Notes |
|---------|-----------|-----------|--------|-------|
| get_tasks.py | ✅ 12 | ❌ 0 | ⚠️ Partial | Unit tests only |
| get_task_extras.py | ✅ 10 | ❌ 0 | ⚠️ Partial | Unit tests only |
| get_projects.py | ✅ 8 | ❌ 0 | ⚠️ Partial | Unit tests only |
| get_project_status_updates.py | ✅ 6 | ❌ 0 | ⚠️ Partial | Unit tests only |

**Status:** Phase 3 planned (Q2)

---

## Summary

```
Total Test Coverage:
├── Regression Tests: 67 tests ✅
├── Unit Tests: 334+ tests ✅
└── Total: 907 tests ✅

Coverage by Tier:
├── CRITICAL (7 functions): 19 regression + 100+ unit tests ✅
├── HIGH (3 functions): 26 regression + 50+ unit tests ✅
├── MEDIUM (2 functions): 22 regression + 30+ unit tests ✅
└── Legacy Reports/API: 334+ unit tests ⚠️ (Phase 2+)
```

---

## Testing Requirements by Feature Status

### ✅ Ready for Enhancement (Full Test Coverage)

These features have comprehensive test protection:
- Export ID management → can safely refactor
- Custom field selection → can safely refactor
- Data refresh workflow → can safely refactor
- Jira key mapping → can safely refactor
- Export project orchestration → can safely refactor
- Team/project hierarchy → can safely refactor
- Link transformation → can safely refactor
- Custom field resolution → can safely refactor
- Comment formatting → can safely refactor

**Action:** You can now safely implement enhancements. All regression tests will validate compatibility.

---

### ⚠️ Partial Coverage (Phase 2 & 3)

These features have unit tests but no regression tests:
- Report generation (portfolio, custom fields, project status, etc.)
- API sync scripts (get_tasks, get_projects, etc.)

**Action:** Add regression tests when enhancing these features.

---

## How to Add New Tests

### For New Feature in Critical Function

```bash
# 1. Create test file (if not exists)
touch tests/regression/<feature>.test.py

# 2. Add test class with setup
class MyFeatureTest(TestCase):
    def setUp(self): ...
    def tearDown(self): ...

# 3. Add test methods
    def test_happy_path(self): ...
    def test_edge_case(self): ...

# 4. Run tests
python3 -m unittest discover tests -v

# 5. Ensure all 907 pass before commit
```

### For Bug Fix

```bash
# 1. Write failing test (reproduces bug)
def test_bug_fix_issue_123(self):
    # Arrange
    # Act
    # Assert - should FAIL before fix

# 2. Implement fix

# 3. Verify test passes

# 4. Keep test permanently (prevents regression)
```

---

## Test Execution Quick Reference

```bash
# Run all tests
python3 -m unittest discover tests -v

# Run regression tests only (filter output)
python3 -m unittest discover tests -v 2>&1 | grep regression

# Run single test module
python3 -m unittest tests.regression.export_id_lifecycle -v

# Check coverage
python3 -m trace --count -m unittest discover tests

# Quick feedback during development
python3 -m unittest --failfast
```

---

## Coverage Goals

| Timeframe | Target | Current | Status |
|-----------|--------|---------|--------|
| **Now (Export Core)** | 60+ regression tests | 67 tests | ✅ Met |
| **Next Sprint (Reports)** | 40-50 regression tests | 0 tests | ⏳ In Progress |
| **Q2 (API/Sync)** | 30-40 regression tests | 0 tests | 🗓️ Planned |
| **Overall Goal** | 130-150 regression tests | 67 tests | 🎯 50% Complete |

