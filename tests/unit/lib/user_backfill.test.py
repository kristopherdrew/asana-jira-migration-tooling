from datetime import datetime, timezone
from unittest import TestCase

from src.lib.database import Database
from src.lib.user_backfill import UserBackfill


class DummyClient:
    def __init__(self):
        self.calls = []

    def get(self, endpoint, params=None):
        self.calls.append((endpoint, params))
        user_gid = endpoint.split("/")[-1]
        return {
            "data": {
                "gid": user_gid,
                "name": f"User {user_gid}",
                "resource_type": "user",
                "email": f"{user_gid}@example.com",
                "is_active": True,
                "workspaces": [],
            }
        }


class DummyDatabase(Database):
    pass


def sync_time():
    return datetime(2025, 10, 31, tzinfo=timezone.utc)


class UserBackfillTest(TestCase):
    def setUp(self) -> None:
        self.database = DummyDatabase(":memory:")
        self.connection = self.database.connect()
        self.database.initialize(self.connection)
        self.client = DummyClient()
        self.backfill = UserBackfill(self.client, self.database)

    def tearDown(self) -> None:
        self.connection.close()

    def test_ensure_user_fetches_missing_user(self) -> None:
        self.backfill.prime(self.connection)
        self.backfill.ensure_user(self.connection, "123", sync_time())
        row = self.connection.execute("SELECT gid, email FROM users WHERE gid = '123'").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["email"], "123@example.com")
        self.assertEqual(len(self.client.calls), 1)

    def test_existing_user_is_not_fetched_again(self) -> None:
        self.backfill.prime(self.connection)
        self.backfill.ensure_user(self.connection, "123", sync_time())
        self.backfill.ensure_user(self.connection, "123", sync_time())
        self.assertEqual(len(self.client.calls), 1)
