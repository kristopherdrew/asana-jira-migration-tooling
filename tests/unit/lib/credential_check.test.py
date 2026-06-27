from __future__ import annotations

from unittest import TestCase
from unittest.mock import MagicMock, patch

from src.lib.config import Credentials
from src.lib.credential_check import (
    check_credentials,
    validate_asana_token,
    validate_atlassian_credentials,
)
from src.lib.http_client import AsanaHttpError
from src.lib.jira_client import JiraApiError


def _creds(**overrides) -> Credentials:
    base = dict(token="asana-tok", workspace_gid="w", skip_ssl_verification=False)
    base.update(overrides)
    return Credentials(**base)


class ValidateAsanaTokenTest(TestCase):
    @patch("src.lib.credential_check.AsanaHttpClient")
    def test_returns_name_on_success(self, mock_client_cls) -> None:
        mock_client_cls.return_value.get.return_value = {"data": {"name": "Jane Doe", "gid": "1"}}
        self.assertEqual(validate_asana_token("tok"), "Jane Doe")
        mock_client_cls.return_value.get.assert_called_once_with("/users/me")

    @patch("src.lib.credential_check.AsanaHttpClient")
    def test_rejected_token_raises_value_error(self, mock_client_cls) -> None:
        mock_client_cls.return_value.get.side_effect = AsanaHttpError(401, "Unauthorized")
        with self.assertRaises(ValueError) as ctx:
            validate_asana_token("bad")
        self.assertIn("Asana token rejected", str(ctx.exception))

    @patch("src.lib.credential_check.AsanaHttpClient")
    def test_empty_payload_raises(self, mock_client_cls) -> None:
        mock_client_cls.return_value.get.return_value = {"data": {}}
        with self.assertRaises(ValueError):
            validate_asana_token("tok")


class ValidateAtlassianCredentialsTest(TestCase):
    @patch("src.lib.credential_check.JiraClient")
    def test_returns_display_name_on_success(self, mock_client_cls) -> None:
        mock_client_cls.return_value.get_myself.return_value = {"displayName": "Jane Q"}
        name = validate_atlassian_credentials("u@example.com", "tok", "https://x.atlassian.net")
        self.assertEqual(name, "Jane Q")
        mock_client_cls.return_value.get_myself.assert_called_once_with()

    @patch("src.lib.credential_check.JiraClient")
    def test_rejected_credentials_raise_value_error(self, mock_client_cls) -> None:
        mock_client_cls.return_value.get_myself.side_effect = JiraApiError(401, "Unauthorized")
        with self.assertRaises(ValueError) as ctx:
            validate_atlassian_credentials("u", "bad", "https://x.atlassian.net")
        self.assertIn("Atlassian credentials rejected", str(ctx.exception))


class CheckCredentialsTest(TestCase):
    @patch("src.lib.credential_check.validate_asana_token", return_value="Jane")
    def test_only_asana_when_no_atlassian_creds(self, _mock_asana) -> None:
        results = check_credentials(_creds())
        self.assertEqual([(r.service, r.ok) for r in results], [("Asana", True)])

    @patch("src.lib.credential_check.validate_atlassian_credentials", return_value="Jane")
    @patch("src.lib.credential_check.validate_asana_token", return_value="Jane")
    def test_atlassian_prefers_production_url(self, _mock_asana, mock_atl) -> None:
        creds = _creds(
            atlassian_username="u@example.com",
            atlassian_token="tok",
            jira_base_url="https://prod.atlassian.net",
            jira_sandbox_base_url="https://sandbox.atlassian.net",
        )
        results = check_credentials(creds)
        labels = {r.service: r.ok for r in results}
        self.assertTrue(labels["Asana"])
        self.assertTrue(labels["Atlassian (production)"])
        mock_atl.assert_called_once_with("u@example.com", "tok", "https://prod.atlassian.net", False)

    @patch("src.lib.credential_check.validate_atlassian_credentials", return_value="Jane")
    @patch("src.lib.credential_check.validate_asana_token", return_value="Jane")
    def test_atlassian_falls_back_to_sandbox_url(self, _mock_asana, mock_atl) -> None:
        creds = _creds(
            atlassian_username="u@example.com",
            atlassian_token="tok",
            jira_sandbox_base_url="https://sandbox.atlassian.net",
        )
        results = check_credentials(creds)
        self.assertIn("Atlassian (sandbox)", {r.service for r in results})
        mock_atl.assert_called_once_with("u@example.com", "tok", "https://sandbox.atlassian.net", False)

    @patch("src.lib.credential_check.validate_asana_token", return_value="Jane")
    def test_atlassian_creds_without_url_reports_cannot_validate(self, _mock_asana) -> None:
        creds = _creds(atlassian_username="u@example.com", atlassian_token="tok")
        results = check_credentials(creds)
        atl = next(r for r in results if r.service.startswith("Atlassian"))
        self.assertFalse(atl.ok)
        self.assertIn("No JIRA_BASE_URL", atl.detail)

    @patch("src.lib.credential_check.validate_asana_token", side_effect=ValueError("Asana token rejected (HTTP 401)."))
    def test_asana_failure_is_captured_as_result(self, _mock_asana) -> None:
        results = check_credentials(_creds())
        self.assertEqual(results[0].service, "Asana")
        self.assertFalse(results[0].ok)
        self.assertIn("rejected", results[0].detail)


if __name__ == "__main__":
    import unittest

    unittest.main()
