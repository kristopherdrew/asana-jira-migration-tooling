from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import MagicMock, patch

from src.lib.config import Credentials
from src.lib.database import Database

import scripts.sync_jira_attachments as sync_script


def timestamp() -> str:
    return datetime(2025, 4, 1, tzinfo=timezone.utc).isoformat()


class SyncJiraAttachmentsTest(TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.workspace = Path(self.temp_dir.name)
        self.db_path = self.workspace / "asana.sqlite"
        self.database = Database(str(self.db_path))
        self.connection = self.database.connect()
        self.database.initialize(self.connection)
        self.project_gid = "proj_1"
        self._seed_data()

    def tearDown(self) -> None:
        self.connection.close()

    def _seed_data(self) -> None:
        self.connection.execute(
            "INSERT INTO projects (gid, name, synced_at) VALUES (?, ?, ?)",
            (self.project_gid, "Example Project", timestamp()),
        )
        self.connection.execute(
            "INSERT INTO tasks (gid, name, synced_at) VALUES (?, ?, ?)",
            ("task_1", "Task One", timestamp()),
        )
        self.connection.execute(
            "INSERT INTO task_projects (task_gid, project_gid, synced_at) VALUES (?, ?, ?)",
            ("task_1", self.project_gid, timestamp()),
        )
        self.connection.execute(
            """
            INSERT INTO task_attachments (
                gid, task_gid, name, permanent_url, synced_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            ("att_1", "task_1", "example.png", "https://app.asana.com/app/asana/-/get_asset?asset_id=1", timestamp()),
        )
        self.connection.execute(
            "INSERT INTO task_jira_mappings (task_gid, jira_key, synced_at) VALUES (?, ?, ?)",
            ("task_1", "ENG-1", timestamp()),
        )
        self.connection.commit()

    def _creds(self) -> Credentials:
        return Credentials(
            token="asana-token",
            workspace_gid="ws",
            skip_ssl_verification=False,
            atlassian_username="jira@example.com",
            atlassian_token="jira-token",
        )

    def test_resolve_project_gids_from_file(self) -> None:
        gids_path = self.workspace / "projects.csv"
        gids_path.write_text("proj_a\nproj_b, proj_a\n", encoding="utf-8")
        self.assertEqual(
            sync_script.resolve_project_gids(None, None, str(gids_path)),
            ["proj_a", "proj_b"],
        )

    def test_resolve_project_gids_rejects_multiple_scope_sources(self) -> None:
        with self.assertRaisesRegex(SystemExit, "Choose only one"):
            sync_script.resolve_project_gids("proj_a", None, "projects.csv")

    def test_collect_attachment_jobs_returns_jobs(self) -> None:
        jobs = sync_script.collect_attachment_jobs(self.connection, self.project_gid)
        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual(job.attachment_gid, "att_1")
        self.assertEqual(job.attachment_name, "example__att_1.png")
        self.assertEqual(job.jira_key, "ENG-1")

    @patch("scripts.atlassian.sync_jira_attachments.download_attachment")
    @patch("scripts.atlassian.sync_jira_attachments.fetch_attachment_download_source")
    @patch("scripts.atlassian.sync_jira_attachments.JiraClient")
    @patch("scripts.atlassian.sync_jira_attachments.load_credentials")
    def test_sync_attachments_dry_run(self, mock_creds, mock_jira_client, mock_source, mock_download) -> None:
        mock_creds.return_value = self._creds()
        mock_client_instance = MagicMock()
        mock_client_instance.list_issue_attachment_names.return_value = set()
        mock_jira_client.return_value = mock_client_instance
        mock_source.return_value = ("https://download", False)
        output_dir = self.workspace / "exports"

        report_paths = sync_script.sync_attachments(
            database_path=str(self.db_path),
            project_gids=[self.project_gid],
            jira_base_url="https://example.atlassian.net",
            output_dir=output_dir,
            max_workers=1,
            throttle_seconds=0.0,
            dry_run=True,
            keep_downloads=False,
        )

        self.assertEqual(len(report_paths), 1)
        report_path = report_paths[0]
        self.assertTrue(report_path.exists())
        with report_path.open("r", newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(rows[0]["status"], "dry-run")
        self.assertIn("project", rows[0])
        mock_client_instance.list_issue_attachment_names.assert_not_called()
        mock_download.assert_not_called()

    @patch("scripts.atlassian.sync_jira_attachments.download_attachment")
    @patch("scripts.atlassian.sync_jira_attachments.fetch_attachment_download_source")
    @patch("scripts.atlassian.sync_jira_attachments.JiraClient")
    @patch("scripts.atlassian.sync_jira_attachments.load_credentials")
    def test_sync_attachments_uploads_and_skips_existing(self, mock_creds, mock_jira_client, mock_source, mock_download) -> None:
        mock_creds.return_value = self._creds()
        mock_client_instance = MagicMock()
        mock_client_instance.list_issue_attachment_names.return_value = {"example__att_1.png"}
        mock_jira_client.return_value = mock_client_instance
        mock_source.return_value = ("https://download", False)

        def fake_download(url: str, destination: Path, _token: str, _skip_ssl: bool, _auth: bool) -> None:
            destination.write_bytes(b"content")

        mock_download.side_effect = fake_download
        output_dir = self.workspace / "exports"

        report_paths = sync_script.sync_attachments(
            database_path=str(self.db_path),
            project_gids=[self.project_gid],
            jira_base_url="https://example.atlassian.net",
            output_dir=output_dir,
            max_workers=1,
            throttle_seconds=0.0,
            dry_run=False,
            keep_downloads=False,
        )

        with report_paths[0].open("r", newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(rows[0]["status"], "skipped-existing")
        mock_client_instance.upload_attachment.assert_not_called()
        mock_client_instance.list_issue_attachment_names.assert_called_once_with("ENG-1")
        mock_download.assert_called_once()

    @patch("scripts.atlassian.sync_jira_attachments.download_attachment")
    @patch("scripts.atlassian.sync_jira_attachments.fetch_attachment_download_source")
    @patch("scripts.atlassian.sync_jira_attachments.JiraClient")
    @patch("scripts.atlassian.sync_jira_attachments.load_credentials")
    def test_skips_project_without_attachments_but_continues(self, mock_creds, mock_jira_client, mock_source, mock_download) -> None:
        # Add a project with no attachments
        empty_project = "proj_empty"
        self.connection.execute(
            "INSERT INTO projects (gid, name, synced_at) VALUES (?, ?, ?)",
            (empty_project, "Empty Project", timestamp()),
        )
        self.connection.commit()

        # Existing project keeps one attachment/mapping from setUp
        mock_creds.return_value = self._creds()
        mock_client_instance = MagicMock()
        mock_client_instance.list_issue_attachment_names.return_value = set()
        mock_jira_client.return_value = mock_client_instance
        mock_source.return_value = ("https://download", False)

        def fake_download(url: str, destination: Path, _token: str, _skip_ssl: bool, _auth: bool) -> None:
            destination.write_bytes(b"content")

        mock_download.side_effect = fake_download

        output_dir = self.workspace / "exports"

        report_paths = sync_script.sync_attachments(
            database_path=str(self.db_path),
            project_gids=[empty_project, self.project_gid],
            jira_base_url="https://example.atlassian.net",
            output_dir=output_dir,
            max_workers=1,
            throttle_seconds=0.0,
            dry_run=False,
            keep_downloads=False,
        )

        # Only the non-empty project should produce a report
        self.assertEqual(len(report_paths), 1)
        self.assertTrue(report_paths[0].exists())
        mock_client_instance.upload_attachment.assert_called()

    @patch("scripts.atlassian.sync_jira_attachments.download_attachment")
    @patch("scripts.atlassian.sync_jira_attachments.fetch_attachment_download_source")
    @patch("scripts.atlassian.sync_jira_attachments.JiraClient")
    @patch("scripts.atlassian.sync_jira_attachments.load_credentials")
    def test_sync_multiple_projects(self, mock_creds, mock_jira_client, mock_source, mock_download) -> None:
        # Seed a second project with its own attachment/mapping
        project_two = "proj_2"
        self.connection.execute(
            "INSERT INTO projects (gid, name, synced_at) VALUES (?, ?, ?)",
            (project_two, "Project Two", timestamp()),
        )
        self.connection.execute(
            "INSERT INTO tasks (gid, name, synced_at) VALUES (?, ?, ?)",
            ("task_2", "Task Two", timestamp()),
        )
        self.connection.execute(
            "INSERT INTO task_projects (task_gid, project_gid, synced_at) VALUES (?, ?, ?)",
            ("task_2", project_two, timestamp()),
        )
        self.connection.execute(
            "INSERT INTO task_attachments (gid, task_gid, name, permanent_url, synced_at) VALUES (?, ?, ?, ?, ?)",
            ("att_2", "task_2", "second.png", "https://app.asana.com/app/asana/-/get_asset?asset_id=2", timestamp()),
        )
        self.connection.execute(
            "INSERT INTO task_jira_mappings (task_gid, jira_key, synced_at) VALUES (?, ?, ?)",
            ("task_2", "OPS-2", timestamp()),
        )
        self.connection.commit()

        mock_creds.return_value = self._creds()
        mock_client_instance = MagicMock()
        mock_client_instance.list_issue_attachment_names.return_value = set()
        mock_jira_client.return_value = mock_client_instance
        mock_source.return_value = ("https://download", False)

        def fake_download(url: str, destination: Path, _token: str, _skip_ssl: bool, _auth: bool) -> None:
            destination.write_bytes(b"content")

        mock_download.side_effect = fake_download

        output_dir = self.workspace / "exports"

        report_paths = sync_script.sync_attachments(
            database_path=str(self.db_path),
            project_gids=[self.project_gid, project_two],
            jira_base_url="https://example.atlassian.net",
            output_dir=output_dir,
            max_workers=1,
            throttle_seconds=0.0,
            dry_run=False,
            keep_downloads=False,
        )

        self.assertEqual(len(report_paths), 2)
        for path in report_paths:
            self.assertTrue(path.exists())
        mock_client_instance.upload_attachment.assert_called()

    def test_consolidated_report_created_for_multiple_projects(self) -> None:
        rows = [
            {
                "project": "Project A",
                "project_gid": "proj_a",
                "jira_key": "ENG-1",
                "task_gid": "task_1",
                "attachment_gid": "att_1",
                "attachment_name": "file1",
                "status": "uploaded",
                "message": "",
            },
            {
                "project": "Project B",
                "project_gid": "proj_b",
                "jira_key": "OPS-2",
                "task_gid": "task_2",
                "attachment_gid": "att_2",
                "attachment_name": "file2",
                "status": "uploaded",
                "message": "",
            },
        ]
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            report1 = root / "projA.csv"
            report2 = root / "projB.csv"
            fieldnames = list(rows[0].keys())
            with report1.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows[:1])
            with report2.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows[1:])

            # simulate consolidation
            consolidated = root / "attachments_consolidated.csv"
            all_rows = []
            for path in [report1, report2]:
                with path.open("r", newline="", encoding="utf-8") as handle:
                    all_rows.extend(list(csv.DictReader(handle)))
            with consolidated.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(all_rows)

            with consolidated.open("r", newline="", encoding="utf-8") as handle:
                merged = list(csv.DictReader(handle))
            self.assertEqual(len(merged), 2)
