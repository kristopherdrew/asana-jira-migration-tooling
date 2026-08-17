from unittest import TestCase

from src.lib.jira_import_fields import (
    STANDARD_IMPORT_FIELDS,
    TRACKING_FIELDS,
    TEXT_FIELD_TYPE,
    URL_FIELD_TYPE,
    ensure_standard_import_fields,
)


class FakeClient:
    def __init__(self, fields=None):
        self.fields = list(fields or [])
        self.created = []
        self.created_options = []

    def get_custom_fields(self):
        return self.fields

    def create_custom_field(self, payload):
        self.created.append(payload)
        return {"id": f"customfield_{len(self.created)}", "name": payload["name"]}

    def get_custom_field_contexts(self, field_id):
        return [{"id": "context_1"}]

    def get_custom_field_options(self, field_id, context_id):
        return [{"value": "Active"}]

    def create_custom_field_options(self, field_id, context_id, options):
        self.created_options.append((field_id, context_id, options))
        return {}


class EnsureStandardImportFieldsTest(TestCase):
    def test_definitions_have_requested_names_and_types(self) -> None:
        self.assertEqual(
            [(field.name, field.field_type) for field in STANDARD_IMPORT_FIELDS],
            [
                ("Asana Section", TEXT_FIELD_TYPE),
                ("Asana Project Link", URL_FIELD_TYPE),
                ("Asana Task Link", URL_FIELD_TYPE),
            ],
        )

    def test_tracking_definitions_and_select_options(self) -> None:
        self.assertEqual(
            [field.name for field in TRACKING_FIELDS],
            [
                "Asana Project Link",
                "Asana Team Link",
                "Asana Task Count",
                "Asana Last Working Day",
                "Asana Current Project Status",
                "Asana Migration Decision",
            ],
        )
        client = FakeClient()
        results = ensure_standard_import_fields(client, definitions=TRACKING_FIELDS)
        self.assertEqual(len(results), 6)
        self.assertEqual(
            client.created_options,
            [
                ("customfield_5", "context_1", ["Archived"]),
                (
                    "customfield_6",
                    "context_1",
                    [
                        "Migrate - Active",
                        "Migrate - Production Read Only",
                        "Migrate - Archive",
                        "Migrate - Abandon",
                    ],
                ),
            ],
        )

    def test_creates_all_missing_fields(self) -> None:
        client = FakeClient()
        results = ensure_standard_import_fields(client)
        self.assertEqual([row["status"] for row in results], ["created"] * 3)
        self.assertEqual([payload["name"] for payload in client.created], [field.name for field in STANDARD_IMPORT_FIELDS])

    def test_dry_run_does_not_create_missing_fields(self) -> None:
        client = FakeClient()
        results = ensure_standard_import_fields(client, dry_run=True)
        self.assertEqual([row["status"] for row in results], ["planned"] * 3)
        self.assertEqual(client.created, [])

    def test_matching_existing_field_is_retained_case_insensitively(self) -> None:
        client = FakeClient([{
            "id": "customfield_9",
            "name": "asana section",
            "schema": {"custom": TEXT_FIELD_TYPE},
        }])
        results = ensure_standard_import_fields(client, definitions=STANDARD_IMPORT_FIELDS[:1])
        self.assertEqual(results, [{"name": "Asana Section", "status": "existing", "id": "customfield_9"}])
        self.assertEqual(client.created, [])

    def test_wrong_type_is_rejected(self) -> None:
        wrong_type = FakeClient([{"name": "Asana Section", "schema": {"custom": URL_FIELD_TYPE}}])
        with self.assertRaisesRegex(ValueError, "expected"):
            ensure_standard_import_fields(wrong_type, definitions=STANDARD_IMPORT_FIELDS[:1])

    def test_duplicate_existing_fields_are_skipped_without_creating_another(self) -> None:
        duplicates = FakeClient([
            {"id": "customfield_1", "name": "Asana Section", "schema": {"custom": TEXT_FIELD_TYPE}},
            {"id": "customfield_2", "name": "asana section", "schema": {"custom": TEXT_FIELD_TYPE}},
        ])
        results = ensure_standard_import_fields(
            duplicates, definitions=STANDARD_IMPORT_FIELDS[:1]
        )
        self.assertEqual(results[0]["status"], "existing")
        self.assertEqual(results[0]["duplicates"], "2")
        self.assertEqual(duplicates.created, [])

    def test_all_conflicts_are_checked_before_any_creation(self) -> None:
        client = FakeClient([{
            "name": "Asana Project Link",
            "schema": {"custom": TEXT_FIELD_TYPE},
        }])
        with self.assertRaisesRegex(ValueError, "Asana Project Link"):
            ensure_standard_import_fields(client, definitions=STANDARD_IMPORT_FIELDS[:2])
        self.assertEqual(client.created, [])
