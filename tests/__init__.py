import os

PROFILE_ENV_VARS = ("ASANA_JIRA_DB_PATH", "ASANA_JIRA_ENV_FILE", "ASANA_JIRA_SITE_KEY")


def scrub_profile_env() -> None:
    """Drop ambient profile env so tests cannot reach a real profile database or env file."""
    for name in PROFILE_ENV_VARS:
        os.environ.pop(name, None)


scrub_profile_env()
