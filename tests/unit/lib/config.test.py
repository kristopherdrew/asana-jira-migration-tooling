import tempfile
from os import environ
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from src.lib.config import Credentials, load_credentials


class LoadCredentialsTest(TestCase):
    def test_loads_and_normalizes_boolean_from_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            env_path = project_root / ".asana_jira_env"
            env_path.write_text(
                "\n".join(
                    [
                        "ASANA_TOKEN=abc123",
                        "ASANA_WORKSPACE_GID=456",
                        "SKIP_SSL_VERIFICATION=true",
                    ]
                ),
                encoding="utf-8",
            )

            with patch.dict(environ, {"ASANA_JIRA_ENV_FILE": str(env_path)}, clear=False):
                credentials = load_credentials(project_root)

            self.assertIsInstance(credentials, Credentials)
            self.assertEqual(credentials.token, "abc123")
            self.assertEqual(credentials.workspace_gid, "456")
            self.assertTrue(credentials.skip_ssl_verification)

    def test_missing_required_key_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            env_path = project_root / ".asana_jira_env"
            env_path.write_text("ASANA_TOKEN=abc\n", encoding="utf-8")

            with patch.dict(environ, {"ASANA_JIRA_ENV_FILE": str(env_path)}, clear=False):
                with self.assertRaises(KeyError):
                    load_credentials(project_root)

    def test_loads_from_env_file_when_credentials_json_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            env_path = Path(tmpdir) / ".asana_jira_env"
            env_path.write_text(
                "\n".join(
                    [
                        'export ASANA_TOKEN="env-token"',
                        "ASANA_WORKSPACE_GID=env-workspace",
                        "SKIP_SSL_VERIFICATION=false",
                        "ATLASSIAN_USERNAME=user@example.com",
                        "ATLASSIAN_TOKEN=jira-token",
                    ]
                ),
                encoding="utf-8",
            )

            with patch.dict(environ, {"ASANA_JIRA_ENV_FILE": str(env_path)}, clear=False):
                credentials = load_credentials(project_root)

            self.assertEqual(credentials.token, "env-token")
            self.assertEqual(credentials.workspace_gid, "env-workspace")
            self.assertFalse(credentials.skip_ssl_verification)
            self.assertEqual(credentials.atlassian_username, "user@example.com")
            self.assertEqual(credentials.atlassian_token, "jira-token")

    def test_process_environment_overrides_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            env_path = Path(tmpdir) / ".asana_jira_env"
            env_path.write_text(
                "\n".join(
                    [
                        "ASANA_TOKEN=env-file-token",
                        "ASANA_WORKSPACE_GID=env-file-workspace",
                        "SKIP_SSL_VERIFICATION=false",
                    ]
                ),
                encoding="utf-8",
            )

            with patch.dict(
                environ,
                {
                    "ASANA_JIRA_ENV_FILE": str(env_path),
                    "ASANA_TOKEN": "process-token",
                    "ASANA_WORKSPACE_GID": "process-workspace",
                    "SKIP_SSL_VERIFICATION": "true",
                },
                clear=False,
            ):
                credentials = load_credentials(project_root)

            self.assertEqual(credentials.token, "process-token")
            self.assertEqual(credentials.workspace_gid, "process-workspace")
            self.assertTrue(credentials.skip_ssl_verification)

    def test_project_pointer_file_selects_client_specific_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            env_path = Path(tmpdir) / "client-a.env"
            env_path.write_text(
                "\n".join(
                    [
                        "ASANA_TOKEN=client-token",
                        "ASANA_WORKSPACE_GID=client-workspace",
                        "SKIP_SSL_VERIFICATION=false",
                    ]
                ),
                encoding="utf-8",
            )
            (project_root / ".asana_jira_env_path").write_text(f"{env_path}\n", encoding="utf-8")

            with patch.dict(environ, {}, clear=True):
                credentials = load_credentials(project_root)

            self.assertEqual(credentials.token, "client-token")
            self.assertEqual(credentials.workspace_gid, "client-workspace")
            self.assertFalse(credentials.skip_ssl_verification)

    def test_missing_env_sources_raises_file_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            with patch.dict(environ, {}, clear=True):
                with self.assertRaises(FileNotFoundError):
                    load_credentials(project_root)
