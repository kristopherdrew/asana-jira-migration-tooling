from unittest import TestCase
from unittest.mock import Mock, patch

from scripts.atlassian import create_jira_import_fields


class CreateJiraImportFieldsCliTest(TestCase):
    def test_parse_args_supports_site_and_dry_run(self) -> None:
        args = create_jira_import_fields.parse_args(["--jira-site", "sandbox", "--field-set", "tracking", "--dry-run"])
        self.assertEqual(args.jira_site, "sandbox")
        self.assertEqual(args.field_set, "tracking")
        self.assertTrue(args.dry_run)

    @patch("scripts.atlassian.create_jira_import_fields.ensure_standard_import_fields")
    @patch("scripts.atlassian.create_jira_import_fields.JiraClient")
    @patch("scripts.atlassian.create_jira_import_fields.resolve_jira_base_url", return_value="https://jira.example")
    @patch("scripts.atlassian.create_jira_import_fields.load_credentials")
    def test_main_passes_ssl_setting_and_runs_dry_run(self, load_credentials, resolve_url, client_class, ensure_fields) -> None:
        credentials = Mock(
            atlassian_username="user@example.com",
            atlassian_token="token",
            skip_ssl_verification=True,
        )
        load_credentials.return_value = credentials
        ensure_fields.return_value = []

        self.assertEqual(create_jira_import_fields.main(["--dry-run"]), 0)

        resolve_url.assert_called_once_with(credentials, prod=False, site_key=None)
        client_class.assert_called_once_with(
            "https://jira.example",
            "user@example.com",
            "token",
            skip_ssl_verification=True,
        )
        ensure_fields.assert_called_once_with(
            client_class.return_value,
            dry_run=True,
            definitions=create_jira_import_fields.FIELD_SETS["import"],
        )

    @patch("builtins.input", return_value="n")
    @patch("scripts.atlassian.create_jira_import_fields.load_credentials")
    def test_declined_confirmation_makes_no_client(self, load_credentials, _input) -> None:
        load_credentials.return_value = Mock(atlassian_username="u", atlassian_token="t")
        with patch("scripts.atlassian.create_jira_import_fields.resolve_jira_base_url", return_value="https://jira.example"), patch(
            "scripts.atlassian.create_jira_import_fields.JiraClient"
        ) as client_class:
            self.assertEqual(create_jira_import_fields.main([]), 0)
        client_class.assert_not_called()
