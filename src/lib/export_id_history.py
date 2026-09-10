"""Helpers for reviewing and clearing persisted export-ID history."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import List, Optional

from src.lib.database import Database


@dataclass(frozen=True)
class ExportIdSummary:
    export_id: str
    task_count: int
    project_count: int
    first_used: str
    last_used: str


@dataclass(frozen=True)
class ExportIdProjectSummary:
    project_gid: str
    project_name: str
    task_count: int
    first_used: str
    last_used: str


@dataclass(frozen=True)
class ExportIdDetail:
    summary: ExportIdSummary
    projects: List[ExportIdProjectSummary]


def list_export_ids(database: Database) -> List[ExportIdSummary]:
    """Return export-ID summaries ordered by newest activity first."""
    with database.connect() as connection:
        database.initialize(connection)
        return list_export_ids_for_connection(connection)


def list_export_ids_for_connection(connection: sqlite3.Connection) -> List[ExportIdSummary]:
    rows = connection.execute(
        """
        SELECT
            export_id,
            COUNT(*) AS task_count,
            COUNT(DISTINCT NULLIF(project_gid, '')) AS project_count,
            MIN(exported_at) AS first_used,
            MAX(exported_at) AS last_used
        FROM exported_tasks
        WHERE TRIM(COALESCE(export_id, '')) != ''
        GROUP BY export_id
        ORDER BY last_used DESC, export_id COLLATE NOCASE
        """
    ).fetchall()
    return [
        ExportIdSummary(
            export_id=row["export_id"],
            task_count=row["task_count"],
            project_count=row["project_count"],
            first_used=row["first_used"] or "",
            last_used=row["last_used"] or "",
        )
        for row in rows
    ]


def get_export_id_detail(database: Database, export_id: str) -> Optional[ExportIdDetail]:
    """Return one export ID with per-project task counts, or None if absent."""
    with database.connect() as connection:
        database.initialize(connection)
        return get_export_id_detail_for_connection(connection, export_id)


def get_export_id_detail_for_connection(
    connection: sqlite3.Connection,
    export_id: str,
) -> Optional[ExportIdDetail]:
    row = connection.execute(
        """
        SELECT
            export_id,
            COUNT(*) AS task_count,
            COUNT(DISTINCT NULLIF(project_gid, '')) AS project_count,
            MIN(exported_at) AS first_used,
            MAX(exported_at) AS last_used
        FROM exported_tasks
        WHERE export_id = ?
        GROUP BY export_id
        """,
        (export_id,),
    ).fetchone()
    if row is None:
        return None

    project_rows = connection.execute(
        """
        SELECT
            COALESCE(NULLIF(et.project_gid, ''), '(unknown)') AS project_gid,
            COALESCE(NULLIF(p.name, ''), '') AS project_name,
            COUNT(*) AS task_count,
            MIN(et.exported_at) AS first_used,
            MAX(et.exported_at) AS last_used
        FROM exported_tasks et
        LEFT JOIN projects p ON p.gid = et.project_gid
        WHERE et.export_id = ?
        GROUP BY COALESCE(NULLIF(et.project_gid, ''), '(unknown)'), COALESCE(NULLIF(p.name, ''), '')
        ORDER BY task_count DESC, project_name COLLATE NOCASE, project_gid COLLATE NOCASE
        """,
        (export_id,),
    ).fetchall()

    summary = ExportIdSummary(
        export_id=row["export_id"],
        task_count=row["task_count"],
        project_count=row["project_count"],
        first_used=row["first_used"] or "",
        last_used=row["last_used"] or "",
    )
    projects = [
        ExportIdProjectSummary(
            project_gid=project_row["project_gid"],
            project_name=project_row["project_name"],
            task_count=project_row["task_count"],
            first_used=project_row["first_used"] or "",
            last_used=project_row["last_used"] or "",
        )
        for project_row in project_rows
    ]
    return ExportIdDetail(summary=summary, projects=projects)


def clear_export_id(database: Database, export_id: str) -> int:
    """Delete all task-history rows for one export ID."""
    with database.connect() as connection:
        database.initialize(connection)
        return database.clear_export_history_for_id(connection, export_id)


def clear_all_export_ids(database: Database) -> int:
    """Delete all persisted export-ID task history."""
    with database.connect() as connection:
        database.initialize(connection)
        return database.clear_export_history(connection)

