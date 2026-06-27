"""Live credential validation against the Asana and Atlassian APIs.

Before persisting a credentials file the interactive CLI calls these helpers so a
bad token is caught immediately rather than on the first real sync. Each check
hits a lightweight "who am I" endpoint:

- Asana:     ``GET /users/me`` (Bearer token)
- Atlassian: ``GET /rest/api/3/myself`` (Basic username + API token)

Stdlib-only: reuses the existing ``AsanaHttpClient`` / ``JiraClient`` transports,
so the resolved ``skip_ssl_verification`` setting is honored.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

from src.lib.config import Credentials
from src.lib.http_client import AsanaHttpClient, AsanaHttpError
from src.lib.jira_client import JiraApiError, JiraClient


@dataclass(frozen=True)
class CheckResult:
    service: str
    ok: bool
    detail: str


def validate_asana_token(token: str, skip_ssl_verification: bool = False) -> str:
    """Return the Asana account's display name if ``token`` authenticates.

    Raises ``ValueError`` with a readable message when the token is rejected or
    the API cannot be reached.
    """
    client = AsanaHttpClient(token=token, skip_ssl_verification=skip_ssl_verification)
    try:
        payload = client.get("/users/me")
    except AsanaHttpError as exc:
        raise ValueError(
            f"Asana token rejected (HTTP {exc.status}). Check ASANA_TOKEN."
        ) from exc
    except Exception as exc:  # noqa: BLE001 - network/SSL/URL errors surfaced to the user
        raise ValueError(f"Could not reach Asana to validate the token: {exc}") from exc

    me = (payload or {}).get("data") or {}
    name = me.get("name") or me.get("email") or me.get("gid")
    if not name:
        raise ValueError("Asana /users/me returned no user data; the token may be invalid.")
    return str(name)


def validate_atlassian_credentials(
    username: str,
    token: str,
    base_url: str,
    skip_ssl_verification: bool = False,
) -> str:
    """Return the Atlassian account's display name if the credentials authenticate.

    Raises ``ValueError`` with a readable message when the username/token pair is
    rejected at ``base_url`` or the site cannot be reached.
    """
    client = JiraClient(
        base_url=base_url,
        username=username,
        token=token,
        skip_ssl_verification=skip_ssl_verification,
    )
    try:
        payload = client.get_myself()
    except JiraApiError as exc:
        raise ValueError(
            f"Atlassian credentials rejected (HTTP {exc.status}) at {base_url}. "
            "Check ATLASSIAN_USERNAME and ATLASSIAN_TOKEN."
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Could not reach Atlassian at {base_url} to validate credentials: {exc}") from exc

    name = payload.get("displayName") or payload.get("emailAddress") or payload.get("accountId")
    if not name:
        raise ValueError("Atlassian /rest/api/3/myself returned no account data; the credentials may be invalid.")
    return str(name)


def check_credentials(credentials: Credentials) -> List[CheckResult]:
    """Validate the tokens present on ``credentials`` and return per-service results.

    Always checks the (required) Asana token. Checks Atlassian only when both a
    username and token are set; it validates against the production Jira URL when
    available, otherwise the sandbox URL, and reports that no check was possible
    when neither URL is configured.
    """
    results: List[CheckResult] = []

    try:
        name = validate_asana_token(credentials.token, credentials.skip_ssl_verification)
        results.append(CheckResult("Asana", True, f"authenticated as {name}"))
    except ValueError as exc:
        results.append(CheckResult("Asana", False, str(exc)))

    if credentials.atlassian_username and credentials.atlassian_token:
        if credentials.jira_base_url:
            base_url, label = credentials.jira_base_url, "Atlassian (production)"
        elif credentials.jira_sandbox_base_url:
            base_url, label = credentials.jira_sandbox_base_url, "Atlassian (sandbox)"
        else:
            base_url, label = None, "Atlassian"
        if not base_url:
            results.append(
                CheckResult(
                    label,
                    False,
                    "No JIRA_BASE_URL or JIRA_SANDBOX_BASE_URL set, so the Atlassian token "
                    "could not be validated. Add a Jira URL to validate it.",
                )
            )
        else:
            try:
                name = validate_atlassian_credentials(
                    credentials.atlassian_username,
                    credentials.atlassian_token,
                    base_url,
                    credentials.skip_ssl_verification,
                )
                results.append(CheckResult(label, True, f"authenticated as {name} at {base_url}"))
            except ValueError as exc:
                results.append(CheckResult(label, False, str(exc)))

    return results
