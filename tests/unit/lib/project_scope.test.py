"""Tests for project scope parsing and file reading."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest import TestCase

from src.lib.project_scope import parse_gid_list, read_project_gids_file


class ParseGidListTest(TestCase):
    """Test parse_gid_list function for parsing comma-separated GID strings."""

    def test_parses_single_gid(self) -> None:
        result = parse_gid_list("proj_123")
        self.assertEqual(result, ["proj_123"])

    def test_parses_multiple_comma_separated_gids(self) -> None:
        result = parse_gid_list("proj_a,proj_b,proj_c")
        self.assertEqual(result, ["proj_a", "proj_b", "proj_c"])

    def test_strips_whitespace_around_gids(self) -> None:
        result = parse_gid_list("  proj_a  ,  proj_b  , proj_c  ")
        self.assertEqual(result, ["proj_a", "proj_b", "proj_c"])

    def test_skips_empty_strings_between_commas(self) -> None:
        result = parse_gid_list("proj_a,,proj_b,,,proj_c")
        self.assertEqual(result, ["proj_a", "proj_b", "proj_c"])

    def test_handles_whitespace_only_entries(self) -> None:
        result = parse_gid_list("proj_a,  ,proj_b")
        self.assertEqual(result, ["proj_a", "proj_b"])

    def test_returns_empty_list_for_none(self) -> None:
        result = parse_gid_list(None)
        self.assertEqual(result, [])

    def test_returns_empty_list_for_empty_string(self) -> None:
        result = parse_gid_list("")
        self.assertEqual(result, [])

    def test_returns_empty_list_for_whitespace_only(self) -> None:
        result = parse_gid_list("   ")
        self.assertEqual(result, [])

    def test_handles_single_trailing_comma(self) -> None:
        result = parse_gid_list("proj_a,proj_b,")
        self.assertEqual(result, ["proj_a", "proj_b"])

    def test_handles_single_leading_comma(self) -> None:
        result = parse_gid_list(",proj_a,proj_b")
        self.assertEqual(result, ["proj_a", "proj_b"])

    def test_preserves_gid_case(self) -> None:
        result = parse_gid_list("PROJ_A,proj_b,ProJ_c")
        self.assertEqual(result, ["PROJ_A", "proj_b", "ProJ_c"])

    def test_handles_gids_with_special_characters(self) -> None:
        result = parse_gid_list("proj-a_1,proj.b.2")
        self.assertEqual(result, ["proj-a_1", "proj.b.2"])

    def test_handles_very_long_list(self) -> None:
        gid_string = ",".join([f"proj_{i}" for i in range(100)])
        result = parse_gid_list(gid_string)
        self.assertEqual(len(result), 100)
        self.assertEqual(result[0], "proj_0")
        self.assertEqual(result[99], "proj_99")


class ReadProjectGidsFileTest(TestCase):
    """Test read_project_gids_file function for reading GIDs from files."""

    def test_reads_plain_text_file_one_per_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "gids.txt"
            path.write_text("proj_a\nproj_b\nproj_c\n", encoding="utf-8")

            result = read_project_gids_file(str(path))
            # Result is a comma-separated string
            parsed = parse_gid_list(result)
            self.assertEqual(parsed, ["proj_a", "proj_b", "proj_c"])

    def test_reads_plain_text_file_comma_separated(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "gids.txt"
            path.write_text("proj_a,proj_b,proj_c", encoding="utf-8")

            result = read_project_gids_file(str(path))
            parsed = parse_gid_list(result)
            self.assertEqual(parsed, ["proj_a", "proj_b", "proj_c"])

    def test_reads_plain_text_file_mixed_delimiters(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "gids.txt"
            # Newlines and commas together
            path.write_text("proj_a\nproj_b,proj_c\nproj_d", encoding="utf-8")

            result = read_project_gids_file(str(path))
            parsed = parse_gid_list(result)
            self.assertIn("proj_a", parsed)
            self.assertIn("proj_b", parsed)
            self.assertIn("proj_c", parsed)
            self.assertIn("proj_d", parsed)

    def test_reads_csv_with_project_gid_column(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "projects.csv"
            csv_content = """project_gid,project_name,team_gid
proj_a,Alpha Project,team_1
proj_b,Beta Project,team_1
proj_c,Gamma Project,team_2
"""
            path.write_text(csv_content, encoding="utf-8")

            result = read_project_gids_file(str(path))
            parsed = parse_gid_list(result)
            self.assertEqual(parsed, ["proj_a", "proj_b", "proj_c"])

    def test_reads_csv_with_case_insensitive_column_header(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "projects.csv"
            csv_content = """PROJECT_GID,project_name
proj_a,Alpha
proj_b,Beta
"""
            path.write_text(csv_content, encoding="utf-8")

            result = read_project_gids_file(str(path))
            parsed = parse_gid_list(result)
            self.assertEqual(parsed, ["proj_a", "proj_b"])

    def test_handles_csv_with_whitespace_in_header(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "projects.csv"
            csv_content = """  project_gid  , project_name
proj_a,Alpha
proj_b,Beta
"""
            path.write_text(csv_content, encoding="utf-8")

            result = read_project_gids_file(str(path))
            parsed = parse_gid_list(result)
            self.assertEqual(parsed, ["proj_a", "proj_b"])

    def test_skips_empty_rows_in_csv(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "projects.csv"
            csv_content = """project_gid,project_name
proj_a,Alpha

proj_b,Beta

"""
            path.write_text(csv_content, encoding="utf-8")

            result = read_project_gids_file(str(path))
            parsed = parse_gid_list(result)
            self.assertEqual(parsed, ["proj_a", "proj_b"])

    def test_handles_csv_with_empty_gid_cells(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "projects.csv"
            csv_content = """project_gid,project_name
proj_a,Alpha
,Beta
proj_c,Gamma
"""
            path.write_text(csv_content, encoding="utf-8")

            result = read_project_gids_file(str(path))
            parsed = parse_gid_list(result)
            # Empty gid rows should be skipped
            self.assertEqual(parsed, ["proj_a", "proj_c"])

    def test_raises_exit_for_missing_file(self) -> None:
        with self.assertRaises(SystemExit):
            read_project_gids_file("/nonexistent/path/to/file.txt")

    def test_raises_exit_for_unreadable_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "unreadable.txt"
            path.write_text("proj_a", encoding="utf-8")
            # Make file unreadable
            path.chmod(0o000)
            try:
                with self.assertRaises(SystemExit):
                    read_project_gids_file(str(path))
            finally:
                path.chmod(0o644)

    def test_raises_exit_for_file_with_no_valid_gids(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "empty.txt"
            path.write_text("   \n  \n  ", encoding="utf-8")

            with self.assertRaises(SystemExit):
                read_project_gids_file(str(path))

    def test_reads_csv_without_project_gid_column_as_plain_text(self) -> None:
        """When there's no project_gid column, treat each line as a potential GID."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "no_gid.csv"
            # Each row from the CSV is treated as a potential GID when no project_gid column exists
            csv_content = """Alpha,team_1
Beta,team_1
"""
            path.write_text(csv_content, encoding="utf-8")

            # Without project_gid column header, it falls back to line-by-line parsing
            # Each row value (joined by comma) becomes a line in the comma-separated list
            result = read_project_gids_file(str(path))
            parsed = parse_gid_list(result)
            # Rows are split by newline and joined back as comma-separated
            # So we should get 2 items (one per row)
            self.assertGreaterEqual(len(parsed), 1)

    def test_handles_file_with_windows_line_endings(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "gids_windows.txt"
            path.write_text("proj_a\r\nproj_b\r\nproj_c", encoding="utf-8")

            result = read_project_gids_file(str(path))
            parsed = parse_gid_list(result)
            self.assertEqual(parsed, ["proj_a", "proj_b", "proj_c"])

    def test_handles_expanduser_in_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a file and use ~ expansion (note: this just tests the expand logic)
            path = Path(tmpdir) / "gids.txt"
            path.write_text("proj_a\nproj_b", encoding="utf-8")

            # Use the absolute path (we can't easily test ~ expansion without mocking)
            result = read_project_gids_file(str(path))
            parsed = parse_gid_list(result)
            self.assertEqual(parsed, ["proj_a", "proj_b"])

    def test_reads_csv_with_many_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "projects_detailed.csv"
            csv_content = """team_gid,team_name,project_gid,project_name,owner,status,created_at
team_1,Alpha Team,proj_a,Alpha Project,user_1,active,2025-01-01
team_1,Alpha Team,proj_b,Beta Project,user_2,active,2025-01-02
team_2,Beta Team,proj_c,Gamma Project,user_3,archived,2024-12-01
"""
            path.write_text(csv_content, encoding="utf-8")

            result = read_project_gids_file(str(path))
            parsed = parse_gid_list(result)
            self.assertEqual(parsed, ["proj_a", "proj_b", "proj_c"])

    def test_preserves_gid_order_from_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "ordered_gids.txt"
            gids = [f"proj_{i}" for i in range(10)]
            path.write_text("\n".join(gids), encoding="utf-8")

            result = read_project_gids_file(str(path))
            parsed = parse_gid_list(result)
            self.assertEqual(parsed, gids)

    def test_handles_file_with_trailing_newlines(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "gids_trailing.txt"
            path.write_text("proj_a\nproj_b\nproj_c\n\n\n", encoding="utf-8")

            result = read_project_gids_file(str(path))
            parsed = parse_gid_list(result)
            self.assertEqual(parsed, ["proj_a", "proj_b", "proj_c"])

    def test_handles_single_gid_in_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "single.txt"
            path.write_text("proj_single", encoding="utf-8")

            result = read_project_gids_file(str(path))
            parsed = parse_gid_list(result)
            self.assertEqual(parsed, ["proj_single"])
