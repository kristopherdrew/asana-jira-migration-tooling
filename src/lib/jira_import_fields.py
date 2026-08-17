"""Definitions and idempotent setup logic for standard Asana Jira-import fields."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

from src.lib.jira_client import JiraClient


TEXT_FIELD_TYPE = "com.atlassian.jira.plugin.system.customfieldtypes:textfield"
URL_FIELD_TYPE = "com.atlassian.jira.plugin.system.customfieldtypes:url"
TEXT_SEARCHER = "com.atlassian.jira.plugin.system.customfieldtypes:textsearcher"
EXACT_TEXT_SEARCHER = "com.atlassian.jira.plugin.system.customfieldtypes:exacttextsearcher"
NUMBER_FIELD_TYPE = "com.atlassian.jira.plugin.system.customfieldtypes:float"
NUMBER_SEARCHER = "com.atlassian.jira.plugin.system.customfieldtypes:exactnumber"
DATE_FIELD_TYPE = "com.atlassian.jira.plugin.system.customfieldtypes:datepicker"
DATE_SEARCHER = "com.atlassian.jira.plugin.system.customfieldtypes:daterange"
SELECT_FIELD_TYPE = "com.atlassian.jira.plugin.system.customfieldtypes:select"
SELECT_SEARCHER = "com.atlassian.jira.plugin.system.customfieldtypes:multiselectsearcher"


@dataclass(frozen=True)
class JiraImportField:
    name: str
    field_type: str
    searcher_key: str
    description: str
    options: Tuple[str, ...] = ()

    def payload(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "description": self.description,
            "type": self.field_type,
            "searcherKey": self.searcher_key,
        }


STANDARD_IMPORT_FIELDS: Sequence[JiraImportField] = (
    JiraImportField("Asana Section", TEXT_FIELD_TYPE, TEXT_SEARCHER, "Asana section name used during Jira imports."),
    JiraImportField("Asana Project Link", URL_FIELD_TYPE, EXACT_TEXT_SEARCHER, "Link to the source Asana project."),
    JiraImportField("Asana Task Link", URL_FIELD_TYPE, EXACT_TEXT_SEARCHER, "Link to the source Asana task."),
)

TRACKING_FIELDS: Sequence[JiraImportField] = (
    JiraImportField("Asana Project Link", URL_FIELD_TYPE, EXACT_TEXT_SEARCHER, "Link to the source Asana project."),
    JiraImportField("Asana Team Link", URL_FIELD_TYPE, EXACT_TEXT_SEARCHER, "Link to the source Asana team."),
    JiraImportField("Asana Task Count", NUMBER_FIELD_TYPE, NUMBER_SEARCHER, "Number of Asana tasks in the source team or project."),
    JiraImportField("Asana Last Working Day", DATE_FIELD_TYPE, DATE_SEARCHER, "Most recent task activity date for the Asana project."),
    JiraImportField("Asana Current Project Status", SELECT_FIELD_TYPE, SELECT_SEARCHER, "Current lifecycle status of the Asana project.", ("Active", "Archived")),
    JiraImportField(
        "Asana Migration Decision",
        SELECT_FIELD_TYPE,
        SELECT_SEARCHER,
        "Migration decision recorded for the Asana project.",
        (
            "Migrate - Active",
            "Migrate - Production Read Only",
            "Migrate - Archive",
            "Migrate - Abandon",
        ),
    ),
)

FIELD_SETS: Dict[str, Sequence[JiraImportField]] = {
    "import": STANDARD_IMPORT_FIELDS,
    "tracking": TRACKING_FIELDS,
}


def _field_type(field: Dict[str, object]) -> str:
    schema = field.get("schema")
    return str(schema.get("custom", "")) if isinstance(schema, dict) else ""


def ensure_standard_import_fields(
    client: JiraClient,
    *,
    dry_run: bool = False,
    definitions: Sequence[JiraImportField] = STANDARD_IMPORT_FIELDS,
) -> List[Dict[str, str]]:
    """Create missing standard fields and report existing/created/planned results.

    Exact, case-insensitive name matches make re-runs safe. Existing duplicates
    of the expected type are retained and reported, while a name found only with
    another Jira type is rejected.
    """
    catalog = client.get_custom_fields()
    matches_by_name: Dict[str, List[Dict[str, object]]] = {}
    for definition in definitions:
        matches = [
            field for field in catalog
            if str(field.get("name", "")).strip().casefold() == definition.name.casefold()
        ]
        if matches:
            correctly_typed = [
                field for field in matches if _field_type(field) == definition.field_type
            ]
            if not correctly_typed:
                existing_types = sorted(
                    {_field_type(field) or "unknown" for field in matches}
                )
                raise ValueError(
                    f"Jira field {definition.name!r} already exists as type "
                    f"{', '.join(existing_types)}, expected {definition.field_type}."
                )
            matches = correctly_typed
        matches_by_name[definition.name] = matches

    results: List[Dict[str, str]] = []
    for definition in definitions:
        matches = matches_by_name[definition.name]
        if matches:
            existing = matches[0]
            result = {
                "name": definition.name,
                "status": "existing",
                "id": str(existing.get("id", "")),
            }
            if len(matches) > 1:
                result["duplicates"] = str(len(matches))
            results.append(result)
            continue
        if dry_run:
            results.append({"name": definition.name, "status": "planned", "id": ""})
            continue
        created = client.create_custom_field(definition.payload())
        results.append({"name": definition.name, "status": "created", "id": str(created.get("id", ""))})

    definitions_by_name = {definition.name: definition for definition in definitions}
    for result in results:
        definition = definitions_by_name[result["name"]]
        if not definition.options or dry_run:
            continue
        field_id = result["id"]
        contexts = client.get_custom_field_contexts(field_id)
        valid_contexts = [context for context in contexts if context.get("id")]
        if not valid_contexts:
            raise ValueError(
                f"Jira field {definition.name!r} has no context in which to configure options."
            )
        added: List[str] = []
        for context in valid_contexts:
            context_id = str(context["id"])
            existing_options = {
                str(option.get("value", "")).strip().casefold()
                for option in client.get_custom_field_options(field_id, context_id)
            }
            missing = [option for option in definition.options if option.casefold() not in existing_options]
            if missing:
                client.create_custom_field_options(field_id, context_id, missing)
                added.extend(missing)
        if added:
            result["options"] = ", ".join(dict.fromkeys(added))
    return results
