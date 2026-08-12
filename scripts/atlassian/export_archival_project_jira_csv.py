#!/usr/bin/env python3
"""Export an archival project's tasks into a simplified Jira-ready CSV.

Unlike ``export_project_to_jira_csv`` (one column per Asana custom field), this
variant is meant for projects marked for archival: every populated non-native
(custom) field is consolidated into the task's Description (the ``notes``
column) as one ``Field Name: value`` line per field, under an
``--- Asana Fields ---`` heading. Genuinely native task attributes (Section,
original parent, completion status, dates, assignee/creator, tags, comments)
are retained as their own columns exactly as the full exporter emits them.

Project selection is explicit (``--project-gid`` / ``--project-gids`` /
``--project-gids-file`` / ``--team-gid``); the script does not derive the
archival set from the migration-decision field.
"""

from __future__ import annotations

import argparse
import csv
import logging
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Set

from scripts.atlassian.export_project_to_jira_csv import (
    DEFAULT_OUTPUT_DIR,
    JIRA_TASK_ID_FIELD_NAME,
    _build_active_creator_fallback,
    _is_explicitly_inactive_atlassian_user,
    build_project_export_data,
    current_utc_time,
    ensure_tasks_suffix,
    extract_profile_gid_overrides,
    format_export_datetime,
    format_jira_hierarchy_depth,
    format_project_entry,
    format_user_display_name,
    load_project_names,
    load_user_display_names,
    load_user_emails,
    normalize_jira_base_url,
    organize_outputs,
    parse_iso_datetime,
    parse_project_list,
    prefer_html_text_with_links,
    record_user_association,
    resolve_atlassian_identity,
    resolve_custom_field_value,
    sanitize_filename_component,
    transform_asana_links,
    write_link_modifications_report,
    write_user_association_report,
)
from src.lib.config import load_credentials, resolve_jira_base_url
from src.lib.database import Database, resolve_default_db_path
from src.lib.logging_utils import configure_logging, resolve_log_level
from src.lib.jira_csv_partition import (
    CsvPart,
    pack_csv_parts,
    partition_project_csv,
    write_partition_manifest,
)
from scripts.merge_project_exports import merge_csv_files

logger = logging.getLogger(__name__)

ARCHIVAL_FIELDS_HEADING = "--- Asana Fields ---"


class NoTasksFoundError(SystemExit):
    """Raised when an explicitly selected project has no exportable tasks."""


def build_archival_description(
    notes_value: str,
    field_meta: List[Dict[str, object]],
    task_gid: str,
    single_map: Dict,
    multi_map: Dict,
    option_map: Dict,
) -> str:
    """Fold every populated custom field into the description, one line per field.

    Fields with no value for the task are omitted. Multi-enum values are joined
    with ``, ``. The original notes stay on top, followed by a labeled block.
    """

    lines: List[str] = []
    for meta in field_meta:
        value = resolve_custom_field_value(meta, task_gid, single_map, multi_map, option_map)
        if value and value.strip():
            lines.append(f"{meta['name']}: {value.strip()}")
    if not lines:
        return notes_value or ""
    block = ARCHIVAL_FIELDS_HEADING + "\n" + "\n".join(lines)
    if notes_value and notes_value.strip():
        return f"{notes_value}\n\n{block}"
    return block


def _needs_flattened_refresh(tasks: Iterable) -> bool:
    return any(
        task["hierarchy_depth"] is not None and task["jira_hierarchy_depth"] is None
        for task in tasks
    )


def _sort_task_key(task: dict) -> tuple:
    depth = task.get("hierarchy_depth")
    depth_key = (depth is None, depth if depth is not None else 10 ** 9)
    created_raw = task.get("created_at")
    created_dt = parse_iso_datetime(created_raw) if created_raw else None
    created_key = (created_dt is None, created_dt or datetime.max.replace(tzinfo=timezone.utc))
    gid_key = task.get("gid") or ""
    return (depth_key, created_key, gid_key)


def build_default_filename(project_name: Optional[str]) -> str:
    base = sanitize_filename_component(project_name or "project")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    return f"{base}-{stamp}_archival_tasks.csv"


def export_archival_project(
    database_path: str,
    project_gid: str,
    output_dir: Path,
    jira_base_url: str,
    filename: Optional[str] = None,
    fallback_atlassian_id: Optional[str] = None,
    export_id: Optional[str] = None,
    skipped_log: Optional[List[Dict[str, str]]] = None,
) -> Optional[Path]:
    jira_base_url = normalize_jira_base_url(jira_base_url)
    if not jira_base_url:
        raise ValueError("Jira base URL is required.")

    database = Database(db_path=database_path)
    connection = database.connect()
    try:
        database.initialize(connection)
        user_map = database.load_atlassian_account_map(connection)
        user_display_names = load_user_display_names(connection)
        user_emails = load_user_emails(connection)
        project_names = load_project_names(connection)
        jira_project_map = database.load_jira_project_mappings(connection)

        def _bundle() -> Dict:
            return build_project_export_data(
                connection,
                project_gid,
                user_map,
                user_display_names,
                project_names,
                jira_base_url,
                custom_field_filters=None,
                prompt_for_custom_fields=False,
                stats_only=False,
                user_emails=user_emails,
                fallback_atlassian_id=fallback_atlassian_id,
            )

        bundle = _bundle()
        tasks = [dict(row) for row in bundle["tasks"]]
        if not tasks:
            raise NoTasksFoundError(f"No tasks found for project {project_gid}.")

        if _needs_flattened_refresh(tasks):
            logger.info(
                "Flattened hierarchy data missing for project %s; rebuilding task_hierarchy_flattened.",
                project_gid,
            )
            database.rebuild_flattened_task_hierarchy(connection)
            bundle = _bundle()
            tasks = [dict(row) for row in bundle["tasks"]]

        if export_id:
            exported_task_projects = database.get_exported_task_projects(connection, export_id)
            if exported_task_projects:
                logger.info(
                    "Found %s previously exported task(s) for export_id %s; they will be skipped.",
                    len(exported_task_projects),
                    export_id,
                )
            retained_tasks = []
            for task in tasks:
                gid = task["gid"]
                if gid in exported_task_projects:
                    if skipped_log is not None:
                        existing_project_gid = exported_task_projects.get(gid) or ""
                        skipped_log.append(
                            {
                                "task_gid": gid,
                                "skipped_project": project_names.get(project_gid, project_gid),
                                "existing_project": project_names.get(
                                    existing_project_gid, existing_project_gid
                                ),
                                "export_id": export_id,
                            }
                        )
                    continue
                retained_tasks.append(task)
            tasks = retained_tasks
            if not tasks:
                logger.info(
                    "All tasks for archival project %s are already recorded for export_id %s; "
                    "skipping export.",
                    project_gid,
                    export_id,
                )
                return None

        field_meta = bundle["field_meta"]
        single_map = bundle["single_map"]
        multi_map = bundle["multi_map"]
        option_map = bundle["option_map"]
        section_map = bundle["section_map"]
        tags_map = bundle["tags_map"]
        projects_map = bundle["projects_map"]
        attachment_names = bundle["attachment_names"]
        comments_by_task = bundle["comments_by_task"]
        link_modifications = bundle["link_modifications"]
        associations: Dict[str, Counter] = bundle["associations"]
        missing_accounts = bundle["missing_accounts"]
        last_active: Dict[str, str] = bundle["last_active"]
        max_sections = bundle["max_sections"]
        max_tags = bundle["max_tags"]
        max_comments = bundle["max_comments"]

        project_row = connection.execute(
            "SELECT name FROM projects WHERE gid = ?", (project_gid,)
        ).fetchone()
        project_name = project_row["name"] if project_row else None
        current_project_display = format_project_entry(project_name, project_gid)

        active_creator_fallback = _build_active_creator_fallback(
            tasks, user_map, user_display_names, user_emails, fallback_atlassian_id
        )
        parent_lookup = {task["gid"]: task["parent_gid"] for task in tasks if task["gid"]}

        def make_link_recorder(task_gid: str, field: str) -> Callable[[str, str], None]:
            def _record(original: str, modified: str) -> None:
                link_modifications.append(
                    {"task_gid": task_gid, "field": field, "original": original, "modified": modified}
                )

            return _record

        header = [
            "current_project",
            "jira_project_name",
            "jira_project_key",
            "jira_project_type",
            "task_gid",
            JIRA_TASK_ID_FIELD_NAME,
            "parent_gid",
            "hierarchy_depth",
            "jira_parent_gid",
            "jira_hierarchy_depth",
            "task_name",
            "assignee",
            "assignee_email",
            "assignee_atlassian_id",
            "assignee_atlassian_email",
            "creator",
            "creator_email",
            "creator_atlassian_id",
            "creator_atlassian_email",
            "projects",
            "modified_section",
        ]
        if max_sections:
            for idx in range(1, max_sections + 1):
                suffix = f" ({idx})" if idx > 1 else ""
                header.append(f"Section{suffix}")
        header.extend(
            [
                "start_on",
                "start_at",
                "created_at",
                "due_date",
                "completed",
                "completed_at",
                "actual_time_minutes",
            ]
        )
        if max_tags:
            header.extend(["Tags"] * max_tags)
        # No per-custom-field columns: they are folded into the notes column.
        header.append("notes")
        header.extend(["Comment"] * max_comments)

        tasks.sort(key=_sort_task_key)
        rows: List[List[str]] = []
        full_contents: List[Dict[str, str]] = []
        section_counts: Dict[str, int] = {}

        for task in tasks:
            due_source = task["due_on"] or task["due_at"] or ""
            due_date = format_export_datetime(due_source)
            completed_flag = ""
            if task["completed"] is not None:
                completed_flag = "Yes" if task["completed"] else "No"
            depth = task["hierarchy_depth"]
            hierarchy_value = "" if depth is None else str(depth)
            jira_parent_gid = task["jira_parent_gid"] or ""
            jira_depth_raw = task["jira_hierarchy_depth"]
            jira_depth_value = format_jira_hierarchy_depth(jira_depth_raw)

            assignee_gid = task["assignee_gid"]
            if not _is_explicitly_inactive_atlassian_user(user_map, assignee_gid) or not active_creator_fallback:
                assignee_display_name = format_user_display_name(assignee_gid, task["assignee_name"])
                assignee_email = task["assignee_email"] or ""
                assignee_atl, assignee_atl_email = resolve_atlassian_identity(
                    user_map, assignee_gid, fallback_atlassian_id
                )
                assignee_gid_effective = assignee_gid
            else:
                assignee_display_name = active_creator_fallback["display_name"]
                assignee_email = active_creator_fallback["email"]
                assignee_atl = active_creator_fallback["atlassian_id"]
                assignee_atl_email = active_creator_fallback["atlassian_email"]
                assignee_gid_effective = active_creator_fallback["user_gid"]
            record_user_association(assignee_gid_effective, "assignee", assignee_email, associations, user_emails)
            if task["created_at"]:
                dt_created = parse_iso_datetime(task["created_at"])
                if dt_created:
                    last_active[assignee_gid_effective] = max(
                        last_active.get(assignee_gid_effective, ""), dt_created.date().isoformat()
                    )

            creator_gid = task["created_by_gid"]
            creator_atl, creator_atl_email = resolve_atlassian_identity(
                user_map, creator_gid, fallback_atlassian_id
            )
            record_user_association(creator_gid, "creator", task["creator_email"], associations, user_emails)
            if task["created_at"]:
                dt_created = parse_iso_datetime(task["created_at"])
                if dt_created:
                    last_active[creator_gid] = max(
                        last_active.get(creator_gid, ""), dt_created.date().isoformat()
                    )

            completed_by_gid = task.get("completed_by_gid")
            record_user_association(completed_by_gid, "completed_by", None, associations, user_emails)
            if task.get("completed_at"):
                dt_completed = parse_iso_datetime(task["completed_at"])
                if dt_completed:
                    last_active[completed_by_gid] = max(
                        last_active.get(completed_by_gid, ""), dt_completed.date().isoformat()
                    )

            projects_value = "; ".join(projects_map.get(task["gid"], []))
            actual_time_value = "" if task["actual_time_minutes"] is None else str(task["actual_time_minutes"])
            start_on_value = format_export_datetime(task["start_on"])
            start_at_value = format_export_datetime(task["start_at"])
            created_at_value = format_export_datetime(task["created_at"])
            completed_at_value = format_export_datetime(task["completed_at"])

            profile_overrides = extract_profile_gid_overrides(task["html_notes"])
            note_recorder = make_link_recorder(task["gid"], "notes")
            raw_notes = prefer_html_text_with_links(task["notes"], task["html_notes"])
            notes_value = transform_asana_links(
                raw_notes,
                user_map,
                user_display_names,
                project_names,
                jira_base_url,
                attachment_names,
                profile_overrides,
                fallback_atlassian_id,
                note_recorder,
            )
            notes_value = build_archival_description(
                notes_value, field_meta, task["gid"], single_map, multi_map, option_map
            )
            full_contents.append({"task_gid": task["gid"], "field": "notes", "content": notes_value})

            sections = section_map.get(task["gid"], [])
            if not sections and task["parent_gid"]:
                ancestor = task["parent_gid"]
                visited: Set[str] = set()
                while ancestor and ancestor not in visited:
                    visited.add(ancestor)
                    anc_sections = section_map.get(ancestor, [])
                    if anc_sections:
                        sections = anc_sections
                        break
                    ancestor = parent_lookup.get(ancestor)
            for sec in sections:
                if sec:
                    section_counts[sec] = section_counts.get(sec, 0) + 1
            primary_section = sections[0] if sections else ""
            if task["completed"]:
                modified_section = "Done"
            elif primary_section.lower() == "done":
                modified_section = "In Progress"
            else:
                modified_section = primary_section

            jira_proj = jira_project_map.get(project_gid, {})
            row = [
                current_project_display,
                jira_proj.get("jira_project_name", ""),
                jira_proj.get("jira_project_key", ""),
                "software",
                task["gid"],
                task["gid"],
                task["parent_gid"] or "",
                hierarchy_value,
                jira_parent_gid,
                jira_depth_value,
                task["name"],
                assignee_display_name,
                assignee_email,
                assignee_atl,
                assignee_atl_email,
                format_user_display_name(task["created_by_gid"], task["creator_name"]),
                task["creator_email"] or "",
                creator_atl,
                creator_atl_email,
                projects_value,
                modified_section,
            ]
            if max_sections:
                row.extend(sections)
                if len(sections) < max_sections:
                    row.extend([""] * (max_sections - len(sections)))
            row.extend(
                [
                    start_on_value,
                    start_at_value,
                    created_at_value,
                    due_date,
                    completed_flag,
                    completed_at_value,
                    actual_time_value,
                ]
            )
            if max_tags:
                tags = tags_map.get(task["gid"], [])
                row.extend(tags)
                if len(tags) < max_tags:
                    row.extend([""] * (max_tags - len(tags)))
            row.append(notes_value)
            if max_comments:
                comments = comments_by_task.get(task["gid"], [])
                for idx, comment_text in enumerate(comments, start=1):
                    full_contents.append(
                        {"task_gid": task["gid"], "field": f"comment_{idx}", "content": comment_text}
                    )
                row.extend(comments)
                if len(comments) < max_comments:
                    row.extend([""] * (max_comments - len(comments)))
            rows.append(row)

        # Archival migrations always receive a synthetic project epic so every
        # top-level task can be imported with its Jira parent in the same CSV.
        epic_gid = f"{project_gid}::archival::epic"
        epic_row = [""] * len(header)
        epic_values = {
            "current_project": current_project_display,
            "jira_project_name": jira_project_map.get(project_gid, {}).get("jira_project_name", ""),
            "jira_project_key": jira_project_map.get(project_gid, {}).get("jira_project_key", ""),
            "jira_project_type": "software",
            "task_gid": epic_gid,
            JIRA_TASK_ID_FIELD_NAME: epic_gid,
            "hierarchy_depth": "-1",
            "jira_hierarchy_depth": "Epic",
            "task_name": project_name or project_gid,
            "projects": current_project_display,
            "modified_section": "Open",
            "notes": f"Synthetic epic for archived Asana project {project_name or project_gid}.",
        }
        for column, value in epic_values.items():
            if column in header:
                epic_row[header.index(column)] = value
        jira_parent_index = header.index("jira_parent_gid")
        jira_depth_index = header.index("jira_hierarchy_depth")
        for row in rows:
            if not row[jira_parent_index] and row[jira_depth_index] == "Task":
                row[jira_parent_index] = epic_gid
        rows.insert(0, epic_row)

        # Prune empty columns while preserving the identity/hierarchy columns Jira needs.
        if rows:
            required_columns = {
                "task_gid",
                JIRA_TASK_ID_FIELD_NAME,
                "parent_gid",
                "hierarchy_depth",
                "jira_parent_gid",
                "jira_hierarchy_depth",
                "jira_project_name",
                "jira_project_key",
                "jira_project_type",
                "notes",
            }
            columns_to_keep: List[int] = []
            for index, name in enumerate(header):
                if name in required_columns:
                    columns_to_keep.append(index)
                    continue
                has_data = any(
                    bool(row[index].strip() if isinstance(row[index], str) else row[index])
                    for row in rows
                )
                if has_data:
                    columns_to_keep.append(index)
            header = [header[idx] for idx in columns_to_keep]
            rows = [[row[idx] for idx in columns_to_keep] for row in rows]

        output_dir.mkdir(parents=True, exist_ok=True)
        export_filename = ensure_tasks_suffix(filename) if filename else build_default_filename(project_name or project_gid)
        output_path = output_dir / export_filename
        with output_path.open("w", newline="", encoding="utf-8") as fp:
            writer = csv.writer(fp)
            writer.writerow(header)
            writer.writerows(rows)

        if section_counts:
            section_path = output_path.with_name(f"{output_path.stem}_section_counts.csv")
            with section_path.open("w", newline="", encoding="utf-8") as fp:
                writer = csv.writer(fp)
                writer.writerow(["section_name", "task_count", "project"])
                for name, count in sorted(section_counts.items(), key=lambda item: item[0].lower()):
                    writer.writerow([name, count, project_name or project_gid])

        # Merge comment-author gaps into the association counts before reporting.
        for gid, identifiers in missing_accounts.items():
            counts = associations.setdefault(gid, Counter())
            if counts.get("commenter", 0) == 0:
                record_user_association(
                    gid,
                    "commenter",
                    next(iter(identifiers)) if identifiers else None,
                    associations,
                    user_emails,
                )

        write_link_modifications_report(output_path, link_modifications, full_contents)
        write_user_association_report(
            output_path,
            associations,
            user_map,
            user_display_names,
            user_emails,
            project_name or project_gid,
            last_active,
        )
        if export_id:
            recorded = database.record_exported_tasks(
                connection,
                export_id,
                (task["gid"] for task in tasks),
                project_gid,
                current_utc_time(),
            )
            logger.info(
                "Recorded %s archival task(s) for export_id %s after exporting project %s.",
                recorded,
                export_id,
                project_gid,
            )
        return output_path
    finally:
        connection.close()


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-gid", help="Single project GID to export.")
    parser.add_argument("--project-gids", help="Comma-separated list of project GIDs to export.")
    parser.add_argument(
        "--project-gids-file",
        help="Path to a file containing project GIDs (newline or comma separated).",
    )
    parser.add_argument("--team-gid", help="Export every project in this team.")
    parser.add_argument(
        "--archived-scope",
        choices=["active", "archived", "all"],
        default="all",
        help="For --team-gid, which projects to include (default: all).",
    )
    parser.add_argument("--database", default=resolve_default_db_path(), help="Path to the SQLite database.")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="Directory for the exported CSV(s).")
    parser.add_argument("--filename", help="Custom CSV filename (single-project runs).")
    parser.add_argument(
        "--fallback-atlassian-id",
        help="Atlassian account id to use when a user has no mapped Atlassian account.",
    )
    parser.add_argument(
        "--export-id",
        help=(
            "Optional identifier used to record exported tasks and skip tasks already exported "
            "with the same identifier."
        ),
    )
    parser.add_argument("--prod", action="store_true", help="Resolve the production Jira base URL (default: sandbox).")
    parser.add_argument("--log-level", default="INFO", help="Logging verbosity (default: INFO).")
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Optional[Iterable[str]] = None) -> None:
    args = parse_args(argv)
    try:
        log_level = resolve_log_level(args.log_level)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    configure_logging(log_level)

    try:
        jira_base_url = normalize_jira_base_url(resolve_jira_base_url(load_credentials(), prod=args.prod))
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if not jira_base_url:
        raise SystemExit("Jira base URL is required.")

    output_dir = Path(args.output_dir)
    database = Database(db_path=args.database)
    with database.connect() as connection:
        database.initialize(connection)
        projects = parse_project_list(
            connection=connection,
            project_gid=args.project_gid,
            project_gids=args.project_gids,
            project_gids_file=args.project_gids_file,
            team_gid=args.team_gid,
            archived_scope=args.archived_scope,
        )

    if args.filename and len(projects) > 1:
        raise SystemExit("--filename can only be used when exporting a single project.")

    task_parts: List[CsvPart] = []
    skipped_empty_projects: List[tuple[str, str]] = []
    skipped_log: List[Dict[str, str]] = []
    for project_gid, project_name in projects:
        logger.info("Starting archival export for project %s (%s)", project_name, project_gid)
        try:
            output_path = export_archival_project(
                args.database,
                project_gid,
                output_dir,
                jira_base_url,
                filename=args.filename,
                fallback_atlassian_id=args.fallback_atlassian_id,
                export_id=args.export_id,
                skipped_log=skipped_log,
            )
        except NoTasksFoundError:
            skipped_empty_projects.append((project_gid, project_name))
            logger.warning(
                "Skipping archival project %s (%s): no tasks found.",
                project_name,
                project_gid,
            )
            continue
        if output_path:
            logger.info("Exported archival project %s (%s) to %s", project_name, project_gid, output_path)
            project_parts = partition_project_csv(output_path)
            task_parts.extend(project_parts)
            if len(project_parts) > 1:
                logger.info(
                    "Split archival project %s into %s Jira CSV parts: %s",
                    project_name,
                    len(project_parts),
                    ", ".join(
                        f"{part.path.name} ({part.issue_count:,} issues)"
                        for part in project_parts
                    ),
                )

    if skipped_log:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
        skipped_path = output_dir / f"_Consolidated-{timestamp}_archival_skipped_tasks.csv"
        output_dir.mkdir(parents=True, exist_ok=True)
        with skipped_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=["task_gid", "skipped_project", "existing_project", "export_id"],
            )
            writer.writeheader()
            writer.writerows(skipped_log)
        logger.info("Wrote archival skipped-task report to %s", skipped_path)

    if task_parts:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
        batches = pack_csv_parts(task_parts)
        consolidated_parts: List[CsvPart] = []
        for number, batch in enumerate(batches, start=1):
            suffix = f"_part_{number:03d}" if len(batches) > 1 else ""
            consolidated_path = output_dir / f"_Consolidated-{timestamp}_archival_tasks{suffix}.csv"
            merge_csv_files(
                [part.path for part in batch],
                consolidated_path,
                source_column="project",
                strip_suffix=None,
            )
            issue_count = sum(part.issue_count for part in batch)
            consolidated_parts.append(CsvPart(consolidated_path, issue_count))
            logger.info(
                "Wrote consolidated archival tasks to %s (%s issues)",
                consolidated_path,
                f"{issue_count:,}",
            )
        manifest_path = output_dir / f"_Consolidated-{timestamp}_archival_tasks_manifest.csv"
        write_partition_manifest(manifest_path, task_parts, consolidated_parts)
        logger.info("Wrote archival Jira CSV partition manifest to %s", manifest_path)

    organize_outputs(output_dir)
    if skipped_empty_projects:
        logger.warning(
            "Skipped %s archival project(s) with no tasks: %s",
            len(skipped_empty_projects),
            ", ".join(f"{name} ({gid})" for gid, name in skipped_empty_projects),
        )


if __name__ == "__main__":
    main()
