"""Helpers for lazily backfilling user records encountered during syncs."""

from __future__ import annotations

from datetime import datetime
from typing import Iterable, Optional, Set

from src.lib.http_client import AsanaHttpClient
from src.tasks.users import USER_FIELDS_REQUIRED, fetch_user


class UserBackfill:
    """Caches user lookups and hydrates missing profiles on demand."""

    def __init__(self, client: AsanaHttpClient, database):
        self._client = client
        self._database = database
        self._known_user_gids: Set[str] = set()

    def prime(self, connection) -> None:
        """Seed the cache with users already persisted to SQLite."""
        rows = connection.execute("SELECT gid FROM users WHERE gid IS NOT NULL")
        self._known_user_gids = {row["gid"] for row in rows}

    def ensure_users(self, connection, user_gids: Iterable[Optional[str]], synced_at: datetime) -> None:
        """Ensure all provided user GIDs are stored in SQLite."""
        for gid in user_gids:
            self.ensure_user(connection, gid, synced_at)

    def ensure_user(self, connection, user_gid: Optional[str], synced_at: datetime) -> Optional[dict]:
        """Fetch and persist a user when it is not yet cached."""
        if not user_gid:
            return None
        if user_gid in self._known_user_gids:
            return None
        user = fetch_user(self._client, user_gid, opt_fields=USER_FIELDS_REQUIRED)
        self._database.upsert_user(connection, user, synced_at)
        self._known_user_gids.add(user_gid)
        return user
