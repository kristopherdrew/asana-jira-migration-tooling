from __future__ import annotations

import re
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from unittest import TestCase

from src.lib.database import Database
from scripts.export_project_import_status import export_project_import_status_workbook

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
SYNC_TIME = datetime(2026, 1, 2, tzinfo=timezone.utc)
SITE_PROD = "production_jira"
SITE_ARCHIVE = "archive_jira"
COLUMN_REF = re.compile(r"[A-Z]+")
SUMMARY_TAB = "Summary"
PROJECTS_TAB = "Projects"
MULTI_SITE_TAB = "Multi-Site"
UNMIGRATED_TAB = "Unmigrated"


def _column_index(ref: str) -> int:
    index = 0
    for char in COLUMN_REF.match(ref).group(0):
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index - 1


def _sheet_names(zf: zipfile.ZipFile) -> list[str]:
    workbook = ET.fromstring(zf.read("xl/workbook.xml"))
    return [sheet.get("name") for sheet in workbook.iter(f"{NS}sheet")]


def _read_sheet(zf: zipfile.ZipFile, name: str) -> list[list[str]]:
    index = _sheet_names(zf).index(name) + 1
    root = ET.fromstring(zf.read(f"xl/worksheets/sheet{index}.xml"))
    rows = []
    for row in root.iter(f"{NS}row"):
        values: list[str] = []
        for cell in row.iter(f"{NS}c"):
            column = _column_index(cell.get("r"))
            while len(values) < column:
                values.append("")
            text_node = cell.find(f".//{NS}t")
            value_node = cell.find(f"{NS}v")
            if text_node is not None:
                values.append(text_node.text or "")
            elif value_node is not None:
                values.append(value_node.text or "")
            else:
                values.append("")
        rows.append(values)
    return rows


def _as_records(rows: list[list[str]]) -> list[dict[str, str]]:
    header, *body = rows
    return [dict(zip(header, row + [""] * (len(header) - len(row)))) for row in body]


class ExportProjectImportStatusWorkbookTest(TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.db_path = str(Path(self.tmpdir.name) / "asana.sqlite")
        self.out_dir = Path(self.tmpdir.name) / "exports"
        self.database = Database(db_path=self.db_path)
        self.connection = self.database.connect()
        self.addCleanup(self.connection.close)
        self.database.initialize(self.connection)

    def _project(self, gid: str, name: str, archived: bool = False, team: str = "team_a") -> None:
        self.database.upsert_project(
            self.connection,
            {"gid": gid, "name": name, "archived": archived, "team": {"gid": team, "name": f"Team {team}"}},
            SYNC_TIME,
        )

    def _task(self, gid: str, project_gids: list[str], deleted: bool = False) -> None:
        self.database.upsert_task(
            self.connection,
            {
                "gid": gid,
                "name": f"Task {gid}",
                "memberships": [{"project": {"gid": project_gid}} for project_gid in project_gids],
            },
            SYNC_TIME,
        )
        if deleted:
            self.connection.execute("UPDATE tasks SET is_deleted = 1 WHERE gid = ?", (gid,))

    def _map_task(self, task_gid: str, jira_key: str, site: str) -> None:
        self.database.upsert_task_jira_mappings(
            self.connection,
            [{"task_gid": task_gid, "jira_key": jira_key}],
            SYNC_TIME,
            jira_site_key=site,
        )

    def _atjm_ticket(self, project_gid: str, jira_key: str, site: str) -> None:
        self.database.upsert_team_project_jira_mappings(
            self.connection,
            [{"kind": "project", "asana_gid": project_gid, "jira_key": jira_key}],
            SYNC_TIME,
            jira_site_key=site,
        )

    def _decision(self, project_gid: str, jira_key: str, decision: str, site: str) -> None:
        self.database.upsert_project_jira_decisions(
            self.connection,
            [{"project_gid": project_gid, "jira_key": jira_key, "decision": decision}],
            SYNC_TIME,
            jira_site_key=site,
        )

    def _run(self, jira_sites=None, project_gids=None) -> zipfile.ZipFile:
        self.connection.commit()
        path = export_project_import_status_workbook(
            database_path=self.db_path,
            output_dir=self.out_dir,
            filename="test.xlsx",
            jira_sites=jira_sites,
            project_gids=project_gids,
        )
        self.assertTrue(path.exists())
        zf = zipfile.ZipFile(path)
        for name in zf.namelist():
            if name.endswith((".xml", ".rels")):
                ET.fromstring(zf.read(name))
        return zf

    def _projects_records(self, zf: zipfile.ZipFile) -> dict[str, dict[str, str]]:
        return {record["Project GID"]: record for record in _as_records(_read_sheet(zf, PROJECTS_TAB))}

    def _summary_row(self, zf: zipfile.ZipFile, site: str) -> dict[str, str]:
        header = _read_sheet(zf, SUMMARY_TAB)[0]
        for row in _read_sheet(zf, SUMMARY_TAB)[1:]:
            if row and row[0] == site:
                return dict(zip(header, row + [""] * (len(header) - len(row))))
        self.fail(f"no summary row for {site}")

    def test_workbook_tabs_follow_selected_sites(self) -> None:
        zf = self._run(jira_sites=[SITE_PROD, SITE_ARCHIVE])
        self.assertEqual(
            _sheet_names(zf),
            [
                SUMMARY_TAB,
                PROJECTS_TAB,
                f"{SITE_PROD} Migrated",
                f"{SITE_ARCHIVE} Migrated",
                MULTI_SITE_TAB,
                UNMIGRATED_TAB,
            ],
        )

    def test_archived_project_is_listed_with_archived_flag(self) -> None:
        self._project("proj_live", "Live Project")
        self._project("proj_old", "Old Project", archived=True)
        zf = self._run()

        records = self._projects_records(zf)

        self.assertEqual(set(records), {"proj_live", "proj_old"})
        self.assertEqual(records["proj_old"]["Archived"], "Yes")
        self.assertEqual(records["proj_live"]["Archived"], "No")

    def test_migrated_column_reflects_selected_site_mappings(self) -> None:
        self._project("proj_mapped", "Mapped")
        self._project("proj_unmapped", "Unmapped")
        self._project("proj_other_site", "Other Site Only")
        self._task("task_1", ["proj_mapped"])
        self._task("task_2", ["proj_other_site"])
        self._map_task("task_1", "PRD-1", SITE_PROD)
        self._map_task("task_2", "ARC-1", SITE_ARCHIVE)
        zf = self._run(jira_sites=[SITE_PROD])

        records = self._projects_records(zf)

        self.assertEqual(records["proj_mapped"]["Migrated"], "Yes")
        self.assertEqual(records["proj_unmapped"]["Migrated"], "No")
        self.assertEqual(records["proj_other_site"]["Migrated"], "No")

    def test_site_tab_lists_only_projects_mapped_to_that_site_with_projects_header(self) -> None:
        self._project("proj_prod", "Prod Project")
        self._project("proj_arc", "Archive Project")
        self._task("task_1", ["proj_prod"])
        self._task("task_2", ["proj_arc"])
        self._map_task("task_1", "PRD-1", SITE_PROD)
        self._map_task("task_2", "ARC-1", SITE_ARCHIVE)
        zf = self._run(jira_sites=[SITE_PROD, SITE_ARCHIVE])

        prod_rows = _read_sheet(zf, f"{SITE_PROD} Migrated")
        header = prod_rows[0]

        self.assertEqual([row[0] for row in prod_rows[1:]], ["proj_prod"])
        self.assertIn(f"{SITE_PROD} Project Key", header)
        self.assertIn(f"{SITE_PROD} %", header)
        self.assertFalse(any(column.startswith(f"{SITE_ARCHIVE} ") for column in header))
        self.assertEqual(header[:5], _read_sheet(zf, PROJECTS_TAB)[0][:5])

    def test_multi_site_project_appears_in_each_site_tab_and_once_on_projects(self) -> None:
        self._project("proj_both", "Both Sites")
        self._task("task_1", ["proj_both"])
        self._task("task_2", ["proj_both"])
        self._map_task("task_1", "PRD-1", SITE_PROD)
        self._map_task("task_2", "ARC-1", SITE_ARCHIVE)
        zf = self._run(jira_sites=[SITE_PROD, SITE_ARCHIVE])

        self.assertEqual([row[0] for row in _read_sheet(zf, f"{SITE_PROD} Migrated")[1:]], ["proj_both"])
        self.assertEqual([row[0] for row in _read_sheet(zf, f"{SITE_ARCHIVE} Migrated")[1:]], ["proj_both"])
        projects = self._projects_records(zf)
        self.assertEqual(projects["proj_both"]["Multi-Site"], "Yes - see Multi-Site tab")

    def test_deleted_tasks_excluded_from_counts_and_mappings(self) -> None:
        self._project("proj_1", "Project One")
        self._task("task_live", ["proj_1"])
        self._task("task_gone", ["proj_1"], deleted=True)
        self._map_task("task_live", "ABC-1", SITE_PROD)
        self._map_task("task_gone", "ABC-2", SITE_PROD)
        zf = self._run(jira_sites=[SITE_PROD])

        record = self._projects_records(zf)["proj_1"]

        self.assertEqual(record["Task Count"], "1")
        self.assertEqual(record[f"{SITE_PROD} Mapped"], "1")
        self.assertEqual(record[f"{SITE_PROD} Project Key"], "ABC")
        self.assertEqual(record[f"{SITE_PROD} %"], "100.0%")

    def test_multiple_keys_in_one_site_are_comma_joined(self) -> None:
        self._project("proj_1", "Project One")
        for task_gid in ("task_1", "task_2", "task_3"):
            self._task(task_gid, ["proj_1"])
        self._map_task("task_1", "ABC-1", SITE_PROD)
        self._map_task("task_2", "XYZ-7", SITE_PROD)
        self._map_task("task_3", "ABC-9", SITE_PROD)
        zf = self._run(jira_sites=[SITE_PROD])

        record = self._projects_records(zf)["proj_1"]

        self.assertEqual(record[f"{SITE_PROD} Project Key"], "ABC, XYZ")
        self.assertEqual(record[f"{SITE_PROD} Mapped"], "3")
        self.assertEqual(record["Multi-Site"], "")

    def test_multi_site_project_appears_per_site_on_multi_site_tab(self) -> None:
        self._project("proj_both", "Both Sites")
        self._task("task_1", ["proj_both"])
        self._task("task_2", ["proj_both"])
        self._map_task("task_1", "PRD-1", SITE_PROD)
        self._map_task("task_2", "ARC-1", SITE_ARCHIVE)
        zf = self._run()

        multi = _as_records(_read_sheet(zf, MULTI_SITE_TAB))

        self.assertEqual(len(multi), 2)
        self.assertEqual({row["Jira Site"] for row in multi}, {SITE_PROD, SITE_ARCHIVE})
        self.assertEqual({row["Project Key(s)"] for row in multi}, {"PRD", "ARC"})
        for row in multi:
            self.assertEqual(row["Mapped"], "1")
            self.assertEqual(row["Task Count"], "2")
            self.assertEqual(row["Mapped %"], "50.0%")

    def test_project_mapped_only_to_unselected_site_is_unmigrated(self) -> None:
        self._project("proj_archive_only", "Archive Only")
        self._task("task_1", ["proj_archive_only"])
        self._map_task("task_1", "ARC-1", SITE_ARCHIVE)
        zf = self._run(jira_sites=[SITE_PROD])

        projects = self._projects_records(zf)
        unmigrated = _as_records(_read_sheet(zf, UNMIGRATED_TAB))

        self.assertEqual(projects["proj_archive_only"][f"{SITE_PROD} Mapped"], "0")
        self.assertEqual(projects["proj_archive_only"][f"{SITE_PROD} %"], "0.0%")
        self.assertEqual([row["Project GID"] for row in unmigrated], ["proj_archive_only"])

    def test_unmigrated_tab_lists_atjm_ticket_decision_and_task_count(self) -> None:
        self._project("proj_none", "No Mapping")
        self._task("task_1", ["proj_none"])
        self._task("task_2", ["proj_none"])
        self._atjm_ticket("proj_none", "ATJM-5", SITE_PROD)
        self._decision("proj_none", "ATJM-5", "Migrate", SITE_PROD)
        zf = self._run(jira_sites=[SITE_PROD])

        unmigrated = _as_records(_read_sheet(zf, UNMIGRATED_TAB))

        self.assertEqual(len(unmigrated), 1)
        row = unmigrated[0]
        self.assertEqual(row["ATJM Ticket"], "ATJM-5")
        self.assertEqual(row["ATJM Migration Decision"], "Migrate")
        self.assertEqual(row["Task Count"], "2")

    def test_zero_task_project_has_blank_percent_without_error(self) -> None:
        self._project("proj_empty", "Empty Project")
        zf = self._run(jira_sites=[SITE_PROD])

        record = self._projects_records(zf)["proj_empty"]

        self.assertEqual(record["Task Count"], "0")
        self.assertEqual(record[f"{SITE_PROD} %"], "")

    def test_project_gids_scope_limits_projects_tab(self) -> None:
        self._project("proj_in", "In Scope")
        self._project("proj_out", "Out of Scope")
        zf = self._run(project_gids=["proj_in"])

        self.assertEqual(set(self._projects_records(zf)), {"proj_in"})

    def test_site_columns_follow_selected_sites_only(self) -> None:
        self._project("proj_1", "Project One")
        self._task("task_1", ["proj_1"])
        self._map_task("task_1", "PRD-1", SITE_PROD)
        zf = self._run(jira_sites=[SITE_PROD])

        header = _read_sheet(zf, PROJECTS_TAB)[0]

        self.assertIn(f"{SITE_PROD} Project Key", header)
        self.assertNotIn(f"{SITE_ARCHIVE} Project Key", header)
        self.assertEqual(header[-1], "Multi-Site")

    def test_summary_counts_cross_listed_task_once_per_site(self) -> None:
        self._project("proj_a", "Project A")
        self._project("proj_b", "Project B")
        self._task("task_shared", ["proj_a", "proj_b"])
        self._task("task_only_a", ["proj_a"])
        self._map_task("task_shared", "PRD-1", SITE_PROD)
        zf = self._run(jira_sites=[SITE_PROD])

        summary = self._summary_row(zf, SITE_PROD)

        self.assertEqual(summary["Asana Projects Migrated"], "2")
        self.assertEqual(summary["Tasks Migrated"], "1")
        self.assertEqual(summary["Keys Mapped"], "1")
        self.assertEqual(summary["Asana Tasks in Migrated Projects"], "2")
        self.assertEqual(summary["% of Asana Tasks Migrated"], "50.0%")

    def test_summary_excludes_deleted_tasks_from_denominator(self) -> None:
        self._project("proj_1", "Project One")
        self._task("task_live", ["proj_1"])
        self._task("task_gone", ["proj_1"], deleted=True)
        self._map_task("task_live", "PRD-1", SITE_PROD)
        zf = self._run(jira_sites=[SITE_PROD])

        summary = self._summary_row(zf, SITE_PROD)

        self.assertEqual(summary["Asana Tasks in Migrated Projects"], "1")
        self.assertEqual(summary["% of Asana Tasks Migrated"], "100.0%")

    def test_summary_reports_multi_key_projects_and_distinct_keys(self) -> None:
        self._project("proj_1", "Project One")
        self._task("task_1", ["proj_1"])
        self._task("task_2", ["proj_1"])
        self._map_task("task_1", "ABC-1", SITE_PROD)
        self._map_task("task_2", "XYZ-2", SITE_PROD)
        zf = self._run(jira_sites=[SITE_PROD])

        summary = self._summary_row(zf, SITE_PROD)

        self.assertEqual(summary["Projects with Multiple Keys"], "1")
        self.assertEqual(summary["Distinct Project Keys"], "2")
        self.assertEqual(summary["Keys Mapped"], "2")

    def test_summary_counts_decisions_among_migrated_projects(self) -> None:
        self._project("proj_migrate", "Migrate Me")
        self._project("proj_none", "No Mapping")
        self._task("task_1", ["proj_migrate"])
        self._map_task("task_1", "PRD-1", SITE_PROD)
        self._decision("proj_migrate", "PRD-1", "Migrate", SITE_PROD)
        self._decision("proj_none", "PRD-9", "Migrate", SITE_PROD)
        zf = self._run(jira_sites=[SITE_PROD])

        rows = _read_sheet(zf, SUMMARY_TAB)
        decision_header_index = next(i for i, row in enumerate(rows) if row and row[0].startswith("ATJM Migration Decision"))
        decision_rows = {row[0]: row[1] for row in rows[decision_header_index + 1 :] if row}

        self.assertEqual(decision_rows, {"Migrate": "1"})

    def test_summary_reports_unsynced_project_references(self) -> None:
        self._project("proj_known", "Known")
        self._task("task_known", ["proj_known"])
        self._task("task_orphan", ["proj_missing"])
        self._map_task("task_known", "PRD-1", SITE_PROD)
        self._map_task("task_orphan", "PRD-2", SITE_PROD)
        zf = self._run(jira_sites=[SITE_PROD])

        rows = _read_sheet(zf, SUMMARY_TAB)
        metric = next(row for row in rows if row and row[0].startswith("Unsynced project GIDs"))

        self.assertEqual(metric[1], "1")
        self.assertEqual(self._summary_row(zf, SITE_PROD)["Asana Projects Migrated"], "1")

    def test_workbook_structure_is_valid_xlsx(self) -> None:
        zf = self._run()
        for filename in ["xl/workbook.xml", "xl/worksheets/sheet1.xml", "xl/worksheets/sheet2.xml", "_rels/.rels"]:
            self.assertIn(filename, zf.namelist())
