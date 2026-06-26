import sqlite3
import tempfile
from datetime import datetime, timezone
from os import environ
from pathlib import Path
from unittest import TestCase
from unittest.mock import MagicMock, patch

import scripts.get_project_status_updates as cli
from src.lib.database import Database


def _write_env(tmp_path: Path) -> Path:
    env_path = tmp_path / ".asana_jira_env"
    env_path.write_text(
        "\n".join(
            [
                "ASANA_TOKEN=token",
                "ASANA_WORKSPACE_GID=workspace",
                "SKIP_SSL_VERIFICATION=false",
            ]
        ),
        encoding="utf-8",
    )
    return env_path


def _seed_project(database_path: Path, project_gid: str) -> None:
    database = Database(str(database_path))
    connection = database.connect()
    try:
        database.initialize(connection)
        database.upsert_project(
            connection,
            {"gid": project_gid, "name": "Status-rich Project", "team": {"gid": "team_1", "name": "Team"}},
            datetime(2025, 1, 1, tzinfo=timezone.utc),
        )
        connection.commit()
    finally:
        connection.close()


class GetProjectStatusUpdatesCliIntegrationTest(TestCase):
    def test_main_persists_status_updates_and_dedups_mirrors(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            env_path = _write_env(tmp_path)
            database_path = tmp_path / "asana.sqlite"
            _seed_project(database_path, "proj_1")

            status_updates = [
                {
                    "gid": "su_1",
                    "title": "Status update - May 16",
                    "status_type": "on_track",
                    "text": "All good",
                    "html_text": "<body>All good</body>",
                    "created_at": "2025-05-16T23:56:22.076Z",
                    "created_by": {"gid": "user_a"},
                    "modified_at": "2025-05-16T23:56:22.667Z",
                    "resource_subtype": "project_status_update",
                }
            ]
            comments = [
                # Mirror of su_1 (same author + created_at) -> dropped.
                {
                    "gid": "story_mirror",
                    "text": "Summary\nAll good",
                    "created_at": "2025-05-16T23:56:22.076Z",
                    "created_by": {"gid": "user_a"},
                    "resource_subtype": "comment_added",
                },
                # Genuine standalone comment -> kept.
                {
                    "gid": "story_real",
                    "text": "Kicking this off",
                    "created_at": "2025-01-01T00:00:00.000Z",
                    "created_by": {"gid": "user_b"},
                    "resource_subtype": "comment_added",
                },
            ]

            with (
                patch.object(cli, "DEFAULT_CONFIG_ROOT", str(tmp_path)),
                patch.object(cli, "DEFAULT_DB_PATH", str(database_path)),
                patch.object(cli, "AsanaHttpClient", MagicMock(return_value=MagicMock())),
                patch.object(cli, "UserBackfill", MagicMock(return_value=MagicMock())),
                patch.object(cli, "fetch_project_status_updates", MagicMock(return_value=status_updates)),
                patch.object(cli, "fetch_project_comments", MagicMock(return_value=comments)),
                patch.dict(environ, {"ASANA_JIRA_ENV_FILE": str(env_path)}, clear=False),
            ):
                cli.main(["--project-gid", "proj_1"])

            connection = sqlite3.connect(database_path)
            connection.row_factory = sqlite3.Row
            try:
                rows = connection.execute(
                    "SELECT gid, kind, status_type FROM project_updates WHERE is_deleted = 0 ORDER BY created_at"
                ).fetchall()
            finally:
                connection.close()

            self.assertEqual([r["gid"] for r in rows], ["story_real", "su_1"])
            self.assertEqual(rows[0]["kind"], "comment")
            self.assertEqual(rows[1]["kind"], "status_update")
            self.assertEqual(rows[1]["status_type"], "on_track")
