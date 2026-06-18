from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from src.lib.database import Database
from src.lib.team_picker import search_team


class SearchTeamTest(TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "asana.sqlite"
        self.database = Database(str(self.db_path))
        now = datetime.now(timezone.utc)
        with self.database.connect() as connection:
            self.database.initialize(connection)
            self.database.upsert_team(connection, {"gid": "team_1", "name": "Platform"}, now)
            self.database.upsert_team(connection, {"gid": "team_2", "name": "Payments"}, now)
            self.database.upsert_team(connection, {"gid": "team_3", "name": "Design"}, now)
            connection.commit()
        self.connection = self.database.connect()
        self.addCleanup(self.connection.close)

    def test_returns_none_when_no_teams(self) -> None:
        empty_db = Database(str(Path(self.temp_dir.name) / "empty.sqlite"))
        with empty_db.connect() as connection:
            empty_db.initialize(connection)
            self.assertIsNone(search_team(connection))

    def test_filters_by_search_term_and_selects(self) -> None:
        # Search "pay" -> single match "Payments", then pick "1".
        with patch("builtins.input", side_effect=["pay", "1"]):
            result = search_team(self.connection)
        self.assertEqual(result, ("team_2", "Payments"))

    def test_quit_during_search_returns_none(self) -> None:
        with patch("builtins.input", side_effect=["q"]):
            self.assertIsNone(search_team(self.connection))

    def test_reprompts_when_no_match_then_quits(self) -> None:
        with patch("builtins.input", side_effect=["zzz", "q"]):
            self.assertIsNone(search_team(self.connection))

    def test_new_search_command_restarts(self) -> None:
        # Blank lists all -> "s" to search again -> "design" -> pick "1".
        with patch("builtins.input", side_effect=["", "s", "design", "1"]):
            result = search_team(self.connection)
        self.assertEqual(result, ("team_3", "Design"))
