"""Configuration helpers for loading environment-backed credentials."""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.lib import keychain

DEFAULT_ENV_PATH = Path.home() / ".asana_jira_env"
PROJECT_ENV_POINTER_FILENAME = ".asana_jira_env_path"

# Named-profile credentials files follow ``~/.asana_jira_env_<slug>``; the
# bare ``~/.asana_jira_env`` is the legacy/default profile.
NAMED_PROFILE_PREFIX = ".asana_jira_env_"

ENV_VAR_NAMES = {
    "client_name": "CLIENT_NAME",
    "token": "ASANA_TOKEN",
    "workspace_gid": "ASANA_WORKSPACE_GID",
    "skip_ssl_verification": "SKIP_SSL_VERIFICATION",
    "atlassian_username": "ATLASSIAN_USERNAME",
    "atlassian_token": "ATLASSIAN_TOKEN",
    "jira_base_url": "JIRA_BASE_URL",
    "jira_sandbox_base_url": "JIRA_SANDBOX_BASE_URL",
}

# The secret fields stored in the macOS Keychain (when available); everything
# else stays in the plaintext env file.
SECRET_FIELDS = ("token", "atlassian_token")
SECRET_ENV_VARS = tuple(ENV_VAR_NAMES[field] for field in SECRET_FIELDS)


@dataclass(frozen=True)
class Credentials:
    token: str
    workspace_gid: str
    skip_ssl_verification: bool
    atlassian_username: Optional[str] = None
    atlassian_token: Optional[str] = None
    jira_base_url: Optional[str] = None
    jira_sandbox_base_url: Optional[str] = None
    client_name: Optional[str] = None


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


def _read_secret(field_name: str, env_file_data: Dict[str, str], env_path: Path) -> Optional[str]:
    """Resolve a secret token with precedence: process env -> Keychain -> env file.

    The Keychain entry is namespaced per credentials file (see ``keychain``), so
    different clients' tokens don't collide. Falls back to the env file when the
    Keychain is unavailable (non-macOS) or holds no value for this account.
    """
    env_name = ENV_VAR_NAMES[field_name]
    if os.environ.get(env_name):
        return os.environ[env_name]
    if keychain.is_available():
        stored = keychain.get_secret(keychain.account_for(env_path, env_name))
        if stored:
            return stored
    file_value = env_file_data.get(env_name)
    return file_value or None


def load_credentials(path: str | Path = ".") -> Credentials:
    """Load credentials from process environment or env files and validate fields."""
    reference_path = Path(path)
    env_path = _resolve_env_path(reference_path)
    env_file_data = _load_env_file(env_path)

    token = _read_secret("token", env_file_data, env_path)
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
    atlassian_token = (_read_secret("atlassian_token", env_file_data, env_path) or "").strip() or None
    jira_base_url = (_read_value("jira_base_url", env_file_data) or "").strip() or None
    jira_sandbox_base_url = (_read_value("jira_sandbox_base_url", env_file_data) or "").strip() or None
    client_name = (_read_value("client_name", env_file_data) or "").strip() or None

    return Credentials(
        token=token.strip(),
        workspace_gid=workspace_gid.strip(),
        skip_ssl_verification=skip_ssl_bool,
        atlassian_username=atlassian_username,
        atlassian_token=atlassian_token,
        jira_base_url=jira_base_url,
        jira_sandbox_base_url=jira_sandbox_base_url,
        client_name=client_name,
    )


def _format_env_assignment(key: str, value: str) -> str:
    return f"export {key}={shlex.quote(value)}"


def _store_secrets_in_keychain(env_path: Path, credentials: Credentials) -> None:
    """Persist the two tokens to the Keychain (delete the entry when a value is cleared)."""
    secret_values = {
        "ASANA_TOKEN": credentials.token,
        "ATLASSIAN_TOKEN": credentials.atlassian_token,
    }
    for env_name, value in secret_values.items():
        account = keychain.account_for(env_path, env_name)
        if value:
            keychain.set_secret(account, value)
        else:
            keychain.delete_secret(account)


def write_credentials_file(path: str | Path, credentials: Credentials) -> Path:
    """Write the env file for the provided credentials.

    When the macOS Keychain is available the two tokens are stored there and kept
    out of the file; otherwise (non-macOS fallback) they are written to the file as
    plaintext alongside the rest. Non-secret config is always written to the file.
    """
    env_path = Path(path).expanduser()
    env_path.parent.mkdir(parents=True, exist_ok=True)

    use_keychain = keychain.is_available()
    if use_keychain:
        _store_secrets_in_keychain(env_path, credentials)

    lines = [_format_env_assignment("ASANA_WORKSPACE_GID", credentials.workspace_gid)]
    if not use_keychain:
        # Fallback: ASANA_TOKEN is required, so always emit it here.
        lines.insert(0, _format_env_assignment("ASANA_TOKEN", credentials.token))
    if credentials.client_name:
        # Profile identifier sits at the very top of the file for readability.
        lines.insert(0, _format_env_assignment("CLIENT_NAME", credentials.client_name))
    lines.append(
        _format_env_assignment("SKIP_SSL_VERIFICATION", "true" if credentials.skip_ssl_verification else "false")
    )
    if credentials.atlassian_username:
        lines.append(_format_env_assignment("ATLASSIAN_USERNAME", credentials.atlassian_username))
    if not use_keychain and credentials.atlassian_token:
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


def file_has_plaintext_tokens(env_path: str | Path) -> bool:
    """Return True when the env file still contains either token line."""
    data = _load_env_file(Path(env_path).expanduser())
    return any(data.get(name) for name in SECRET_ENV_VARS)


def migrate_tokens_to_keychain(env_path: str | Path) -> List[str]:
    """Move any plaintext tokens from the env file into the Keychain.

    Copies ``ASANA_TOKEN`` / ``ATLASSIAN_TOKEN`` values into the Keychain (namespaced
    to this file) and rewrites the file without those lines, preserving everything
    else. Returns the env-var names that were migrated. No-op (returns ``[]``) when
    the Keychain is unavailable or the file holds no tokens.
    """
    path = Path(env_path).expanduser()
    if not keychain.is_available() or not path.exists():
        return []
    data = _load_env_file(path)
    migrated: List[str] = []
    for env_name in SECRET_ENV_VARS:
        value = data.get(env_name)
        if value:
            keychain.set_secret(keychain.account_for(path, env_name), value)
            migrated.append(env_name)
    if not migrated:
        return []

    # Rewrite the file verbatim minus the migrated token lines.
    kept_lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        body = stripped[len("export "):].strip() if stripped.startswith("export ") else stripped
        key = body.split("=", 1)[0].strip() if "=" in body else ""
        if key in migrated:
            continue
        kept_lines.append(line)
    path.write_text("\n".join(kept_lines).rstrip("\n") + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return migrated


def write_project_env_pointer(reference_path: str | Path, env_path: str | Path) -> Path:
    """Point the project-specific env reference file at the provided credentials file."""
    pointer_path = _resolve_project_root(Path(reference_path)) / PROJECT_ENV_POINTER_FILENAME
    pointer_path.write_text(f"{Path(env_path).expanduser()}\n", encoding="utf-8")
    return pointer_path


def slugify(name: str) -> str:
    """Normalize a free-text client name into a filesystem/Keychain-safe slug.

    The slug becomes both the credentials-file suffix (``.asana_jira_env_<slug>``)
    and the per-profile database filename, so it is restricted to ``[a-z0-9_-]``.
    Falls back to ``"client"`` when nothing usable remains.
    """
    lowered = (name or "").strip().lower()
    collapsed = re.sub(r"\s+", "_", lowered)
    cleaned = re.sub(r"[^a-z0-9_-]", "", collapsed)
    cleaned = cleaned.strip("_-")
    return cleaned or "client"


def named_profile_env_path(name: str) -> Path:
    """Return the credentials-file path for a named client profile."""
    return DEFAULT_ENV_PATH.with_name(f"{NAMED_PROFILE_PREFIX}{slugify(name)}")


def profile_slug(env_path: str | Path) -> str:
    """Return the profile slug derived from a credentials-file name.

    The bare default file (``.asana_jira_env``) maps to ``""`` (the legacy/default
    profile, which keeps the unsuffixed database). ``.asana_jira_env_<slug>`` maps to
    ``<slug>``; any other basename falls back to a sanitized slug of the whole name.
    """
    basename = Path(env_path).name
    if basename == DEFAULT_ENV_PATH.name:
        return ""
    if basename.startswith(NAMED_PROFILE_PREFIX):
        return slugify(basename[len(NAMED_PROFILE_PREFIX):])
    return slugify(basename)


def read_profile_name(env_path: str | Path) -> Optional[str]:
    """Read just the ``CLIENT_NAME`` value from a credentials file (None if absent/unreadable)."""
    path = Path(env_path).expanduser()
    try:
        data = _load_env_file(path)
    except (OSError, ValueError):
        return None
    value = (data.get(ENV_VAR_NAMES["client_name"]) or "").strip()
    return value or None


def read_profile_jira_urls(env_path: str | Path) -> Tuple[Optional[str], Optional[str]]:
    """Read ``(JIRA_BASE_URL, JIRA_SANDBOX_BASE_URL)`` from a specific credentials file.

    Non-secret values read straight from the file (no Keychain), tolerant of a
    missing/unparsable file. Used for display, e.g. the interactive CLI header.
    """
    path = Path(env_path).expanduser()
    try:
        data = _load_env_file(path)
    except (OSError, ValueError):
        return (None, None)
    base = (data.get(ENV_VAR_NAMES["jira_base_url"]) or "").strip() or None
    sandbox = (data.get(ENV_VAR_NAMES["jira_sandbox_base_url"]) or "").strip() or None
    return (base, sandbox)


def list_credential_profiles() -> List[Tuple[Path, Optional[str]]]:
    """Discover candidate credentials profiles in the home directory.

    Returns ``(path, client_name)`` for each ``~/.asana_jira_env*`` file, sorted by
    path. The project pointer file is excluded, and unparsable files are skipped.
    """
    profiles: List[Tuple[Path, Optional[str]]] = []
    home = DEFAULT_ENV_PATH.parent
    for candidate in sorted(home.glob(f"{DEFAULT_ENV_PATH.name}*")):
        if candidate.name == PROJECT_ENV_POINTER_FILENAME:
            continue
        if not candidate.is_file():
            continue
        try:
            _load_env_file(candidate)
        except (OSError, ValueError):
            continue
        profiles.append((candidate, read_profile_name(candidate)))
    return profiles
