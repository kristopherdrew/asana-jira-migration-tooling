from unittest import TestCase

from src.lib.asana_urls import (
    build_portfolio_url,
    build_project_url,
    build_team_url,
    build_user_task_list_url,
    build_user_url,
)


class BuildUrlTest(TestCase):
    def test_build_team_url(self) -> None:
        self.assertEqual(build_team_url("123"), "https://app.asana.com/0/123/")
        self.assertEqual(build_team_url(None), "")

    def test_build_project_url(self) -> None:
        self.assertEqual(
            build_project_url("ws", "proj"),
            "https://app.asana.com/1/ws/project/proj",
        )
        self.assertEqual(build_project_url("ws", None), "")
        self.assertEqual(build_project_url(None, "proj"), "")

    def test_build_portfolio_url(self) -> None:
        self.assertEqual(
            build_portfolio_url("ws", "pf"),
            "https://app.asana.com/1/ws/portfolio/pf",
        )
        self.assertEqual(build_portfolio_url("ws", None), "")
        self.assertEqual(build_portfolio_url(None, "pf"), "")

    def test_build_user_url(self) -> None:
        self.assertEqual(
            build_user_url("ws", "user"),
            "https://app.asana.com/1/ws/profile/user",
        )
        self.assertEqual(build_user_url(None, "user"), "")

    def test_build_user_task_list_url(self) -> None:
        self.assertEqual(
            build_user_task_list_url("utl_1"),
            "https://app.asana.com/0/utl_1/list",
        )

    def test_build_user_task_list_url_blank_when_missing(self) -> None:
        self.assertEqual(build_user_task_list_url(None), "")
        self.assertEqual(build_user_task_list_url(""), "")
