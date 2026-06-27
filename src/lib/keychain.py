"""macOS Keychain access for secret credentials via the stdlib ``security`` CLI.

Only the two API tokens (``ASANA_TOKEN``, ``ATLASSIAN_TOKEN``) are stored here;
non-secret config stays in the env file. Entries are generic passwords under a
single service, namespaced per credentials file so multiple clients don't
collide:

    service = "asana-jira-migration"
    account = "<profile-slug>:<ENV_VAR>"   e.g. "client_a:ASANA_TOKEN" ("default" for the
                                          default profile)

Accounts are namespaced by the profile *slug* (see ``config.profile_slug``) so a
profile's secrets survive the config file being relocated/renamed. The older
basename-based scheme (``account_for`` / ``legacy_account_for``) is retained to
read pre-migration entries.

Stdlib-only (``subprocess`` + the system ``security`` binary). Every helper is a
no-op/``None`` when Keychain isn't available (non-macOS or missing binary), so
callers can transparently fall back to the env file.

Caveat: ``security ... -w <secret>`` passes the secret as a process argument,
briefly visible to other local processes. ``add-generic-password`` offers no
stdin path, and this only runs during interactive credential writes, so the
exposure window is small and local.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

KEYCHAIN_SERVICE = "asana-jira-migration"

# How an empty slug (the default profile) is represented in the account string.
SLUG_DEFAULT = "default"

# `security find-generic-password` exits 44 when the item does not exist.
_ITEM_NOT_FOUND_EXIT = 44


def is_available() -> bool:
    """Return True only on macOS with the ``security`` binary on PATH."""
    return sys.platform == "darwin" and shutil.which("security") is not None


def account_for_slug(slug: str, env_var_name: str) -> str:
    """Namespace a Keychain account by the profile slug + env var.

    An empty slug (the default profile) maps to ``SLUG_DEFAULT``. The slug is
    computed by the caller (``config.profile_slug``) to keep this module free of a
    dependency on ``config``.
    """
    return f"{slug or SLUG_DEFAULT}:{env_var_name}"


def legacy_account_for(env_path, env_var_name: str) -> str:
    """Old basename-based account, kept to read pre-migration Keychain entries."""
    return f"{Path(env_path).name}:{env_var_name}"


# Backwards-compatible alias for the original basename-based scheme.
account_for = legacy_account_for


def get_secret(account: str) -> Optional[str]:
    """Return the stored secret for ``account``, or ``None`` if absent/unavailable."""
    if not is_available():
        return None
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", account, "-w"],
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, OSError):
        return None
    if result.returncode != 0:
        return None
    secret = result.stdout.rstrip("\n")
    return secret or None


def set_secret(account: str, secret: str) -> None:
    """Create or update (``-U``) the Keychain item for ``account``.

    Raises ``RuntimeError`` if the ``security`` call fails."""
    if not is_available():
        raise RuntimeError("macOS Keychain is not available on this system.")
    try:
        result = subprocess.run(
            [
                "security",
                "add-generic-password",
                "-U",
                "-s",
                KEYCHAIN_SERVICE,
                "-a",
                account,
                "-w",
                secret,
            ],
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, OSError) as exc:
        raise RuntimeError(f"Unable to invoke the macOS 'security' tool: {exc}") from exc
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to store secret in Keychain (exit {result.returncode}): {result.stderr.strip()}"
        )


def delete_secret(account: str) -> bool:
    """Remove the Keychain item for ``account``. Return True if one was deleted."""
    if not is_available():
        return False
    try:
        result = subprocess.run(
            ["security", "delete-generic-password", "-s", KEYCHAIN_SERVICE, "-a", account],
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, OSError):
        return False
    return result.returncode == 0
