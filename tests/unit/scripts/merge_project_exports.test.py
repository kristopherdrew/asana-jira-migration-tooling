from __future__ import annotations

import csv
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from scripts.merge_project_exports import canonicalize_headers, merge_csv_files, merge_field_counts, merge_link_mods


class MergeProjectExportsTest(TestCase):
    def test_merge_preserves_duplicate_headers_and_source(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            file_a = root / "projA-20250101.csv"
            file_b = root / "projB-20250101.csv"

            file_a.write_text(
                "Task,Comment,Comment\n"
                "A1,first,second\n",
                encoding="utf-8",
            )
            file_b.write_text(
                "Task,Summary\n"
                "B1,summary text\n",
                encoding="utf-8",
            )

            output = root / "merged.csv"
            merge_csv_files([file_a, file_b], output, source_column="source", strip_suffix="-20250101")

            with output.open("r", newline="", encoding="utf-8") as handle:
                rows = list(csv.reader(handle))
            header = rows[0]
            self.assertEqual(header, ["Task", "Comment", "Comment", "Summary", "source"])

            # Row from file A retains both comments and source without suffix
            self.assertEqual(rows[1], ["A1", "first", "second", "", "projA"])
            # Row from file B leaves missing columns blank and sets summary + source
            self.assertEqual(rows[2], ["B1", "", "", "summary text", "projB"])

    def test_merge_field_counts_and_link_mods(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            a_fc = root / "projA-20250101_tasks_field_counts.csv"
            b_fc = root / "projB-20250101_tasks_field_counts.csv"
            a_fc.write_text("Field,Count\nAssignee,5\n", encoding="utf-8")
            b_fc.write_text("Field,Count\nTags,3\n", encoding="utf-8")

            merged_fc = root / "merged_field_counts.csv"
            merge_field_counts([a_fc, b_fc], merged_fc, source_column="source", strip_suffix=None)
            with merged_fc.open("r", newline="", encoding="utf-8") as handle:
                rows = list(csv.reader(handle))
            self.assertEqual(rows[0], ["Field", "Count", "source"])
            self.assertEqual(rows[1], ["Assignee", "5", "projA-20250101"])
            self.assertEqual(rows[2], ["Tags", "3", "projB-20250101"])

            a_lm = root / "projA-20250101_tasks_link_modifications.txt"
            b_lm = root / "projB-20250101_tasks_link_modifications.txt"
            a_lm.write_text("task_gid: 1\nfield: notes\n", encoding="utf-8")
            b_lm.write_text("task_gid: 2\nfield: comment\n", encoding="utf-8")
            merged_lm = root / "merged_link_modifications.csv"
            merge_link_mods([a_lm, b_lm], merged_lm, source_column="src", strip_suffix=None)
            with merged_lm.open("r", newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[0]["task_gid"], "1")
            self.assertEqual(rows[0]["field"], "notes")
            self.assertEqual(rows[0]["src"], "projA-20250101")
            self.assertEqual(rows[1]["task_gid"], "2")
            self.assertEqual(rows[1]["field"], "comment")
            self.assertEqual(rows[1]["src"], "projB-20250101")
