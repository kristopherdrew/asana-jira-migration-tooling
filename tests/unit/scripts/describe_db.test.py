from __future__ import annotations

from pathlib import Path
from unittest import TestCase

from scripts.describe_db import (
    _canonical_schema_connection,
    column_info,
    render_anonymization_markdown,
)
from src.lib.database_anonymizer import column_anonymization_policy
from src.lib.table_metadata import TABLE_METADATA


class DescribeDbAnonymizationMarkdownTest(TestCase):
    def test_anonymization_markdown_includes_representative_columns(self) -> None:
        conn = _canonical_schema_connection()
        self.addCleanup(conn.close)

        markdown = render_anonymization_markdown(conn)

        self.assertIn("# Database Anonymization Map", markdown)
        self.assertIn("| `email` | TEXT |  | yes | email | `user_<hash>@example.invalid` |", markdown)
        self.assertIn("| `synced_at` | TEXT |  | no | none | preserved |", markdown)
        self.assertIn("#### `view_team_memberships` (view)", markdown)

    def test_anonymization_markdown_doc_is_current(self) -> None:
        conn = _canonical_schema_connection()
        self.addCleanup(conn.close)

        expected = render_anonymization_markdown(conn)
        actual = Path("docs/anonymization-map.md").read_text(encoding="utf-8")

        self.assertEqual(actual, expected)

    def test_every_schema_column_has_an_explicit_anonymization_policy(self) -> None:
        conn = _canonical_schema_connection()
        self.addCleanup(conn.close)

        fallback_columns = []
        for meta in TABLE_METADATA.values():
            for column, _ctype, _is_pk in column_info(conn, meta.name):
                policy = column_anonymization_policy(column)
                if policy.notes == "No anonymization rule currently applies.":
                    fallback_columns.append(f"{meta.name}.{column}")

        self.assertEqual(fallback_columns, [])
