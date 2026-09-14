"""Create anonymized SQLite copies for safer troubleshooting."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


DEFAULT_SALT_BYTES = 16
DEFAULT_TEXT = "redacted"
EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
URL_RE = re.compile(r"(?i)\bhttps?://[^\s<>\")']+")
TOKEN_RE = re.compile(
    r"(?i)\b(?:bearer\s+)?(?:pat|sk|xox[pbar]|ATATT|ATCTT)[A-Za-z0-9._=-]{12,}\b"
)


IDENTIFIER_COLUMNS = {
    "asana_gid",
    "archive_id",
    "attachment_gid",
    "custom_field_gid",
    "enum_option_gid",
    "export_id",
    "flattened_parent_gid",
    "gid",
    "global_id",
    "item_gid",
    "jira_remote_link_id",
    "manifest_sha256",
    "parent_gid",
    "project_gid",
    "related_task_gid",
    "sha256",
    "task_gid",
    "user_gid",
    "user_task_list_gid",
    "workspace_gid",
}

KEY_COLUMNS = {
    "jira_site_key",
}

TEXT_COLUMNS = {
    "current_status_title",
    "custom_label",
    "decision",
    "description",
    "display_value",
    "error",
    "html_notes",
    "html_text",
    "notes",
    "scope_json",
    "text",
    "text_value",
    "title",
}

NAME_COLUMNS = {
    "atlassian_email",
    "created_by_name",
    "custom_field_name",
    "enum_value_name",
    "item_name",
    "jira_name",
    "jira_project_name",
    "name",
    "original_name",
    "owner_name",
    "project_name",
    "section_name",
    "tag_name",
    "team_name",
    "user_name",
    "workspace_name",
}

URL_COLUMNS = {
    "archive_path",
    "permalink_url",
    "permanent_url",
    "relative_path",
    "url",
    "view_url",
}
JIRA_KEY_COLUMNS = {"jira_key", "jira_project_key"}


@dataclass(frozen=True)
class TableColumn:
    table: str
    column: str


@dataclass(frozen=True)
class AnonymizationPolicy:
    anonymized: bool
    method: str
    replacement_shape: str
    notes: str


@dataclass(frozen=True)
class AnonymizationReport:
    source_path: Path
    output_path: Path
    salt_fingerprint: str
    updated_values: int
    updated_columns: Tuple[TableColumn, ...]
    remaining_findings: Tuple[str, ...]


class ValueAnonymizer:
    """Deterministic anonymization helper scoped by a private salt."""

    def __init__(self, salt: str):
        self._salt = salt
        self._email_cache: Dict[str, str] = {}

    def digest(self, value: object, length: int = 12) -> str:
        raw = "" if value is None else str(value)
        digest = hashlib.sha256(f"{self._salt}\0{raw}".encode("utf-8")).hexdigest()
        return digest[:length]

    def fingerprint(self) -> str:
        return hashlib.sha256(self._salt.encode("utf-8")).hexdigest()[:12]

    def identifier(self, value: object, prefix: str = "id") -> str:
        return f"{prefix}_{self.digest(value)}"

    def email(self, value: object) -> str:
        raw = str(value)
        if raw not in self._email_cache:
            self._email_cache[raw] = f"user_{self.digest(raw, 10)}@example.invalid"
        return self._email_cache[raw]

    def jira_key(self, value: object) -> str:
        numeric = int(self.digest(value, 8), 16) % 900000 + 100000
        return f"JIRA-{numeric}"

    def name(self, value: object, column: str) -> str:
        label = column.replace("_", " ").replace("jira", "Jira").title()
        return f"{label} {self.digest(value, 8)}"

    def text(self, value: object) -> str:
        raw = str(value)
        if not raw:
            return raw
        return f"{DEFAULT_TEXT} {self.digest(raw, 10)}"

    def json_text(self, value: object) -> str:
        raw = str(value)
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return self.text(raw)

        def scrub(node: object) -> object:
            if isinstance(node, dict):
                return {str(key): scrub(item) for key, item in node.items()}
            if isinstance(node, list):
                return [scrub(item) for item in node]
            if isinstance(node, str):
                if EMAIL_RE.fullmatch(node):
                    return self.email(node)
                if URL_RE.fullmatch(node):
                    return self.url(node)
                return self.text(node)
            return node

        return json.dumps(scrub(parsed), sort_keys=True, separators=(",", ":"))

    def url(self, value: object) -> str:
        return f"https://example.invalid/{self.digest(value, 16)}"

    def path(self, value: object) -> str:
        return f"redacted/{self.digest(value, 16)}"


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _table_names(connection: sqlite3.Connection) -> List[str]:
    rows = connection.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
          AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    ).fetchall()
    return [row["name"] if isinstance(row, sqlite3.Row) else row[0] for row in rows]


def _columns(connection: sqlite3.Connection, table: str) -> List[str]:
    return [
        row["name"]
        for row in connection.execute(f"PRAGMA table_info({_quote_identifier(table)})")
    ]


def _is_text_column(connection: sqlite3.Connection, table: str, column: str) -> bool:
    for row in connection.execute(f"PRAGMA table_info({_quote_identifier(table)})"):
        if row["name"] == column:
            return "TEXT" in (row["type"] or "").upper()
    return False


def _column_transform(column: str, anonymizer: ValueAnonymizer) -> Optional[Callable[[object], str]]:
    policy = column_anonymization_policy(column)
    lower = column.lower()
    if not policy.anonymized:
        return None
    if lower in JIRA_KEY_COLUMNS:
        return anonymizer.jira_key
    if lower == "email" or lower.endswith("_email"):
        return anonymizer.email
    if lower in URL_COLUMNS or lower.endswith("_url"):
        if lower.endswith("_path"):
            return anonymizer.path
        return anonymizer.url
    if lower in IDENTIFIER_COLUMNS or lower.endswith("_gid"):
        return lambda value: anonymizer.identifier(value, "gid")
    if lower == "teamid":
        return lambda value: anonymizer.identifier(value, "id")
    if lower in KEY_COLUMNS:
        return lambda value: anonymizer.identifier(value, "key")
    if lower.endswith("_id"):
        return lambda value: anonymizer.identifier(value, "id")
    if lower in NAME_COLUMNS or lower.endswith("_name"):
        return lambda value: anonymizer.name(value, lower)
    if lower.endswith("_json"):
        return anonymizer.json_text
    if lower in TEXT_COLUMNS:
        return anonymizer.text
    return None


def column_anonymization_policy(column: str) -> AnonymizationPolicy:
    """Return the anonymization policy applied to a SQLite column name."""

    lower = column.lower()
    if lower in JIRA_KEY_COLUMNS:
        return AnonymizationPolicy(True, "Jira key", "`JIRA-<number>`", "Deterministic fake Jira key.")
    if lower == "email" or lower.endswith("_email"):
        return AnonymizationPolicy(
            True,
            "email",
            "`user_<hash>@example.invalid`",
            "Deterministic fake email address.",
        )
    if lower in URL_COLUMNS or lower.endswith("_url"):
        if lower.endswith("_path"):
            return AnonymizationPolicy(
                True,
                "path",
                "`redacted/<hash>`",
                "Deterministic fake local/archive path.",
            )
        return AnonymizationPolicy(
            True,
            "URL",
            "`https://example.invalid/<hash>`",
            "Deterministic fake URL.",
        )
    if lower in IDENTIFIER_COLUMNS or lower.endswith("_gid"):
        return AnonymizationPolicy(
            True,
            "identifier",
            "`gid_<hash>`",
            "Deterministic fake identifier; join relationships remain consistent.",
        )
    if lower == "teamid":
        return AnonymizationPolicy(
            True,
            "identifier",
            "`id_<hash>`",
            "Deterministic fake identifier; join relationships remain consistent.",
        )
    if lower in KEY_COLUMNS:
        return AnonymizationPolicy(
            True,
            "key",
            "`key_<hash>`",
            "Deterministic fake key.",
        )
    if lower.endswith("_id"):
        return AnonymizationPolicy(
            True,
            "identifier",
            "`id_<hash>`",
            "Deterministic fake identifier; join relationships remain consistent.",
        )
    if lower in NAME_COLUMNS or lower.endswith("_name"):
        return AnonymizationPolicy(
            True,
            "name",
            "`<Column Label> <hash>`",
            "Deterministic fake display/name value.",
        )
    if lower.endswith("_json"):
        return AnonymizationPolicy(
            True,
            "JSON text",
            "valid JSON with string values redacted",
            "Preserves JSON structure while scrubbing string values.",
        )
    if lower in TEXT_COLUMNS:
        return AnonymizationPolicy(
            True,
            "text",
            "`redacted <hash>`",
            "Deterministic free-text redaction.",
        )
    if (
        lower.endswith("_at")
        or lower.endswith("_on")
        or lower.endswith("_date")
        or lower in {"date_value", "last_updated_issue", "oldest_created_issue", "synced_at"}
    ):
        return AnonymizationPolicy(False, "none", "preserved", "Timestamp/date value preserved.")
    if (
        lower.startswith("level_")
        or lower.startswith("num_")
        or "_num_" in lower
        or lower.endswith("_bytes")
        or lower.endswith("_count")
        or lower.endswith("_depth")
        or lower.endswith("_minutes")
        or lower.endswith("_size")
        or lower in {"number_value", "position", "precision", "size"}
    ):
        return AnonymizationPolicy(False, "none", "preserved", "Numeric/count value preserved.")
    if (
        lower.startswith("is_")
        or lower.endswith("_is_active")
        or lower.endswith("_is_public")
        or lower in {"archived", "completed", "enabled", "liked", "public"}
    ):
        return AnonymizationPolicy(False, "none", "preserved", "Boolean/flag value preserved.")
    if lower in {
        "access_level",
        "assignee_status",
        "atlassian_status",
        "color",
        "currency_code",
        "current_status_type",
        "custom_field_type",
        "custom_label_position",
        "format",
        "host",
        "kind",
        "parent_resource_type",
        "position",
        "precision",
        "privacy_setting",
        "recurrence",
        "relation_type",
        "resource_subtype",
        "resource_type",
        "role",
        "service",
        "status",
        "status_type",
        "type",
    }:
        return AnonymizationPolicy(False, "none", "preserved", "Category/status value preserved.")
    return AnonymizationPolicy(False, "none", "preserved", "No anonymization rule currently applies.")


def _replacement_map(
    connection: sqlite3.Connection,
    table: str,
    column: str,
    transform: Callable[[object], str],
) -> Dict[object, str]:
    quoted_table = _quote_identifier(table)
    quoted_column = _quote_identifier(column)
    rows = connection.execute(
        f"""
        SELECT DISTINCT {quoted_column} AS value
        FROM {quoted_table}
        WHERE {quoted_column} IS NOT NULL
          AND {quoted_column} != ''
        """
    ).fetchall()
    return {row["value"]: transform(row["value"]) for row in rows}


def _apply_replacements(
    connection: sqlite3.Connection,
    table: str,
    column: str,
    replacements: Dict[object, str],
) -> int:
    if not replacements:
        return 0
    quoted_table = _quote_identifier(table)
    quoted_column = _quote_identifier(column)
    updated = 0
    for original, replacement in replacements.items():
        connection.execute(
            f"UPDATE {quoted_table} SET {quoted_column} = ? WHERE {quoted_column} = ?",
            (replacement, original),
        )
        updated += 1
    return updated


def _scan_remaining_sensitive_values(connection: sqlite3.Connection) -> List[str]:
    findings: List[str] = []
    for table in _table_names(connection):
        for column in _columns(connection, table):
            if not _is_text_column(connection, table, column):
                continue
            quoted_table = _quote_identifier(table)
            quoted_column = _quote_identifier(column)
            rows = connection.execute(
                f"""
                SELECT {quoted_column} AS value
                FROM {quoted_table}
                WHERE {quoted_column} IS NOT NULL
                """
            ).fetchall()
            for row in rows:
                value = str(row["value"])
                emails = [match for match in EMAIL_RE.findall(value) if not match.endswith("@example.invalid")]
                urls = [
                    match
                    for match in URL_RE.findall(value)
                    if not match.lower().startswith("https://example.invalid/")
                ]
                if emails:
                    findings.append(f"{table}.{column}: email-like value remains")
                    break
                if urls:
                    findings.append(f"{table}.{column}: URL-like value remains")
                    break
                if TOKEN_RE.search(value):
                    findings.append(f"{table}.{column}: token-like value remains")
                    break
    return findings


def anonymize_database(
    source_path: Path,
    output_path: Path,
    *,
    overwrite: bool = False,
    salt: Optional[str] = None,
    verify: bool = True,
) -> AnonymizationReport:
    """Copy ``source_path`` to ``output_path`` and anonymize sensitive values."""

    source = source_path.expanduser()
    output = output_path.expanduser()
    if not source.exists():
        raise FileNotFoundError(f"SQLite database not found: {source}")
    if source.resolve() == output.resolve():
        raise ValueError(
            "Output path must differ from source path; anonymization never runs in place."
        )
    if output.exists() and not overwrite:
        raise FileExistsError(f"Output database already exists: {output}")

    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, output)

    active_salt = salt or secrets.token_hex(DEFAULT_SALT_BYTES)
    anonymizer = ValueAnonymizer(active_salt)
    updated_columns: List[TableColumn] = []
    updated_values = 0

    with sqlite3.connect(output) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("BEGIN")
        try:
            for table in _table_names(connection):
                for column in _columns(connection, table):
                    transform = _column_transform(column, anonymizer)
                    if transform is None:
                        continue
                    replacements = _replacement_map(connection, table, column, transform)
                    changed = _apply_replacements(connection, table, column, replacements)
                    if changed:
                        updated_values += changed
                        updated_columns.append(TableColumn(table, column))
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        remaining_findings = tuple(_scan_remaining_sensitive_values(connection) if verify else [])
        connection.execute("VACUUM")

    logger.info(
        "Anonymized %s distinct value(s) across %s column(s) into %s.",
        updated_values,
        len(updated_columns),
        output,
    )
    return AnonymizationReport(
        source_path=source,
        output_path=output,
        salt_fingerprint=anonymizer.fingerprint(),
        updated_values=updated_values,
        updated_columns=tuple(updated_columns),
        remaining_findings=remaining_findings,
    )


def default_output_path(source_path: Path) -> Path:
    """Return the conventional anonymized output path for a source database."""

    source = source_path.expanduser()
    stem = source.stem
    return source.parent / f"{stem}_anonymized{source.suffix or '.sqlite'}"
