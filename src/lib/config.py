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
# bare ``~/.asana_jira_env`` is the legacy/default profile. These home-directory
# files are still discovered for one-time migration and read as a fallback.
NAMED_PROFILE_PREFIX = ".asana_jira_env_"

# Project-local profile storage (the current scheme). Non-secret config lives in
# ``<project>/profiles/<slug>.env`` (``default.env`` for the default profile) and
# the active profile is named by ``<project>/profiles/.asana_jira_active``.
PROFILES_DIRNAME = "profiles"
ACTIVE_MARKER_FILENAME = ".asana_jira_active"
DEFAULT_PROFILE_FILENAME = "default.env"
PROFILE_FILE_SUFFIX = ".env"

ENV_VAR_NAMES = {
    "client_name": "CLIENT_NAME",
    "token": "ASANA_TOKEN",
    "workspace_gid": "ASANA_WORKSPACE_GID",
    "skip_ssl_verification": "SKIP_SSL_VERIFICATION",
    "atlassian_username": "ATLASSIAN_USERNAME",
    "atlassian_token": "ATLASSIAN_TOKEN",
    "jira_base_url": "JIRA_BASE_URL",
    "jira_sandbox_base_url": "JIRA_SANDBOX_BASE_URL",
    "jira_sites": "JIRA_SITES",
    "jira_default_site": "JIRA_DEFAULT_SITE",
}

# The secret fields stored in the macOS Keychain (when available); everything
# else stays in the plaintext env file.
SECRET_FIELDS = ("token", "atlassian_token")
SECRET_ENV_VARS = tuple(ENV_VAR_NAMES[field] for field in SECRET_FIELDS)
ACTIVE_JIRA_SITE_ENV = "ASANA_JIRA_SITE_KEY"


@dataclass(frozen=True)
class JiraSite:
    key: str
    name: str
    base_url: str


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
    jira_sites: Tuple[JiraSite, ...] = ()
    jira_default_site: Optional[str] = None


def resolve_jira_site(credentials: "Credentials", site_key: Optional[str] = None) -> JiraSite:
    """Resolve a named Jira site, defaulting to the profile's persisted choice."""
    sites = {site.key: site for site in credentials.jira_sites}
    selected = (site_key or credentials.jira_default_site or "").strip()
    if selected and selected in sites:
        os.environ[ACTIVE_JIRA_SITE_ENV] = selected
        return sites[selected]
    if selected:
        raise ValueError(f"Unknown Jira site {selected!r}. Available sites: {', '.join(sites) or 'none'}.")
    if len(sites) == 1:
        return next(iter(sites.values()))
    raise ValueError("No default Jira site configured. Set JIRA_DEFAULT_SITE or pass --jira-site.")


def resolve_jira_base_url(
    credentials: "Credentials", prod: bool = False, site_key: Optional[str] = None
) -> str:
    """Return the configured Jira base URL for the selected environment.

    Defaults to the sandbox (``JIRA_SANDBOX_BASE_URL``); pass ``prod=True`` to
    target production (``JIRA_BASE_URL``). Raises ``ValueError`` with actionable
    guidance when the chosen environment's URL is not set in the credentials.
    """
    if site_key or credentials.jira_sites:
        selected = "production_jira" if prod and not site_key else site_key
        return resolve_jira_site(credentials, selected).base_url.rstrip("/")
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


def _load_jira_sites(env_file_data: Dict[str, str]) -> Tuple[Tuple[JiraSite, ...], Optional[str]]:
    raw_keys = os.environ.get("JIRA_SITES") or env_file_data.get("JIRA_SITES") or ""
    keys = [slugify(value) for value in raw_keys.split(",") if value.strip()]
    sites: List[JiraSite] = []
    for key in dict.fromkeys(keys):
        prefix = f"JIRA_SITE_{key.upper()}"
        name = os.environ.get(f"{prefix}_NAME") or env_file_data.get(f"{prefix}_NAME") or key
        url = os.environ.get(f"{prefix}_URL") or env_file_data.get(f"{prefix}_URL") or ""
        if url.strip():
            sites.append(JiraSite(key, name.strip(), url.strip().rstrip("/")))
    default = (os.environ.get("JIRA_DEFAULT_SITE") or env_file_data.get("JIRA_DEFAULT_SITE") or "").strip()
    return tuple(sites), (slugify(default) if default else None)


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


def project_profiles_dir(reference_path: str | Path = ".") -> Path:
    """Return the project-local ``profiles/`` directory for a reference path."""
    return _resolve_project_root(Path(reference_path)) / PROFILES_DIRNAME


def project_profile_path(slug: str, reference_path: str | Path = ".") -> Path:
    """Return the ``profiles/<slug>.env`` path (``default.env`` for the default profile)."""
    name = DEFAULT_PROFILE_FILENAME if not slug else f"{slug}{PROFILE_FILE_SUFFIX}"
    return project_profiles_dir(reference_path) / name


def _active_marker_path(reference_path: str | Path = ".") -> Path:
    return project_profiles_dir(reference_path) / ACTIVE_MARKER_FILENAME


def _legacy_active_marker_path(reference_path: str | Path = ".") -> Path:
    return _resolve_project_root(Path(reference_path)) / ACTIVE_MARKER_FILENAME


def _existing_active_marker_path(reference_path: str | Path = ".") -> Path:
    current = _active_marker_path(reference_path)
    return current if current.exists() else _legacy_active_marker_path(reference_path)


def has_active_marker(reference_path: str | Path = ".") -> bool:
    """Return True when the project has an active-profile marker file."""
    return _existing_active_marker_path(reference_path).exists()


def read_active_slug(reference_path: str | Path = ".") -> str:
    """Read the active profile slug ('' for the default profile).

    Tolerant of a missing/blank marker file; ``default`` (or empty) maps to ''.
    """
    path = _existing_active_marker_path(reference_path)
    if not path.exists():
        return ""
    raw = ""
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                raw = _strip_wrapping_quotes(stripped).strip()
                break
    except OSError:
        return ""
    if raw in ("", "default", keychain.SLUG_DEFAULT):
        return ""
    return slugify(raw)


def write_active_slug(slug: str, reference_path: str | Path = ".") -> Path:
    """Write the active profile marker (``default`` for the default profile)."""
    path = _active_marker_path(reference_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{slug or 'default'}\n", encoding="utf-8")
    return path


def _legacy_home_path_for_slug(slug: str) -> Path:
    """Return the home-directory credentials file for a slug (pre-migration fallback)."""
    if not slug:
        return DEFAULT_ENV_PATH
    return DEFAULT_ENV_PATH.with_name(f"{NAMED_PROFILE_PREFIX}{slug}")


def _resolve_env_path(reference_path: Path) -> Path:
    override = os.environ.get("ASANA_JIRA_ENV_FILE", "").strip()
    if override:
        return Path(override).expanduser()

    root = _resolve_project_root(reference_path)

    # 1. Active-slug marker -> the project profile file for that slug.
    if has_active_marker(root):
        slug = read_active_slug(root)
        candidate = project_profile_path(slug, root)
        if candidate.exists():
            return candidate
        # Marker present but project file not created yet: fall back to the home
        # file for that slug (keeps standalone scripts working pre-migration);
        # otherwise return the intended path so callers get a clear target.
        legacy = _legacy_home_path_for_slug(slug)
        return legacy if legacy.exists() else candidate

    # 2. No marker: the default project profile file, if it exists.
    default_project = project_profile_path("", root)
    if default_project.exists():
        return default_project

    # 3. Legacy project pointer (``.asana_jira_env_path``).
    project_override = _resolve_project_env_pointer(root / PROJECT_ENV_POINTER_FILENAME)
    if project_override:
        return project_override

    # 4. Home default.
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
        slug = profile_slug(env_path)
        stored = keychain.get_secret(keychain.account_for_slug(slug, env_name))
        if stored:
            return stored
        # Fallback: read pre-migration entries keyed by the file basename, so
        # un-migrated installs (and standalone scripts) still resolve secrets.
        legacy = keychain.get_secret(keychain.legacy_account_for(env_path, env_name))
        if legacy:
            return legacy
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
            root = _resolve_project_root(reference_path)
            raise FileNotFoundError(
                f"No credentials found. Set environment variables, create {env_path}, "
                f"or run the interactive CLI to create a profile in {root / PROFILES_DIRNAME} "
                f"(selected via {project_profiles_dir(root) / ACTIVE_MARKER_FILENAME})."
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
    jira_sites, jira_default_site = _load_jira_sites(env_file_data)
    if not jira_sites:
        legacy_sites = []
        if jira_base_url:
            legacy_sites.append(JiraSite("production_jira", "Production Jira", jira_base_url.rstrip("/")))
        if jira_sandbox_base_url:
            legacy_sites.append(JiraSite("sandbox_jira", "Sandbox Jira", jira_sandbox_base_url.rstrip("/")))
        jira_sites = tuple(legacy_sites)
        jira_default_site = "sandbox_jira" if jira_sandbox_base_url else ("production_jira" if jira_base_url else None)

    return Credentials(
        token=token.strip(),
        workspace_gid=workspace_gid.strip(),
        skip_ssl_verification=skip_ssl_bool,
        atlassian_username=atlassian_username,
        atlassian_token=atlassian_token,
        jira_base_url=jira_base_url,
        jira_sandbox_base_url=jira_sandbox_base_url,
        client_name=client_name,
        jira_sites=jira_sites,
        jira_default_site=jira_default_site,
    )


def _format_env_assignment(key: str, value: str) -> str:
    return f"export {key}={shlex.quote(value)}"


def _store_secrets_in_keychain(env_path: Path, credentials: Credentials) -> None:
    """Persist the two tokens to the Keychain (delete the entry when a value is cleared)."""
    secret_values = {
        "ASANA_TOKEN": credentials.token,
        "ATLASSIAN_TOKEN": credentials.atlassian_token,
    }
    slug = profile_slug(env_path)
    for env_name, value in secret_values.items():
        account = keychain.account_for_slug(slug, env_name)
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
    preserved_archive_csv_settings: List[str] = []
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            body = stripped[7:].strip() if stripped.startswith("export ") else stripped
            key = body.split("=", 1)[0].strip() if "=" in body else ""
            if re.fullmatch(r"JIRA_SITE_[A-Z0-9_]+_CSV_ARCHIVE_[A-Z0-9_]+", key):
                preserved_archive_csv_settings.append(line)

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
    if credentials.jira_sites:
        lines.append(_format_env_assignment("JIRA_SITES", ",".join(site.key for site in credentials.jira_sites)))
        if credentials.jira_default_site:
            lines.append(_format_env_assignment("JIRA_DEFAULT_SITE", credentials.jira_default_site))
        for site in credentials.jira_sites:
            prefix = f"JIRA_SITE_{site.key.upper()}"
            lines.append(_format_env_assignment(f"{prefix}_NAME", site.name))
            lines.append(_format_env_assignment(f"{prefix}_URL", site.base_url.rstrip("/")))
    lines.extend(preserved_archive_csv_settings)

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
    slug = profile_slug(path)
    migrated: List[str] = []
    for env_name in SECRET_ENV_VARS:
        value = data.get(env_name)
        if value:
            keychain.set_secret(keychain.account_for_slug(slug, env_name), value)
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

    The slug becomes the profile filename (``profiles/<slug>.env``), the per-profile
    database filename, and the Keychain account namespace, so it is restricted to
    ``[a-z0-9_-]``. Falls back to ``"client"`` when nothing usable remains. The
    reserved value ``"default"`` is remapped to ``"default-client"`` so a named
    profile can never collide with the default profile's file/account.
    """
    lowered = (name or "").strip().lower()
    collapsed = re.sub(r"\s+", "_", lowered)
    cleaned = re.sub(r"[^a-z0-9_-]", "", collapsed)
    cleaned = cleaned.strip("_-")
    if cleaned == "default":
        return "default-client"
    return cleaned or "client"


def named_profile_env_path(name: str) -> Path:
    """Return the credentials-file path for a named client profile."""
    return DEFAULT_ENV_PATH.with_name(f"{NAMED_PROFILE_PREFIX}{slugify(name)}")


def profile_slug(env_path: str | Path) -> str:
    """Return the profile slug derived from a credentials-file name.

    Both the current project names and the legacy home names are understood, and
    all map to the same slug (so the per-profile database stays stable across the
    one-time migration):

    - ``profiles/default.env`` / ``~/.asana_jira_env``         -> ``""`` (default profile)
    - ``profiles/<slug>.env``  / ``~/.asana_jira_env_<slug>``  -> ``<slug>``

    Any other basename falls back to a sanitized slug of the whole name.
    """
    basename = Path(env_path).name
    # Current project profile files.
    if basename == DEFAULT_PROFILE_FILENAME:
        return ""
    if basename.endswith(PROFILE_FILE_SUFFIX):
        return slugify(basename[: -len(PROFILE_FILE_SUFFIX)])
    # Legacy home-directory files.
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


def read_profile_jira_sites(env_path: str | Path) -> Tuple[Tuple[JiraSite, ...], Optional[str]]:
    """Read named Jira sites for CLI display without loading profile secrets."""
    path = Path(env_path).expanduser()
    try:
        data = _load_env_file(path)
    except (OSError, ValueError):
        return (), None
    sites, default = _load_jira_sites(data)
    if sites:
        return sites, default
    legacy = []
    production = (data.get("JIRA_BASE_URL") or "").strip()
    sandbox = (data.get("JIRA_SANDBOX_BASE_URL") or "").strip()
    if production:
        legacy.append(JiraSite("production_jira", "Production Jira", production.rstrip("/")))
    if sandbox:
        legacy.append(JiraSite("sandbox_jira", "Sandbox Jira", sandbox.rstrip("/")))
    return tuple(legacy), ("sandbox_jira" if sandbox else ("production_jira" if production else None))


def write_profile_default_jira_site(env_path: str | Path, site_key: str) -> None:
    """Persist the default Jira site while preserving all other profile lines."""
    path = Path(env_path).expanduser()
    key = slugify(site_key)
    lines = path.read_text(encoding="utf-8").splitlines()
    replacement = _format_env_assignment("JIRA_DEFAULT_SITE", key)
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("export "):
            stripped = stripped[7:].strip()
        if stripped.startswith("JIRA_DEFAULT_SITE="):
            lines[index] = replacement
            break
    else:
        lines.append(replacement)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def read_profile_values(env_path: str | Path, keys: Iterable[str]) -> Dict[str, str]:
    """Read selected non-secret profile assignments without environment overrides."""
    path = Path(env_path).expanduser()
    try:
        data = _load_env_file(path)
    except (OSError, ValueError):
        return {}
    return {key: data[key] for key in keys if key in data and data[key] != ""}


def write_profile_values(
    env_path: str | Path,
    values: Dict[str, Optional[str]],
) -> None:
    """Upsert or remove non-secret profile assignments while preserving other lines.

    A ``None`` value removes the assignment. Empty strings remain valid persisted
    values, although callers normally use ``None`` when clearing a setting.
    """
    path = Path(env_path).expanduser()
    lines = path.read_text(encoding="utf-8").splitlines()
    pending = dict(values)
    rewritten: List[str] = []
    for line in lines:
        stripped = line.strip()
        body = stripped[7:].strip() if stripped.startswith("export ") else stripped
        key = body.split("=", 1)[0].strip() if "=" in body else ""
        if key not in pending:
            rewritten.append(line)
            continue
        value = pending.pop(key)
        if value is not None:
            rewritten.append(_format_env_assignment(key, value))
    for key, value in pending.items():
        if value is not None:
            rewritten.append(_format_env_assignment(key, value))
    path.write_text("\n".join(rewritten) + "\n", encoding="utf-8")


def write_profile_jira_sites(
    env_path: str | Path,
    sites: Tuple[JiraSite, ...],
    default_site_key: Optional[str],
) -> None:
    """Replace named-site assignments while preserving unrelated profile values."""
    path = Path(env_path).expanduser()
    kept = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()[7:].strip() if line.strip().startswith("export ") else line.strip()
        key = stripped.split("=", 1)[0]
        if key in {"JIRA_SITES", "JIRA_DEFAULT_SITE"} or re.fullmatch(
            r"JIRA_SITE_[A-Z0-9_]+_(?:NAME|URL)", key
        ):
            continue
        kept.append(line)
    kept.append(_format_env_assignment("JIRA_SITES", ",".join(site.key for site in sites)))
    if default_site_key:
        kept.append(_format_env_assignment("JIRA_DEFAULT_SITE", slugify(default_site_key)))
    for site in sites:
        prefix = f"JIRA_SITE_{site.key.upper()}"
        kept.append(_format_env_assignment(f"{prefix}_NAME", site.name))
        kept.append(_format_env_assignment(f"{prefix}_URL", site.base_url.rstrip("/")))
    path.write_text("\n".join(kept) + "\n", encoding="utf-8")


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


def list_project_profiles(reference_path: str | Path = ".") -> List[Tuple[Path, Optional[str]]]:
    """Discover project-local profiles in ``<project>/profiles/``.

    Returns ``(path, client_name)`` for each ``profiles/*.env`` file, sorted by
    path. Unparsable files are skipped.
    """
    profiles: List[Tuple[Path, Optional[str]]] = []
    pdir = project_profiles_dir(reference_path)
    if not pdir.is_dir():
        return profiles
    for candidate in sorted(pdir.glob(f"*{PROFILE_FILE_SUFFIX}")):
        if not candidate.is_file():
            continue
        try:
            _load_env_file(candidate)
        except (OSError, ValueError):
            continue
        profiles.append((candidate, read_profile_name(candidate)))
    return profiles


def migrate_home_profiles_into_project(
    reference_path: str | Path = ".",
) -> List[Tuple[str, Path, Path]]:
    """Copy any ``~/.asana_jira_env*`` home profiles into ``profiles/<slug>.env``.

    For each discovered home profile this copies the file verbatim (preserving any
    plaintext tokens on non-macOS) and, when the Keychain is available, re-keys its
    secrets from the legacy basename account to the slug account. Existing project
    files are skipped (idempotent) and home files are left intact.

    Returns ``[(slug, home_path, project_path), ...]`` for the profiles migrated.
    """
    root = _resolve_project_root(Path(reference_path))
    migrated: List[Tuple[str, Path, Path]] = []
    keychain_available = keychain.is_available()
    for home_path, _client_name in list_credential_profiles():
        slug = profile_slug(home_path)
        target = project_profile_path(slug, root)
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            target.write_text(home_path.read_text(encoding="utf-8"), encoding="utf-8")
        except OSError:
            continue
        try:
            target.chmod(0o600)
        except OSError:
            pass
        if keychain_available:
            for env_name in SECRET_ENV_VARS:
                value = keychain.get_secret(keychain.legacy_account_for(home_path, env_name))
                if value:
                    keychain.set_secret(keychain.account_for_slug(slug, env_name), value)
        migrated.append((slug, home_path, target))
    return migrated
