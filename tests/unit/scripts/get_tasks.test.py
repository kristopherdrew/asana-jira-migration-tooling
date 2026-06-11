from argparse import Namespace
from datetime import datetime, timezone
from unittest import TestCase
from unittest.mock import patch

from src.lib.database import Database

from src.lib.project_scope import determine_projects
from src.tasks.task_processing import collect_custom_field_gids, merge_task_payload
from src.tasks.tasks import collect_user_gids_from_task, fetch_project_tasks


def sync_time():
    return datetime(2025, 10, 31, tzinfo=timezone.utc).isoformat()


class GetTasksHelpersTest(TestCase):
    def setUp(self) -> None:
        self.database = Database(":memory:")
        self.connection = self.database.connect()
        self.database.initialize(self.connection)
        # Seed a default project and team
        self.connection.execute(
            """
            INSERT INTO teams (gid, name, synced_at)
            VALUES (?, ?, ?)
            """,
            ("team_1", "Team One", sync_time()),
        )
        default_project_name = "Default Project"
        self.connection.execute(
            """
            INSERT INTO projects (
                gid, name, resource_subtype, archived, permalink_url,
                created_at, modified_at, start_on, due_on, completed,
                completed_at, completed_by_gid, owner_gid, owner_name,
                team_gid, team_name, privacy_setting, notes, html_notes, synced_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "proj_default",
                default_project_name,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                "team_1",
                "Team One",
                None,
                None,
                None,
                sync_time(),
            ),
        )
        self.connection.execute(
            """
            INSERT INTO projects (
                gid, name, resource_subtype, archived, permalink_url,
                created_at, modified_at, start_on, due_on, completed,
                completed_at, completed_by_gid, owner_gid, owner_name,
                team_gid, team_name, privacy_setting, notes, html_notes, synced_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "proj_other",
                "Other Project",
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                "team_1",
                "Team One",
                None,
                None,
                None,
                sync_time(),
            ),
        )
        self.connection.execute(
            """
            INSERT INTO projects (
                gid, name, resource_subtype, archived, permalink_url,
                created_at, modified_at, start_on, due_on, completed,
                completed_at, completed_by_gid, owner_gid, owner_name,
                team_gid, team_name, privacy_setting, notes, html_notes, synced_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "proj_archived",
                "Archived Project",
                None,
                1,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                "team_1",
                "Team One",
                None,
                None,
                None,
                sync_time(),
            ),
        )
        self.connection.commit()
        self.default_project_name = default_project_name

    def tearDown(self) -> None:
        self.connection.close()

    def test_collect_custom_field_gids_returns_all_ids(self) -> None:
        task = {"custom_fields": [{"gid": "cf_1"}, {"gid": "cf_2"}]}
        gids = collect_custom_field_gids(task)
        self.assertEqual(gids, {"cf_1", "cf_2"})

    def test_merge_task_payload_preserves_missing_fields(self) -> None:
        listing = {"gid": "task_1", "memberships": [{"project": {"gid": "proj"}}]}
        detail = {"gid": "task_1", "memberships": []}
        merged = merge_task_payload(listing, detail)
        self.assertEqual(merged["memberships"][0]["project"]["gid"], "proj")

    def test_determine_projects_with_project_gid(self) -> None:
        args = Namespace(
            project_gid="proj_other",
            team=False,
            team_gids=None,
            all_teams=False,
            exclude_team_gids=None,
        )
        projects = determine_projects(
            connection=self.connection,
            project_gid=args.project_gid,
            use_team_picker=args.team,
            team_gids=args.team_gids,
            all_teams=args.all_teams,
            exclude_team_gids=args.exclude_team_gids,
            team_selector=lambda conn: None,
        )
        self.assertEqual(len(projects), 1)
        project = projects[0]
        self.assertEqual(project["project_gid"], "proj_other")
        self.assertEqual(project["project_name"], "Other Project")
        self.assertEqual(project["team_gid"], "team_1")

    @patch("scripts.get_tasks.choose_team")
    def test_determine_projects_with_team_selection(self, mock_choose_team) -> None:
        mock_choose_team.return_value = ("team_1", "Team One")
        args = Namespace(
            project_gid=None,
            team=True,
            team_gids=None,
            all_teams=False,
            exclude_team_gids=None,
        )
        projects = determine_projects(
            connection=self.connection,
            project_gid=args.project_gid,
            use_team_picker=args.team,
            team_gids=args.team_gids,
            all_teams=args.all_teams,
            exclude_team_gids=args.exclude_team_gids,
            team_selector=mock_choose_team,
        )
        mock_choose_team.assert_called_once()
        self.assertEqual(len(projects), 3)
        self.assertTrue(all(project["team_gid"] == "team_1" for project in projects))

    def test_determine_projects_with_team_gids(self) -> None:
        args = Namespace(
            project_gid=None,
            team=False,
            team_gids="team_1",
            all_teams=False,
            exclude_team_gids=None,
        )
        projects = determine_projects(
            connection=self.connection,
            project_gid=args.project_gid,
            use_team_picker=args.team,
            team_gids=args.team_gids,
            all_teams=args.all_teams,
            exclude_team_gids=args.exclude_team_gids,
            team_selector=lambda conn: None,
        )
        self.assertEqual(len(projects), 3)
        self.assertTrue(all(project["team_gid"] == "team_1" for project in projects))

    def test_determine_projects_filters_archived_scope(self) -> None:
        args = Namespace(
            project_gid=None,
            team=False,
            team_gids="team_1",
            all_teams=False,
            exclude_team_gids=None,
        )
        projects = determine_projects(
            connection=self.connection,
            project_gid=args.project_gid,
            use_team_picker=args.team,
            team_gids=args.team_gids,
            all_teams=args.all_teams,
            exclude_team_gids=args.exclude_team_gids,
            team_selector=lambda conn: None,
            archived_scope="archived",
        )
        self.assertEqual([project["project_gid"] for project in projects], ["proj_archived"])
