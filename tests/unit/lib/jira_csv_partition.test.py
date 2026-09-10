from __future__ import annotations

import argparse
import csv
import tempfile
from pathlib import Path
from unittest import TestCase

from src.lib.jira_csv_partition import (
    CsvPart,
    count_csv_issues,
    pack_csv_parts,
    parse_synthetic_epic_limit,
    partition_project_csv,
)


HEADER = ["task_gid", "External issue ID", "jira_parent_gid", "jira_hierarchy_depth", "task_name"]


def write_rows(path: Path, rows) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADER)
        writer.writerows(rows)


class JiraCsvPartitionTest(TestCase):
    def test_parses_synthetic_epic_limit(self) -> None:
        self.assertEqual(parse_synthetic_epic_limit("250"), 250)
        with self.assertRaisesRegex(argparse.ArgumentTypeError, "between 1 and 7999"):
            parse_synthetic_epic_limit("8000")

    def test_balances_top_level_tasks_across_parts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "tasks.csv"
            write_rows(path, [[f"t{i}", f"t{i}", "", "0", f"Task {i}"] for i in range(10)])
            parts = partition_project_csv(path, limit=6)
            self.assertFalse(path.exists())
            self.assertEqual([part.issue_count for part in parts], [5, 5])
            self.assertTrue(all(count_csv_issues(part.path) <= 6 for part in parts))

    def test_keeps_real_parent_subtrees_together(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "tasks.csv"
            rows = [
                ["root_a", "root_a", "", "0", "Root A"],
                ["child_a", "child_a", "root_a", "1", "Child A"],
                ["root_b", "root_b", "", "0", "Root B"],
                ["child_b", "child_b", "root_b", "1", "Child B"],
                ["root_c", "root_c", "", "0", "Root C"],
                ["child_c", "child_c", "root_c", "1", "Child C"],
            ]
            write_rows(path, rows)
            parts = partition_project_csv(path, limit=4)
            for part in parts:
                with part.path.open("r", newline="", encoding="utf-8") as handle:
                    exported = list(csv.DictReader(handle))
                ids = {row["External issue ID"] for row in exported}
                for row in exported:
                    if row["jira_parent_gid"]:
                        self.assertIn(row["jira_parent_gid"], ids)

    def test_splits_oversized_synthetic_epic_into_part_epics(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "tasks.csv"
            rows = [["epic", "epic", "", "Epic", "Project Epic"]]
            rows.extend([f"t{i}", f"t{i}", "epic", "Task", f"Task {i}"] for i in range(10))
            write_rows(path, rows)
            parts = partition_project_csv(path, limit=6, synthetic_epic_limit=5)
            self.assertEqual([part.issue_count for part in parts], [6, 6])
            for number, part in enumerate(parts, start=1):
                with part.path.open("r", newline="", encoding="utf-8") as handle:
                    exported = list(csv.DictReader(handle))
                epic_id = f"epic::part::{number:03d}"
                self.assertEqual(exported[0]["External issue ID"], epic_id)
                self.assertEqual(exported[0]["task_name"], f"Project Epic — Part {number}")
                self.assertTrue(all(row["jira_parent_gid"] == epic_id for row in exported[1:]))

    def test_balances_1600_tasks_across_two_synthetic_epics(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "tasks.csv"
            rows = [["epic", "epic", "", "Epic", "Project Epic"]]
            rows.extend([f"t{i}", f"t{i}", "epic", "Task", f"Task {i}"] for i in range(1600))
            write_rows(path, rows)

            parts = partition_project_csv(path)

            self.assertEqual(len(parts), 1)
            with path.open("r", newline="", encoding="utf-8") as handle:
                exported = list(csv.DictReader(handle))
            epics = [row for row in exported if row["jira_hierarchy_depth"] == "Epic"]
            self.assertEqual(len(epics), 2)
            child_counts = {
                epic["External issue ID"]: sum(
                    row["jira_parent_gid"] == epic["External issue ID"] for row in exported
                )
                for epic in epics
            }
            self.assertEqual(sorted(child_counts.values()), [800, 800])

    def test_keeps_subtask_families_together_when_splitting_synthetic_epic(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "tasks.csv"
            rows = [["epic", "epic", "", "Epic", "Project Epic"]]
            for number in range(4):
                parent_id = f"parent{number}"
                rows.append([parent_id, parent_id, "epic", "Task", f"Parent {number}"])
                rows.extend(
                    [f"{parent_id}_sub{i}", f"{parent_id}_sub{i}", parent_id, "Sub-task", f"Sub {i}"]
                    for i in range(2)
                )
            write_rows(path, rows)

            partition_project_csv(path, synthetic_epic_limit=6)

            with path.open("r", newline="", encoding="utf-8") as handle:
                exported = list(csv.DictReader(handle))
            by_id = {row["External issue ID"]: row for row in exported}
            for row in exported:
                if row["jira_hierarchy_depth"] == "Sub-task":
                    self.assertIn(row["jira_parent_gid"], by_id)
                    self.assertEqual(by_id[row["jira_parent_gid"]]["jira_hierarchy_depth"], "Task")

    def test_rejects_one_real_hierarchy_larger_than_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "tasks.csv"
            rows = [["root", "root", "", "0", "Root"]]
            rows.extend([f"t{i}", f"t{i}", "root", "1", f"Task {i}"] for i in range(6))
            write_rows(path, rows)
            with self.assertRaisesRegex(SystemExit, "real task hierarchy"):
                partition_project_csv(path, limit=6)
            self.assertFalse(path.exists())

    def test_consolidation_packs_whole_project_parts(self) -> None:
        parts = [CsvPart(Path("a.csv"), 5), CsvPart(Path("b.csv"), 4), CsvPart(Path("c.csv"), 3)]
        batches = pack_csv_parts(parts, limit=7)
        self.assertEqual(sorted(sum(part.issue_count for part in batch) for batch in batches), [5, 7])
        self.assertEqual({part.path.name for batch in batches for part in batch}, {"a.csv", "b.csv", "c.csv"})
