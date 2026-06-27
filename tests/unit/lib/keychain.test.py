from __future__ import annotations

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from src.lib import keychain


def _completed(returncode=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


class IsAvailableTest(TestCase):
    def test_true_on_macos_with_security_binary(self) -> None:
        with patch("src.lib.keychain.sys.platform", "darwin"), patch(
            "src.lib.keychain.shutil.which", return_value="/usr/bin/security"
        ):
            self.assertTrue(keychain.is_available())

    def test_false_off_macos(self) -> None:
        with patch("src.lib.keychain.sys.platform", "linux"), patch(
            "src.lib.keychain.shutil.which", return_value="/usr/bin/security"
        ):
            self.assertFalse(keychain.is_available())

    def test_false_when_binary_missing(self) -> None:
        with patch("src.lib.keychain.sys.platform", "darwin"), patch(
            "src.lib.keychain.shutil.which", return_value=None
        ):
            self.assertFalse(keychain.is_available())


class AccountForTest(TestCase):
    def test_legacy_account_namespaces_by_file_name_and_var(self) -> None:
        self.assertEqual(
            keychain.legacy_account_for("/home/me/.asana_jira_env_client_a", "ASANA_TOKEN"),
            ".asana_jira_env_client_a:ASANA_TOKEN",
        )
        # ``account_for`` is retained as an alias of the legacy scheme.
        self.assertEqual(
            keychain.account_for("/home/me/.asana_jira_env_client_a", "ASANA_TOKEN"),
            ".asana_jira_env_client_a:ASANA_TOKEN",
        )

    def test_slug_account_namespaces_by_slug_and_var(self) -> None:
        self.assertEqual(keychain.account_for_slug("acme", "ASANA_TOKEN"), "acme:ASANA_TOKEN")
        # Empty slug (the default profile) maps to "default".
        self.assertEqual(keychain.account_for_slug("", "ATLASSIAN_TOKEN"), "default:ATLASSIAN_TOKEN")


class GetSecretTest(TestCase):
    @patch("src.lib.keychain.is_available", return_value=True)
    @patch("src.lib.keychain.subprocess.run", return_value=_completed(0, "tok\n"))
    def test_returns_stored_value(self, mock_run, _avail) -> None:
        self.assertEqual(keychain.get_secret("acct:ASANA_TOKEN"), "tok")
        argv = mock_run.call_args.args[0]
        self.assertEqual(
            argv,
            ["security", "find-generic-password", "-s", keychain.KEYCHAIN_SERVICE, "-a", "acct:ASANA_TOKEN", "-w"],
        )

    @patch("src.lib.keychain.is_available", return_value=True)
    @patch("src.lib.keychain.subprocess.run", return_value=_completed(44, ""))
    def test_returns_none_when_not_found(self, _run, _avail) -> None:
        self.assertIsNone(keychain.get_secret("missing"))

    @patch("src.lib.keychain.is_available", return_value=True)
    @patch("src.lib.keychain.subprocess.run", side_effect=FileNotFoundError())
    def test_returns_none_on_missing_binary(self, _run, _avail) -> None:
        self.assertIsNone(keychain.get_secret("acct"))

    @patch("src.lib.keychain.is_available", return_value=False)
    def test_returns_none_when_unavailable(self, _avail) -> None:
        with patch("src.lib.keychain.subprocess.run") as mock_run:
            self.assertIsNone(keychain.get_secret("acct"))
            mock_run.assert_not_called()


class SetSecretTest(TestCase):
    @patch("src.lib.keychain.is_available", return_value=True)
    @patch("src.lib.keychain.subprocess.run", return_value=_completed(0))
    def test_uses_update_flag(self, mock_run, _avail) -> None:
        keychain.set_secret("acct:ASANA_TOKEN", "sekret")
        argv = mock_run.call_args.args[0]
        self.assertEqual(argv[:3], ["security", "add-generic-password", "-U"])
        self.assertIn("sekret", argv)

    @patch("src.lib.keychain.is_available", return_value=True)
    @patch("src.lib.keychain.subprocess.run", return_value=_completed(1, "", "boom"))
    def test_raises_on_failure(self, _run, _avail) -> None:
        with self.assertRaises(RuntimeError):
            keychain.set_secret("acct", "x")

    @patch("src.lib.keychain.is_available", return_value=False)
    def test_raises_when_unavailable(self, _avail) -> None:
        with self.assertRaises(RuntimeError):
            keychain.set_secret("acct", "x")


class DeleteSecretTest(TestCase):
    @patch("src.lib.keychain.is_available", return_value=True)
    @patch("src.lib.keychain.subprocess.run", return_value=_completed(0))
    def test_true_when_deleted(self, _run, _avail) -> None:
        self.assertTrue(keychain.delete_secret("acct"))

    @patch("src.lib.keychain.is_available", return_value=True)
    @patch("src.lib.keychain.subprocess.run", return_value=_completed(44))
    def test_false_when_absent(self, _run, _avail) -> None:
        self.assertFalse(keychain.delete_secret("acct"))


if __name__ == "__main__":
    import unittest

    unittest.main()
