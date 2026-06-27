"""Configuration helpers for loading environment-backed credentials."""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

DEFAULT_ENV_PATH = Path.home() / ".asana_jira_env"
PROJECT_ENV_POINTER_FILENAME = ".asana_jira_env_path"

ENV_VAR_NAMES = {
    "token": "ASANA_TOKEN",
    "workspace_gid": "ASANA_WORKSPACE_GID",
    "skip_ssl_verification": "SKIP_SSL_VERIFICATION",
    "atlassian_username": "ATLASSIAN_USERNAME",
    "atlassian_token": "ATLASSIAN_TOKEN",
    "jira_base_url": "JIRA_BASE_URL",
    "jira_sandbox_base_url": "JIRA_SANDBOX_BASE_URL",
}


@dataclass(frozen=True)
class Credentials:
    token: str
    workspace_gid: str
    skip_ssl_verification: bool
    atlassian_username: Optional[str] = None
    atlassian_token: Optional[str] = None
    jira_base_url: Optional[str] = None
    jira_sandbox_base_url: Optional[str] = None


def resolve_jira_base_url(credentials: "Credentials", prod: bool = False) -> str:
    """Return the configured Jira base URL for the selected environment.

    Defaults to the sandbox (``JIRA_SANDBOX_BASE_URL``); pass ``prod=True`` to
    target production (``JIRA_BASE_URL``). Raises ``ValueError`` with actionable
    guidance when the chosen environment's URL is not set in the credentials.
    """
    if prod:
        url, env_name, which = credentials.jira_base_url, ENV_VAR_NAMES["jira_base_url"], "production"
    else:
        url, env_name, which = (
            credentials.jira_sandbox_base_url,
            ENV_VAR_NAMES["jira_sandbox_base_url"],
            "sandbox",
        )
    url = (url or "").strip()
    if not url:
        hint = "" if prod else " (or pass --prod to target production)"
        raise ValueError(
            f"No {which} Jira base URL configured. Set {env_name} in your credentials file{hint}."
        )
    return url.rstrip("/")


def _coerce_bool(value: Any) -> bool:
    """Normalize truthy values coming from JSON or environment overrides."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y"}:
            return True
        if normalized in {"false", "0", "no", "n"}:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    raise ValueError(f"Cannot coerce {value!r} to boolean.")


def _strip_wrapping_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _load_env_file(path: Path) -> Dict[str, str]:
    if not path.exists():
        return {}

    values: Dict[str, str] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.startswith("export "):
                stripped = stripped[len("export ") :].strip()
            if "=" not in stripped:
                raise ValueError(f"Invalid env assignment on line {line_number} in {path}.")
            key, raw_value = stripped.split("=", 1)
            key = key.strip()
            if not key:
                raise ValueError(f"Missing env var name on line {line_number} in {path}.")
            raw_value = raw_value.strip()
            if not raw_value:
                values[key] = ""
                continue
            try:
                parsed = shlex.split(raw_value, posix=True)
            except ValueError:
                values[key] = _strip_wrapping_quotes(raw_value)
                continue
            values[key] = parsed[0] if parsed else ""
    return values


def _resolve_project_env_pointer(path: Path) -> Optional[Path]:
    if not path.exists():
        return None

    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            return Path(_strip_wrapping_quotes(stripped)).expanduser()
    raise ValueError(f"No env file path found in {path}.")


def _resolve_project_root(path: Path) -> Path:
    expanded = path.expanduser()
    if expanded.exists() and expanded.is_dir():
        return expanded
    if expanded.suffix:
        return expanded.parent
    return expanded


def _resolve_env_path(reference_path: Path) -> Path:
    override = os.environ.get("ASANA_JIRA_ENV_FILE", "").strip()
    if override:
        return Path(override).expanduser()

    project_pointer = _resolve_project_root(reference_path) / PROJECT_ENV_POINTER_FILENAME
    project_override = _resolve_project_env_pointer(project_pointer)
    if project_override:
        return project_override

    return DEFAULT_ENV_PATH


def resolve_credentials_path(path: str | Path = ".") -> Path:
    """Return the env file path that will be used for the given reference path."""
    return _resolve_env_path(Path(path))


def _read_value(field_name: str, env_file_data: Dict[str, str]) -> Any:
    env_name = ENV_VAR_NAMES[field_name]
    if env_name in os.environ and os.environ[env_name] != "":
        return os.environ[env_name]
    if env_name in env_file_data and env_file_data[env_name] != "":
        return env_file_data[env_name]
    return None


def load_credentials(path: str | Path = ".") -> Credentials:
    """Load credentials from process environment or env files and validate fields."""
    reference_path = Path(path)
    env_path = _resolve_env_path(reference_path)
    env_file_data = _load_env_file(env_path)

    token = _read_value("token", env_file_data)
    workspace_gid = _read_value("workspace_gid", env_file_data)
    if token is None or workspace_gid is None:
        if not env_file_data:
            raise FileNotFoundError(
                f"No credentials found. Set environment variables, create {env_path}, "
                f"or add {_resolve_project_root(reference_path) / PROJECT_ENV_POINTER_FILENAME}."
            )
        missing_keys = []
        if token is None:
            missing_keys.append("token")
        if workspace_gid is None:
            missing_keys.append("workspace_gid")
        raise KeyError(f"Missing {', '.join(repr(key) for key in missing_keys)} in environment credentials.")

    skip_ssl = _read_value("skip_ssl_verification", env_file_data)
    if skip_ssl is None:
        skip_ssl = False
    try:
        skip_ssl_bool = _coerce_bool(skip_ssl)
    except ValueError as exc:
        raise ValueError("skip_ssl_verification must be a boolean value.") from exc

    if not isinstance(token, str) or not token:
        raise ValueError("token must be a non-empty string.")
    if not isinstance(workspace_gid, str) or not workspace_gid:
        raise ValueError("workspace_gid must be a non-empty string.")

    atlassian_username = (_read_value("atlassian_username", env_file_data) or "").strip() or None
    atlassian_token = (_read_value("atlassian_token", env_file_data) or "").strip() or None
    jira_base_url = (_read_value("jira_base_url", env_file_data) or "").strip() or None
    jira_sandbox_base_url = (_read_value("jira_sandbox_base_url", env_file_data) or "").strip() or None

    return Credentials(
        token=token.strip(),
        workspace_gid=workspace_gid.strip(),
        skip_ssl_verification=skip_ssl_bool,
        atlassian_username=atlassian_username,
        atlassian_token=atlassian_token,
        jira_base_url=jira_base_url,
        jira_sandbox_base_url=jira_sandbox_base_url,
    )


def _format_env_assignment(key: str, value: str) -> str:
    return f"export {key}={shlex.quote(value)}"


def write_credentials_file(path: str | Path, credentials: Credentials) -> Path:
    """Write a shell-compatible env file for the provided credentials."""
    env_path = Path(path).expanduser()
    env_path.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        _format_env_assignment("ASANA_TOKEN", credentials.token),
        _format_env_assignment("ASANA_WORKSPACE_GID", credentials.workspace_gid),
        _format_env_assignment("SKIP_SSL_VERIFICATION", "true" if credentials.skip_ssl_verification else "false"),
    ]
    if credentials.atlassian_username:
        lines.append(_format_env_assignment("ATLASSIAN_USERNAME", credentials.atlassian_username))
    if credentials.atlassian_token:
        lines.append(_format_env_assignment("ATLASSIAN_TOKEN", credentials.atlassian_token))
    if credentials.jira_base_url:
        lines.append(_format_env_assignment("JIRA_BASE_URL", credentials.jira_base_url))
    if credentials.jira_sandbox_base_url:
        lines.append(_format_env_assignment("JIRA_SANDBOX_BASE_URL", credentials.jira_sandbox_base_url))

    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        env_path.chmod(0o600)
    except OSError:
        pass
    return env_path


def write_project_env_pointer(reference_path: str | Path, env_path: str | Path) -> Path:
    """Point the project-specific env reference file at the provided credentials file."""
    pointer_path = _resolve_project_root(Path(reference_path)) / PROJECT_ENV_POINTER_FILENAME
    pointer_path.write_text(f"{Path(env_path).expanduser()}\n", encoding="utf-8")
    return pointer_path
