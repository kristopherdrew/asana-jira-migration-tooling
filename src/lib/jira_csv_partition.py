"""Hierarchy-safe partitioning helpers for Jira issue-import CSV files."""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Sequence


MAX_JIRA_ISSUES_PER_CSV = 8_000
ID_COLUMNS = ("External issue ID", "task_gid")
PARENT_COLUMN = "jira_parent_gid"
DEPTH_COLUMN = "jira_hierarchy_depth"
NAME_COLUMN = "task_name"


@dataclass(frozen=True)
class CsvPart:
    path: Path
    issue_count: int


def _column_index(header: Sequence[str], names: Sequence[str]) -> int:
    for name in names:
        if name in header:
            return header.index(name)
    raise SystemExit(f"Jira CSV is missing required column: {' or '.join(names)}")


def count_csv_issues(path: Path) -> int:
    with path.open("r", newline="", encoding="utf-8") as handle:
        return max(sum(1 for _ in csv.reader(handle)) - 1, 0)


def _read_csv(path: Path) -> tuple[List[str], List[List[str]]]:
    with path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration:
            raise SystemExit(f"Jira CSV is empty: {path}")
        return header, list(reader)


def _write_csv(path: Path, header: Sequence[str], rows: Iterable[Sequence[str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def _hierarchy_groups(rows: List[List[str]], id_index: int, parent_index: int) -> List[List[List[str]]]:
    row_by_id = {row[id_index]: row for row in rows if row[id_index]}
    root_cache: dict[str, str] = {}

    def root_id(row: List[str]) -> str:
        task_id = row[id_index]
        if task_id in root_cache:
            return root_cache[task_id]
        current = row
        visited: set[str] = set()
        while current[id_index] not in visited:
            visited.add(current[id_index])
            parent_id = current[parent_index]
            parent = row_by_id.get(parent_id)
            if not parent:
                break
            current = parent
        root = current[id_index] or task_id
        for visited_id in visited:
            root_cache[visited_id] = root
        return root

    grouped: dict[str, List[List[str]]] = {}
    order: List[str] = []
    for row in rows:
        root = root_id(row)
        if root not in grouped:
            grouped[root] = []
            order.append(root)
        grouped[root].append(row)
    return [grouped[root] for root in order]


def _balanced_bins(groups: Sequence[List[List[str]]], limit: int) -> List[List[List[List[str]]]]:
    if not groups:
        return []
    oversized = [len(group) for group in groups if len(group) > limit]
    if oversized:
        raise SystemExit(
            f"A real task hierarchy contains {max(oversized):,} issues and cannot fit in the "
            f"{limit:,}-issue Jira CSV limit without separating a parent from its children."
        )
    bin_count = max(1, math.ceil(sum(map(len, groups)) / limit))
    ordered = sorted(enumerate(groups), key=lambda item: (-len(item[1]), item[0]))
    while True:
        bins: List[List[List[List[str]]]] = [[] for _ in range(bin_count)]
        sizes = [0] * bin_count
        failed = False
        for _, group in ordered:
            choices = sorted(range(bin_count), key=lambda idx: (sizes[idx], idx))
            target = next((idx for idx in choices if sizes[idx] + len(group) <= limit), None)
            if target is None:
                failed = True
                break
            bins[target].append(group)
            sizes[target] += len(group)
        if not failed:
            return [bucket for bucket in bins if bucket]
        bin_count += 1


def _split_oversized_synthetic_group(
    group: List[List[str]],
    header: Sequence[str],
    id_index: int,
    parent_index: int,
    depth_index: int,
    limit: int,
) -> List[List[List[str]]]:
    root = next((row for row in group if row[parent_index] == ""), group[0])
    try:
        is_synthetic = int(root[depth_index]) < 0
    except (TypeError, ValueError):
        is_synthetic = False
    if not is_synthetic:
        return [group]

    root_id = root[id_index]
    descendants = [row for row in group if row is not root]
    branches = _hierarchy_groups(descendants, id_index, parent_index)
    bins = _balanced_bins(branches, limit - 1)
    id_indexes = [header.index(name) for name in ID_COLUMNS if name in header]
    name_index = header.index(NAME_COLUMN) if NAME_COLUMN in header else None
    split_groups: List[List[List[str]]] = []
    for number, bucket in enumerate(bins, start=1):
        part_id = f"{root_id}::part::{number:03d}"
        epic = list(root)
        for index in id_indexes:
            epic[index] = part_id
        if name_index is not None:
            epic[name_index] = f"{epic[name_index]} — Part {number}"
        part_rows = [epic]
        for branch in bucket:
            for original in branch:
                row = list(original)
                if row[parent_index] == root_id:
                    row[parent_index] = part_id
                part_rows.append(row)
        split_groups.append(part_rows)
    return split_groups


def partition_project_csv(
    path: Path,
    limit: int = MAX_JIRA_ISSUES_PER_CSV,
) -> List[CsvPart]:
    """Split one project CSV while keeping every Jira parent hierarchy together."""
    if limit < 1 or limit > MAX_JIRA_ISSUES_PER_CSV:
        raise ValueError(f"CSV issue limit must be between 1 and {MAX_JIRA_ISSUES_PER_CSV}.")
    header, rows = _read_csv(path)
    if len(rows) <= limit:
        return [CsvPart(path, len(rows))]

    id_index = _column_index(header, ID_COLUMNS)
    parent_index = _column_index(header, (PARENT_COLUMN,))
    depth_index = _column_index(header, (DEPTH_COLUMN,))
    groups = _hierarchy_groups(rows, id_index, parent_index)
    expanded: List[List[List[str]]] = []
    try:
        for group in groups:
            if len(group) > limit:
                split = _split_oversized_synthetic_group(
                    group, header, id_index, parent_index, depth_index, limit
                )
                if split == [group]:
                    raise SystemExit(
                        f"A real task hierarchy in {path.name} contains {len(group):,} issues and "
                        f"cannot fit in the {limit:,}-issue Jira CSV limit."
                    )
                expanded.extend(split)
            else:
                expanded.append(group)
        bins = _balanced_bins(expanded, limit)
    except SystemExit:
        # Never leave behind an oversized CSV that could be imported accidentally.
        path.unlink(missing_ok=True)
        raise

    parts: List[CsvPart] = []
    try:
        for number, bucket in enumerate(bins, start=1):
            part_path = path.with_name(f"{path.stem}_part_{number:03d}{path.suffix}")
            part_rows = [row for group in bucket for row in group]
            _write_csv(part_path, header, part_rows)
            parts.append(CsvPart(part_path, len(part_rows)))
    except OSError:
        for part in parts:
            part.path.unlink(missing_ok=True)
        path.unlink(missing_ok=True)
        raise
    path.unlink()
    return parts


def pack_csv_parts(
    parts: Sequence[CsvPart],
    limit: int = MAX_JIRA_ISSUES_PER_CSV,
) -> List[List[CsvPart]]:
    """Balance already-safe project parts into consolidated batches without splitting them."""
    if any(part.issue_count > limit for part in parts):
        raise ValueError("Cannot consolidate a project part that exceeds the Jira CSV limit.")
    bin_count = max(1, math.ceil(sum(part.issue_count for part in parts) / limit))
    ordered = sorted(enumerate(parts), key=lambda item: (-item[1].issue_count, item[0]))
    while True:
        bins: List[List[CsvPart]] = [[] for _ in range(bin_count)]
        sizes = [0] * bin_count
        failed = False
        for _, part in ordered:
            choices = sorted(range(bin_count), key=lambda idx: (sizes[idx], idx))
            target = next(
                (idx for idx in choices if sizes[idx] + part.issue_count <= limit),
                None,
            )
            if target is None:
                failed = True
                break
            bins[target].append(part)
            sizes[target] += part.issue_count
        if not failed:
            return [bucket for bucket in bins if bucket]
        bin_count += 1


def write_partition_manifest(
    path: Path,
    project_parts: Sequence[CsvPart],
    consolidated_parts: Sequence[CsvPart],
) -> None:
    """Write an auditable summary of bounded project and consolidated CSV outputs."""
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["kind", "filename", "issue_count"])
        for kind, parts in (
            ("project", project_parts),
            ("consolidated", consolidated_parts),
        ):
            for part in parts:
                writer.writerow([kind, part.path.name, part.issue_count])
