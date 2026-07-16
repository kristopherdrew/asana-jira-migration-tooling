from __future__ import annotations

from unittest import TestCase

from src.lib.atlassian_status import is_active_atlassian_status


class IsActiveAtlassianStatusTest(TestCase):
    def test_active_variants_are_true(self) -> None:
        for value in ("active", "Active", "  ACTIVE  ", "aCtIvE"):
            self.assertTrue(is_active_atlassian_status(value), value)

    def test_inactive_blank_and_none_are_false(self) -> None:
        for value in (None, "", "   ", "deactivated", "inactive", "disabled", "unknown"):
            self.assertFalse(is_active_atlassian_status(value), repr(value))
