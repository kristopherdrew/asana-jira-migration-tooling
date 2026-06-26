from unittest import TestCase

from src.tasks.portfolios import (
    PORTFOLIO_FIELDS_DEFAULT,
    fetch_portfolio,
    fetch_portfolio_custom_field_settings,
    fetch_portfolio_items,
    fetch_portfolio_memberships,
    fetch_portfolio_status_updates,
    fetch_user_portfolios,
)


class DummyClient:
    def __init__(self, paginated=None, single=None):
        self.paginated = paginated or []
        self.single = single or {}
        self.calls = []

    def get_paginated(self, endpoint, params=None):
        self.calls.append((endpoint, params))
        for item in self.paginated:
            yield item

    def get(self, endpoint, params=None):
        self.calls.append((endpoint, params))
        return self.single


class FetchUserPortfoliosTest(TestCase):
    def test_requires_workspace_and_owner_params(self) -> None:
        client = DummyClient(paginated=[{"gid": "pf_1"}])

        result = list(fetch_user_portfolios(client, "ws_1", "user_1"))

        self.assertEqual(result[0]["gid"], "pf_1")
        endpoint, params = client.calls[0]
        self.assertEqual(endpoint, "/portfolios")
        self.assertEqual(params["workspace"], "ws_1")
        self.assertEqual(params["owner"], "user_1")
        self.assertIn("opt_fields", params)
        self.assertEqual(params["opt_fields"], ",".join(PORTFOLIO_FIELDS_DEFAULT))


class FetchPortfolioTest(TestCase):
    def test_unwraps_data_envelope(self) -> None:
        client = DummyClient(single={"data": {"gid": "pf_9", "name": "Nested"}})

        portfolio = fetch_portfolio(client, "pf_9")

        self.assertEqual(portfolio, {"gid": "pf_9", "name": "Nested"})
        endpoint, _ = client.calls[0]
        self.assertEqual(endpoint, "/portfolios/pf_9")

    def test_missing_data_returns_empty_dict(self) -> None:
        client = DummyClient(single={})
        self.assertEqual(fetch_portfolio(client, "pf_9"), {})


class FetchPortfolioItemsTest(TestCase):
    def test_returns_projects_and_nested_portfolios(self) -> None:
        client = DummyClient(
            paginated=[
                {"gid": "proj_1", "resource_type": "project"},
                {"gid": "pf_2", "resource_type": "portfolio"},
            ]
        )

        items = fetch_portfolio_items(client, "pf_1")

        self.assertEqual([i["resource_type"] for i in items], ["project", "portfolio"])
        endpoint, params = client.calls[0]
        self.assertEqual(endpoint, "/portfolios/pf_1/items")
        self.assertIn("resource_type", params["opt_fields"])


class FetchPortfolioCustomFieldSettingsTest(TestCase):
    def test_uses_settings_endpoint(self) -> None:
        client = DummyClient(paginated=[{"custom_field": {"gid": "cf_1"}}])

        settings = fetch_portfolio_custom_field_settings(client, "pf_1")

        self.assertEqual(settings[0]["custom_field"]["gid"], "cf_1")
        endpoint, _ = client.calls[0]
        self.assertEqual(endpoint, "/portfolios/pf_1/custom_field_settings")


class FetchPortfolioMembershipsTest(TestCase):
    def test_filters_by_portfolio_param(self) -> None:
        client = DummyClient(paginated=[{"user": {"gid": "u_1"}, "access_level": "admin"}])

        memberships = fetch_portfolio_memberships(client, "pf_1")

        self.assertEqual(memberships[0]["user"]["gid"], "u_1")
        endpoint, params = client.calls[0]
        self.assertEqual(endpoint, "/portfolio_memberships")
        self.assertEqual(params["portfolio"], "pf_1")


class FetchPortfolioStatusUpdatesTest(TestCase):
    def test_uses_status_updates_endpoint_with_parent(self) -> None:
        client = DummyClient(paginated=[{"gid": "su_1", "status_type": "on_track"}])

        updates = fetch_portfolio_status_updates(client, "pf_1")

        self.assertEqual(updates[0]["gid"], "su_1")
        endpoint, params = client.calls[0]
        self.assertEqual(endpoint, "/status_updates")
        self.assertEqual(params["parent"], "pf_1")
