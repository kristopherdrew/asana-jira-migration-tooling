from __future__ import annotations

from collections import Counter
from unittest import TestCase

from scripts.atlassian.report_migration_coverage import (
    EXTRA_COLUMNS,
    MISSING_COLUMNS,
    build_asana_group,
    build_workbook_sheets,
    normalize_name,
    parse_group_overrides,
    reconcile_group,
    summarize_missing,
)
from src.lib.database import Database


def _asana_task(name, *, gid="g", group="POPS", project="P", is_subtask=False, completed=False):
    return {
        "group": group,
        "gid": gid,
        "name": name,
        "project": project,
        "is_subtask": is_subtask,
        "completed": completed,
        "created_at": "2026-01-01T00:00:00.000Z",
        "modified_at": "2026-02-01T00:00:00.000Z",
        "parent_gid": "",
        "parent_name": "",
        "assignee_email": "assignee@x.com",
        "creator_email": "creator@x.com",
    }


def _jira_issue(summary, *, key="POPS-1", group="POPS", issue_type="Task", is_subtask=False, status="Done"):
    return {
        "group": group,
        "key": key,
        "summary": summary,
        "issue_type": issue_type,
        "is_subtask": is_subtask,
        "status": status,
    }


class NormalizeNameTest(TestCase):
    def test_trims_collapses_and_casefolds(self) -> None:
        self.assertEqual(normalize_name("  Fix   the  Bug "), "fix the bug")
        self.assertEqual(normalize_name("Fix The Bug"), "fix the bug")

    def test_none_and_blank(self) -> None:
        self.assertEqual(normalize_name(None), "")
        self.assertEqual(normalize_name("   "), "")


class ReconcileGroupTest(TestCase):
    def test_matches_missing_and_extra(self) -> None:
        asana = [
            _asana_task("Alpha", gid="a1"),
            _asana_task("Beta subtask", gid="a2", is_subtask=True),
            _asana_task("Gamma", gid="a3", completed=True),
        ]
        jira = [
            _jira_issue("alpha", key="POPS-1"),   # matches Alpha (normalized)
            _jira_issue("Delta", key="POPS-2"),   # extra
        ]
        matched, missing, extra = reconcile_group(asana, jira)
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0][0]["gid"], "a1")
        self.assertEqual({t["gid"] for t in missing}, {"a2", "a3"})
        self.assertEqual([i["key"] for i in extra], ["POPS-2"])

    def test_duplicate_names_pair_by_count(self) -> None:
        asana = [_asana_task("Dup", gid="a1"), _asana_task("Dup", gid="a2"), _asana_task("Dup", gid="a3")]
        jira = [_jira_issue("Dup", key="POPS-1"), _jira_issue("Dup", key="POPS-2")]
        matched, missing, extra = reconcile_group(asana, jira)
        self.assertEqual(len(matched), 2)
        self.assertEqual(len(missing), 1)  # one unpaired Asana task remains
        self.assertEqual(extra, [])

    def test_empty_sides(self) -> None:
        matched, missing, extra = reconcile_group([], [_jira_issue("Solo")])
        self.assertEqual(matched, [])
        self.assertEqual(missing, [])
        self.assertEqual(len(extra), 1)


class SummarizeMissingTest(TestCase):
    def test_breakdown_by_hierarchy_and_completion(self) -> None:
        missing = [
            _asana_task("a", is_subtask=False, completed=False),
            _asana_task("b", is_subtask=True, completed=False),
            _asana_task("c", is_subtask=True, completed=True),
        ]
        self.assertEqual(
            summarize_missing(missing),
            {"top_level": 1, "subtask": 2, "completed": 1, "open": 2},
        )


class ParseGroupOverridesTest(TestCase):
    def test_defaults_to_builtin_groups(self) -> None:
        groups = parse_group_overrides(None)
        self.assertIn("POPS", groups)
        self.assertEqual(groups["PROJ24"], ["1205436878144436"])
        self.assertEqual(len(groups["POPS"]), 7)

    def test_parses_key_and_names(self) -> None:
        groups = parse_group_overrides(["FOO=Alpha||Beta ", "BAR=Solo"])
        self.assertEqual(groups, {"FOO": ["Alpha", "Beta"], "BAR": ["Solo"]})

    def test_rejects_malformed(self) -> None:
        with self.assertRaises(SystemExit):
            parse_group_overrides(["no-equals-sign"])


class BuildAsanaGroupTest(TestCase):
    def setUp(self) -> None:
        self.database = Database(":memory:")
        self.connection = self.database.connect()
        self.database.initialize(self.connection)
        self.connection.executemany(
            "INSERT INTO projects (gid, name, synced_at) VALUES (?, ?, ?)",
            [("p1", "Alpha Project", "2026-01-01"), ("p2", "Alpha Project", "2026-01-01")],
        )
        self.connection.executemany(
            "INSERT INTO users (gid, name, email, synced_at) VALUES (?, ?, ?, ?)",
            [("u1", "Ann Assignee", "ann@x.com", "2026-01-01"),
             ("u2", "Cara Creator", "cara@x.com", "2026-01-01")],
        )
        # p1: 1 top-level open, 1 subtask completed (child of t1). p2 shares t1 (dedup).
        self.connection.executemany(
            "INSERT INTO tasks (gid, name, completed, hierarchy_depth, parent_gid, "
            "assignee_gid, created_by_gid, created_at, modified_at, synced_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("t1", "Top task", 0, 0, None, "u1", "u2", "2026-01-01T00:00:00Z", "2026-03-01T00:00:00Z", "2026-01-01"),
                ("t2", "Sub task", 1, 1, "t1", None, "u2", "2026-01-02T00:00:00Z", "2026-03-02T00:00:00Z", "2026-01-01"),
            ],
        )
        self.connection.executemany(
            "INSERT INTO task_projects (task_gid, project_gid, synced_at) VALUES (?, ?, ?)",
            [("t1", "p1", "2026-01-01"), ("t2", "p1", "2026-01-01"), ("t1", "p2", "2026-01-01")],
        )
        self.connection.commit()

    def tearDown(self) -> None:
        self.connection.close()

    def test_dedupes_union_and_warns_on_ambiguous_name(self) -> None:
        result = build_asana_group(self.connection, "POPS", ["Alpha Project"])
        # Union deduped by gid: t1 counted once even though it's in p1 and p2.
        self.assertEqual({t["gid"] for t in result["tasks"]}, {"t1", "t2"})
        self.assertTrue(any("2 projects matched" in w for w in result["warnings"]))

    def test_warns_when_name_missing(self) -> None:
        result = build_asana_group(self.connection, "POPS", ["Nope"])
        self.assertEqual(result["tasks"], [])
        self.assertTrue(any("no Asana project matched" in w for w in result["warnings"]))

    def test_matches_trimmed_name(self) -> None:
        result = build_asana_group(self.connection, "POPS", ["  Alpha Project  "])
        self.assertEqual(len(result["tasks"]), 2)

    def test_carries_dates_and_resolved_parent_name(self) -> None:
        result = build_asana_group(self.connection, "POPS", ["Alpha Project"])
        by_gid = {t["gid"]: t for t in result["tasks"]}
        self.assertEqual(by_gid["t1"]["created_at"], "2026-01-01T00:00:00Z")
        self.assertEqual(by_gid["t1"]["modified_at"], "2026-03-01T00:00:00Z")
        self.assertEqual(by_gid["t1"]["parent_name"], "")  # top-level, no parent
        # Subtask resolves its parent's name via the LEFT JOIN.
        self.assertEqual(by_gid["t2"]["parent_gid"], "t1")
        self.assertEqual(by_gid["t2"]["parent_name"], "Top task")

    def test_resolves_assignee_and_creator_emails(self) -> None:
        result = build_asana_group(self.connection, "POPS", ["Alpha Project"])
        by_gid = {t["gid"]: t for t in result["tasks"]}
        self.assertEqual(by_gid["t1"]["assignee_email"], "ann@x.com")
        self.assertEqual(by_gid["t1"]["creator_email"], "cara@x.com")
        # Unassigned task -> blank assignee email, creator still resolves.
        self.assertEqual(by_gid["t2"]["assignee_email"], "")
        self.assertEqual(by_gid["t2"]["creator_email"], "cara@x.com")

    def test_resolves_by_numeric_gid(self) -> None:
        self.connection.execute(
            "INSERT INTO projects (gid, name, synced_at) VALUES (?, ?, ?)",
            ("1205436878144436", "Project 24", "2026-01-01"),
        )
        self.connection.execute(
            "INSERT INTO tasks (gid, name, completed, hierarchy_depth, synced_at) VALUES (?, ?, ?, ?, ?)",
            ("t9", "Solo task", 0, 0, "2026-01-01"),
        )
        self.connection.execute(
            "INSERT INTO task_projects (task_gid, project_gid, synced_at) VALUES (?, ?, ?)",
            ("t9", "1205436878144436", "2026-01-01"),
        )
        self.connection.commit()
        result = build_asana_group(self.connection, "PROJ24", ["1205436878144436"])
        self.assertEqual([t["gid"] for t in result["tasks"]], ["t9"])
        # DB name is used as the source_project label, not the GID.
        self.assertEqual(result["per_project"][0]["project"], "Project 24")
        self.assertEqual(result["warnings"], [])


class BuildWorkbookSheetsTest(TestCase):
    def _group_result(self):
        asana_tasks = [
            _asana_task("Alpha", gid="a1"),
            _asana_task("Beta", gid="a2", is_subtask=True, completed=True),
        ]
        jira_issues = [_jira_issue("alpha", key="POPS-1"), _jira_issue("Extra", key="POPS-9")]
        matched, missing, extra = reconcile_group(asana_tasks, jira_issues)
        return {
            "jira_key": "POPS",
            "asana": {"per_project": [
                {"project": "P", "gid": "p1", "total": 2, "top_level": 1,
                 "subtask": 1, "completed": 1, "open": 1,
                 "names": Counter({"alpha": 1, "beta": 1})}
            ]},
            "tasks": asana_tasks,
            "jira_issues": jira_issues,
            "matched": matched,
            "missing": missing,
            "extra": extra,
        }

    def test_sheet_titles_and_headers(self) -> None:
        sheets = build_workbook_sheets(
            [self._group_result()],
            db_path="/tmp/asana.sqlite",
            jira_base_url="https://example.atlassian.net",
            generated_at="2026-07-31T00:00:00Z",
        )
        titles = [title for title, _ in sheets]
        self.assertEqual(
            titles,
            ["Overview", "Asana Baseline", "Jira Inventory", "Reconciliation Summary",
             "Per-Project Coverage", "Missing in Jira", "Extra in Jira"],
        )
        by_title = dict(sheets)
        self.assertEqual(by_title["Missing in Jira"][0], list(MISSING_COLUMNS))
        self.assertEqual(by_title["Extra in Jira"][0], list(EXTRA_COLUMNS))

    def test_per_project_coverage_matches_names_against_pool(self) -> None:
        sheets = dict(build_workbook_sheets(
            [self._group_result()],
            db_path="/tmp/asana.sqlite",
            jira_base_url="https://example.atlassian.net",
            generated_at="2026-07-31T00:00:00Z",
        ))
        coverage = sheets["Per-Project Coverage"]
        # header + one project row; only "alpha" is in the Jira pool -> 1 of 2 matched.
        data = coverage[1]
        matched_idx = coverage[0].index("matched")
        rate_idx = coverage[0].index("match_rate_pct")
        self.assertEqual(data[matched_idx], 1)
        self.assertEqual(data[rate_idx], 50.0)
