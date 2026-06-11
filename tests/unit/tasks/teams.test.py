from unittest import TestCase

from src.tasks.teams import fetch_team_memberships, fetch_workspace_teams


class DummyClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get_paginated(self, endpoint, params=None):
        self.calls.append((endpoint, params))
        for item in self.payload:
            yield item


class FetchWorkspaceTeamsTest(TestCase):
    def test_uses_workspace_endpoint(self) -> None:
        client = DummyClient(payload=[{"gid": "team"}])
        teams = list(fetch_workspace_teams(client, "123"))

        self.assertEqual(teams[0]["gid"], "team")
        endpoint, params = client.calls[0]
        self.assertEqual(endpoint, "/workspaces/123/teams")
        self.assertIn("opt_fields", params)


class FetchTeamMembershipsTest(TestCase):
    def test_uses_team_membership_endpoint(self) -> None:
        client = DummyClient(payload=[{"gid": "membership"}])
        memberships = fetch_team_memberships(client, "321")

        self.assertEqual(memberships[0]["gid"], "membership")
        endpoint, params = client.calls[0]
        self.assertEqual(endpoint, "/teams/321/team_memberships")
        self.assertIn("opt_fields", params)
