import os
from unittest import TestCase
from unittest.mock import patch

from tests import PROFILE_ENV_VARS, scrub_profile_env


class ScrubProfileEnvTest(TestCase):
    def test_scrub_removes_profile_env_vars(self) -> None:
        ambient = {name: "/real/profile" for name in PROFILE_ENV_VARS}
        with patch.dict(os.environ, ambient):
            scrub_profile_env()
            for name in PROFILE_ENV_VARS:
                self.assertNotIn(name, os.environ, f"{name} should be removed so tests cannot reach a real profile")

    def test_scrub_covers_database_env_file_and_site(self) -> None:
        self.assertEqual(
            set(PROFILE_ENV_VARS),
            {"ASANA_JIRA_DB_PATH", "ASANA_JIRA_ENV_FILE", "ASANA_JIRA_SITE_KEY"},
        )

    def test_scrub_is_safe_when_vars_are_absent(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            for name in PROFILE_ENV_VARS:
                os.environ.pop(name, None)
            scrub_profile_env()
