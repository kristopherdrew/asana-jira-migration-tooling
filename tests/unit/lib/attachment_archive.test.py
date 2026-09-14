from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import MagicMock, patch

from src.lib.attachment_archive import (
    build_attachment_weblink_payload,
    collect_asana_attachment_sources,
    collect_attachment_weblink_sources,
    create_archive,
    upload_archive,
    upload_attachment_weblinks,
    verify_archive,
)
from src.lib.config import Credentials
from src.lib.database import Database


def timestamp() -> str:
    return datetime(2026, 8, 12, tzinfo=timezone.utc).isoformat()


class AttachmentArchiveTest(TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "asana.sqlite"
        self.database = Database(str(self.db_path))
        with self.database.connect() as connection:
            self.database.initialize(connection)
            connection.execute(
                "INSERT INTO projects (gid, name, synced_at) VALUES ('project_1', 'Project', ?)",
                (timestamp(),),
            )
            connection.execute(
                "INSERT INTO tasks (gid, name, synced_at) VALUES ('task_1', 'Task', ?)",
                (timestamp(),),
            )
            connection.execute(
                "INSERT INTO task_projects (task_gid, project_gid, synced_at) VALUES ('task_1', 'project_1', ?)",
                (timestamp(),),
            )
            connection.executemany(
                """
                INSERT INTO task_attachments (
                    gid, task_gid, name, resource_subtype, host, size,
                    permanent_url, view_url, is_deleted, synced_at
                ) VALUES (?, 'task_1', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        "att_asana", "design.pdf", "asana", "asana", 7,
                        "https://asana/download", "https://asana/view", 0, timestamp(),
                    ),
                    (
                        "att_drive", "linked.pdf", "gdrive", "gdrive", None,
                        "https://drive/link", "https://drive/view", 0, timestamp(),
                    ),
                    (
                        "att_external", None, "external", "external", None,
                        None, "https://external/view", 0, timestamp(),
                    ),
                    (
                        "att_deleted", "deleted.pdf", "gdrive", "gdrive", None,
                        None, "https://drive/deleted", 1, timestamp(),
                    ),
                    (
                        "att_no_url", "missing-url.pdf", "external", "external", None,
                        None, None, 0, timestamp(),
                    ),
                ],
            )
            connection.commit()

    def credentials(self) -> Credentials:
        return Credentials(
            token="asana-token",
            workspace_gid="workspace_1",
            skip_ssl_verification=False,
            atlassian_username="jira@example.com",
            atlassian_token="jira-token",
        )

    def test_collect_sources_excludes_non_asana_hosts(self) -> None:
        with self.database.connect() as connection:
            sources = collect_asana_attachment_sources(connection, ["project_1"])
        self.assertEqual([source.attachment_gid for source in sources], ["att_asana"])
        self.assertEqual(sources[0].jira_name, "design__att_asana.pdf")

    def test_collect_weblink_sources_includes_only_active_non_asana_urls(self) -> None:
        with self.database.connect() as connection:
            sources = collect_attachment_weblink_sources(connection, ["project_1"])
        self.assertEqual([source.attachment_gid for source in sources], ["att_drive", "att_external"])
        self.assertEqual(sources[0].title, "linked.pdf")
        self.assertEqual(sources[1].title, "https://external/view")

    def test_build_weblink_payload_uses_idempotent_global_id(self) -> None:
        with self.database.connect() as connection:
            source = collect_attachment_weblink_sources(connection, ["project_1"])[0]

        payload = build_attachment_weblink_payload(source)

        self.assertEqual(payload["globalId"], "system=asana&attachment_gid=att_drive")
        self.assertEqual(payload["application"]["name"], "Asana App Link")
        self.assertEqual(payload["application"]["type"], "com.asana.app-link")
        self.assertEqual(payload["relationship"], "links to")
        self.assertEqual(payload["object"]["url"], "https://drive/view")
        self.assertEqual(payload["object"]["title"], "linked.pdf")

    def test_verify_archive_can_ignore_not_ready_entries(self) -> None:
        archive_dir = self.root / "archive"
        archive_dir.mkdir()
        file_path = archive_dir / "ready.txt"
        file_path.write_bytes(b"content")
        manifest = archive_dir / "manifest.json"
        manifest.write_text(
            json.dumps({
                "manifest_version": 1,
                "archive_id": "wave-partial",
                "files": [
                    {
                        "attachment_gid": "att_ready",
                        "task_gid": "task_1",
                        "relative_path": "ready.txt",
                        "actual_size": 7,
                        "sha256": "ed7002b439e9ac845f22357d822bac1444730fbdb6016d3ec9432297b9ec9f73",
                        "status": "ready",
                    },
                    {
                        "attachment_gid": "att_failed",
                        "task_gid": "task_1",
                        "relative_path": "failed.txt",
                        "actual_size": None,
                        "sha256": None,
                        "status": "error",
                        "error": "download expired",
                    },
                ],
            }),
            encoding="utf-8",
        )

        self.assertEqual(verify_archive(manifest, include_not_ready=False), [])
        self.assertEqual(verify_archive(manifest)[0]["attachment_gid"], "att_failed")

    @patch("src.lib.attachment_archive.download_file")
    @patch("src.lib.attachment_archive.fetch_download_source")
    def test_create_archive_records_verified_file(self, mock_source, mock_download) -> None:
        mock_source.return_value = ("https://temporary", False)
        mock_download.side_effect = lambda _url, destination, *_args: destination.write_bytes(b"content")
        with self.assertLogs("src.lib.attachment_archive", level="INFO") as logs:
            manifest = create_archive(
                str(self.db_path), self.root / "archives", "wave-1", self.credentials(),
                project_gids=["project_1"], workers=2,
            )
        self.assertIn("1 Asana-hosted attachment(s) selected", logs.output[0])
        self.assertIn("downloaded 1/1 attachment(s)", logs.output[1])
        self.assertIn("complete (1/1 attachment(s) ready)", logs.output[2])
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual(payload["ready_count"], 1)
        self.assertEqual(payload["total_bytes"], 7)
        self.assertEqual(verify_archive(manifest), [])
        archived_file = manifest.parent / payload["files"][0]["relative_path"]
        self.assertEqual(archived_file.read_bytes(), b"content")
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT status, actual_size, sha256 FROM attachment_archive_files"
            ).fetchone()
        self.assertEqual((row["status"], row["actual_size"]), ("ready", 7))
        self.assertEqual(len(row["sha256"]), 64)

        archived_file.write_bytes(b"changed")
        self.assertEqual(verify_archive(manifest)[0]["error"], "checksum mismatch")

    @patch("src.lib.attachment_archive.download_file")
    @patch("src.lib.attachment_archive.fetch_download_source")
    def test_upload_archive_uses_current_mapping_and_records_jira_id(self, mock_source, mock_download) -> None:
        mock_source.return_value = ("https://temporary", False)
        mock_download.side_effect = lambda _url, destination, *_args: destination.write_bytes(b"content")
        manifest = create_archive(
            str(self.db_path), self.root / "archives", "wave-2", self.credentials(), workers=1,
        )
        with self.database.connect() as connection:
            connection.execute(
                "INSERT INTO task_jira_mappings (task_gid, jira_key, synced_at) VALUES ('task_1', 'MIG-7', ?)",
                (timestamp(),),
            )
            connection.commit()
        jira = MagicMock()
        jira.list_issue_attachment_names.return_value = set()
        jira.upload_attachment.return_value = {"id": "9001", "size": 7}
        report = upload_archive(
            str(self.db_path), manifest, jira, dry_run=False, workers=1, throttle_seconds=0,
        )
        self.assertTrue(report.exists())
        jira.upload_attachment.assert_called_once()
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT jira_key, jira_attachment_id, status FROM attachment_jira_uploads"
            ).fetchone()
        self.assertEqual((row["jira_key"], row["jira_attachment_id"], row["status"]), ("MIG-7", "9001", "uploaded"))

    @patch("src.lib.attachment_archive.download_file")
    @patch("src.lib.attachment_archive.fetch_download_source")
    def test_upload_archive_reports_missing_mapping(self, mock_source, mock_download) -> None:
        mock_source.return_value = ("https://temporary", False)
        mock_download.side_effect = lambda _url, destination, *_args: destination.write_bytes(b"content")
        manifest = create_archive(
            str(self.db_path), self.root / "archives", "wave-3", self.credentials(), workers=1,
        )
        jira = MagicMock()
        report = upload_archive(str(self.db_path), manifest, jira, dry_run=True, workers=1)
        self.assertIn("missing-mapping", report.read_text(encoding="utf-8"))
        jira.upload_attachment.assert_not_called()

    @patch("src.lib.attachment_archive.download_file")
    @patch("src.lib.attachment_archive.fetch_download_source")
    def test_upload_archive_filters_by_project_gid(self, mock_source, mock_download) -> None:
        mock_source.return_value = ("https://temporary", False)
        mock_download.side_effect = lambda _url, destination, *_args: destination.write_bytes(b"content")
        with self.database.connect() as connection:
            connection.execute("INSERT INTO projects (gid, name, synced_at) VALUES ('project_2', 'Other', ?)", (timestamp(),))
            connection.execute("INSERT INTO tasks (gid, name, synced_at) VALUES ('task_2', 'Other task', ?)", (timestamp(),))
            connection.execute("INSERT INTO task_projects (task_gid, project_gid, synced_at) VALUES ('task_2', 'project_2', ?)", (timestamp(),))
            connection.execute(
                "INSERT INTO task_attachments (gid, task_gid, name, host, size, permanent_url, synced_at) VALUES ('att_2', 'task_2', 'other.pdf', 'asana', 7, 'https://asana/other', ?)",
                (timestamp(),),
            )
            connection.executemany(
                "INSERT INTO task_jira_mappings (task_gid, jira_key, synced_at) VALUES (?, ?, ?)",
                [("task_1", "MIG-1", timestamp()), ("task_2", "MIG-2", timestamp())],
            )
            connection.commit()
        manifest = create_archive(str(self.db_path), self.root / "archives", "wave-filter", self.credentials(), workers=1)
        jira = MagicMock()
        jira.list_issue_attachment_names.return_value = set()
        report = upload_archive(str(self.db_path), manifest, jira, dry_run=True, workers=1, project_gids=["project_2"])
        self.assertIn("task_2", report.read_text(encoding="utf-8"))
        self.assertNotIn("task_1", report.read_text(encoding="utf-8"))

    @patch("src.lib.attachment_archive.download_file")
    @patch("src.lib.attachment_archive.fetch_download_source")
    def test_upload_weblinks_dry_run_records_without_jira_call(self, mock_source, mock_download) -> None:
        mock_source.return_value = ("https://temporary", False)
        mock_download.side_effect = lambda _url, destination, *_args: destination.write_bytes(b"content")
        manifest = create_archive(
            str(self.db_path), self.root / "archives", "wave-links-dry", self.credentials(), workers=1,
        )
        with self.database.connect() as connection:
            connection.execute(
                "INSERT INTO task_jira_mappings (task_gid, jira_key, synced_at) VALUES ('task_1', 'MIG-7', ?)",
                (timestamp(),),
            )
            connection.commit()
        jira = MagicMock()

        report = upload_attachment_weblinks(str(self.db_path), manifest, jira, dry_run=True, workers=1)

        self.assertTrue(report.name.startswith("jira_weblinks_"))
        self.assertIn("dry-run", report.read_text(encoding="utf-8"))
        jira.create_or_update_remote_link.assert_not_called()
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT attachment_gid, jira_key, status FROM attachment_jira_weblinks ORDER BY attachment_gid"
            ).fetchall()
        self.assertEqual(
            [(row["attachment_gid"], row["jira_key"], row["status"]) for row in rows],
            [("att_drive", "MIG-7", "dry-run"), ("att_external", "MIG-7", "dry-run")],
        )

    @patch("src.lib.attachment_archive.download_file")
    @patch("src.lib.attachment_archive.fetch_download_source")
    def test_upload_weblinks_success_records_remote_link_id(self, mock_source, mock_download) -> None:
        mock_source.return_value = ("https://temporary", False)
        mock_download.side_effect = lambda _url, destination, *_args: destination.write_bytes(b"content")
        manifest = create_archive(
            str(self.db_path), self.root / "archives", "wave-links-success", self.credentials(), workers=1,
        )
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO task_jira_mappings (task_gid, jira_key, synced_at, jira_site_key)
                VALUES ('task_1', 'MIG-7', ?, 'sandbox_jira')
                """,
                (timestamp(),),
            )
            connection.commit()
        jira = MagicMock()
        jira.create_or_update_remote_link.return_value = {"id": 901}

        report = upload_attachment_weblinks(
            str(self.db_path),
            manifest,
            jira,
            dry_run=False,
            workers=1,
            throttle_seconds=0,
            jira_site_key="sandbox_jira",
        )

        self.assertIn("created-or-updated", report.read_text(encoding="utf-8"))
        self.assertEqual(jira.create_or_update_remote_link.call_count, 2)
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT jira_key, jira_remote_link_id, status, global_id
                FROM attachment_jira_weblinks
                WHERE attachment_gid = 'att_drive'
                """
            ).fetchone()
        self.assertEqual(
            (row["jira_key"], row["jira_remote_link_id"], row["status"], row["global_id"]),
            ("MIG-7", "901", "created-or-updated", "system=asana&attachment_gid=att_drive"),
        )

    @patch("src.lib.attachment_archive.download_file")
    @patch("src.lib.attachment_archive.fetch_download_source")
    def test_upload_weblinks_reports_missing_mapping_and_errors(self, mock_source, mock_download) -> None:
        mock_source.return_value = ("https://temporary", False)
        mock_download.side_effect = lambda _url, destination, *_args: destination.write_bytes(b"content")
        manifest = create_archive(
            str(self.db_path), self.root / "archives", "wave-links-errors", self.credentials(), workers=1,
        )
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO tasks (gid, name, synced_at) VALUES ('task_2', 'Other task', ?)
                """,
                (timestamp(),),
            )
            connection.execute(
                """
                INSERT INTO task_attachments (
                    gid, task_gid, name, resource_subtype, host, view_url, synced_at
                ) VALUES ('att_other', 'task_2', 'Other link', 'external', 'external', 'https://other/link', ?)
                """,
                (timestamp(),),
            )
            connection.execute(
                "INSERT INTO task_jira_mappings (task_gid, jira_key, synced_at) VALUES ('task_1', 'MIG-7', ?)",
                (timestamp(),),
            )
            connection.commit()
        jira = MagicMock()
        jira.create_or_update_remote_link.side_effect = RuntimeError("nope")

        report = upload_attachment_weblinks(
            str(self.db_path), manifest, jira, dry_run=False, workers=1, throttle_seconds=0,
        )
        report_text = report.read_text(encoding="utf-8")

        self.assertIn("error", report_text)
        self.assertIn("missing-mapping", report_text)
        with self.database.connect() as connection:
            statuses = {
                row["attachment_gid"]: row["status"]
                for row in connection.execute("SELECT attachment_gid, status FROM attachment_jira_weblinks")
            }
        self.assertEqual(statuses["att_drive"], "error")
        self.assertEqual(statuses["att_external"], "error")
        self.assertEqual(statuses["att_other"], "missing-mapping")

    @patch("src.lib.attachment_archive.download_file")
    @patch("src.lib.attachment_archive.fetch_download_source")
    def test_upload_weblinks_uses_manifest_scope_when_project_scope_omitted(self, mock_source, mock_download) -> None:
        mock_source.return_value = ("https://temporary", False)
        mock_download.side_effect = lambda _url, destination, *_args: destination.write_bytes(b"content")
        with self.database.connect() as connection:
            connection.execute("INSERT INTO projects (gid, name, synced_at) VALUES ('project_2', 'Other', ?)", (timestamp(),))
            connection.execute("INSERT INTO tasks (gid, name, synced_at) VALUES ('task_2', 'Other task', ?)", (timestamp(),))
            connection.execute("INSERT INTO task_projects (task_gid, project_gid, synced_at) VALUES ('task_2', 'project_2', ?)", (timestamp(),))
            connection.execute(
                """
                INSERT INTO task_attachments (
                    gid, task_gid, name, resource_subtype, host, view_url, synced_at
                ) VALUES ('att_project_2', 'task_2', 'Other link', 'gdrive', 'gdrive', 'https://drive/other', ?)
                """,
                (timestamp(),),
            )
            connection.executemany(
                "INSERT INTO task_jira_mappings (task_gid, jira_key, synced_at) VALUES (?, ?, ?)",
                [("task_1", "MIG-1", timestamp()), ("task_2", "MIG-2", timestamp())],
            )
            connection.commit()
        manifest = create_archive(
            str(self.db_path),
            self.root / "archives",
            "wave-links-scope",
            self.credentials(),
            project_gids=["project_2"],
            workers=1,
        )
        jira = MagicMock()

        report = upload_attachment_weblinks(str(self.db_path), manifest, jira, dry_run=True, workers=1)
        report_text = report.read_text(encoding="utf-8")

        self.assertIn("att_project_2", report_text)
        self.assertNotIn("att_drive", report_text)
