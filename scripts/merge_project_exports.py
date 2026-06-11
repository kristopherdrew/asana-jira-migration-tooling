#!/usr/bin/env python3
"""Merge multiple per-project Jira export CSVs into a single consolidated file."""

from __future__ import annotations

import argparse
import csv
import glob
import logging
import os
import re
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger(__name__)
DEFAULT_OUTPUT = PROJECT_ROOT / "exports" / f"_Consolidated-{datetime.now().strftime('%Y%m%d-%H%M')}_tasks.csv"
TIMESTAMP_STEM_PATTERN = re.compile(r"^(.*)-\d{8}-\d{4}$")


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--inputs",
        required=True,
        nargs="+",
        help="List of glob patterns or file paths for per-project CSVs (e.g., exports/*-*.csv)",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help=f"Path for consolidated CSV (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--source-column",
        help="Optional column name to store the originating filename (stem).",
    )
    parser.add_argument(
        "--strip-suffix",
        help="Optional suffix to strip from each filename stem when populating the source column (e.g., a shared timestamp).",
    )
    parser.add_argument(
        "--no-merge-field-counts",
        action="store_true",
        help="Skip merging companion *_field_counts.csv files (default is to merge when present).",
    )
    parser.add_argument(
        "--no-merge-link-mods",
        action="store_true",
        help="Skip merging companion *_link_modifications.txt files (default is to merge when present).",
    )
    parser.add_argument(
        "--field-counts-output",
        help="Optional output path for merged field_counts CSV (defaults to <output_stem>_field_counts.csv).",
    )
    parser.add_argument(
        "--link-mods-output",
        help="Optional output path for merged link_modifications log (defaults to <output_stem>_link_modifications.csv).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging verbosity (default: INFO)",
    )
    return parser.parse_args(argv)


def canonicalize_headers(headers: Sequence[str]) -> List[str]:
    # Used for companion files where duplicate headers are unlikely; preserve originals.
    return [h or "" for h in headers]


def derive_source_value(path: Path, strip_suffix: str | None) -> str:
    stem = path.stem
    if stem.endswith("_tasks_field_counts"):
        stem = stem[: -len("_tasks_field_counts")]
    elif stem.endswith("_tasks_link_modifications"):
        stem = stem[: -len("_tasks_link_modifications")]
    elif stem.endswith("_link_modifications"):
        stem = stem[: -len("_link_modifications")]
    elif stem.endswith("_field_counts"):
        stem = stem[: -len("_field_counts")]
    elif stem.endswith("_tasks"):
        stem = stem[: -len("_tasks")]
    match = TIMESTAMP_STEM_PATTERN.match(stem)
    if match:
        stem = match.group(1)
    if strip_suffix and stem.endswith(strip_suffix):
        stem = stem[: -len(strip_suffix)]
    return stem


def merge_csv_files(
    files: List[Path],
    output_path: Path,
    source_column: str | None = None,
    strip_suffix: str | None = None,
) -> Path:
    if not files:
        raise SystemExit("No input CSVs were provided after glob expansion.")

    source_name = source_column or None

    header_order: List[str] = []
    max_counts: Dict[str, int] = OrderedDict()
    per_file_rows: List[Tuple[List[str], List[List[str]], Optional[str]]] = []

    for file_path in files:
        with file_path.open("r", newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle)
            try:
                raw_header = next(reader)
            except StopIteration:
                logger.warning("Skipping empty CSV: %s", file_path)
                continue
            rows = list(reader)
        if source_name and source_name in raw_header:
            raise SystemExit(
                f"Source column '{source_name}' conflicts with an existing column in {file_path}. Choose a different name."
            )
        # Track max occurrences per header label
        counts: Dict[str, int] = {}
        for label in raw_header:
            counts[label] = counts.get(label, 0) + 1
            if label not in max_counts:
                header_order.append(label)
                max_counts[label] = counts[label]
            else:
                max_counts[label] = max(max_counts[label], counts[label])
        source_value = derive_source_value(file_path, strip_suffix) if source_name else None
        per_file_rows.append((raw_header, rows, source_value))

    if not max_counts:
        raise SystemExit("No rows were read from the provided CSVs.")

    # Build union header preserving first-seen order, repeating labels up to max_count
    union_header: List[str] = []
    for label in header_order:
        repeat = max_counts[label]
        if repeat == 1:
            union_header.append(label)
        else:
            union_header.extend([label] * repeat)
    if source_name:
        union_header.append(source_name)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(union_header)
        out_rows: List[List[str]] = []
        for raw_header, rows, source_value in per_file_rows:
            # Build positional map label -> list of values per row
            for row in rows:
                label_counts: Dict[str, int] = {}
                value_map: Dict[Tuple[str, int], str] = {}
                for idx, label in enumerate(raw_header):
                    label_counts[label] = label_counts.get(label, 0) + 1
                    key = (label, label_counts[label])
                    value_map[key] = row[idx] if idx < len(row) else ""
                out_row: List[str] = []
                for label in header_order:
                    repeat = max_counts[label]
                    for occ in range(1, repeat + 1):
                        out_row.append(value_map.get((label, occ), ""))
                if source_name:
                    out_row.append(source_value or "")
                out_rows.append(out_row)

        # Optional sorting: parents before children, then project, then created_at
        if "hierarchy_depth" in union_header:
            depth_idx = union_header.index("hierarchy_depth")
            project_idx = union_header.index("current_project") if "current_project" in union_header else None
            created_idx = union_header.index("created_at") if "created_at" in union_header else None

            def parse_created_at(value: str) -> tuple:
                if not value:
                    return (True, None)
                for fmt in ("%d/%b/%y %I:%M %p", "%d/%b/%y %I:%M%p", "%d/%b/%y %I %p"):
                    try:
                        return (False, datetime.strptime(value.strip(), fmt))
                    except ValueError:
                        continue
                return (True, None)

            def sort_key(row: List[str]) -> tuple:
                raw_depth = row[depth_idx].strip()
                try:
                    depth_val = int(raw_depth)
                except ValueError:
                    depth_val = 1_000_000  # blanks/unparsable go last
                project_val = row[project_idx] if project_idx is not None else ""
                if created_idx is not None and created_idx < len(row):
                    missing_created, created_dt = parse_created_at(row[created_idx])
                else:
                    missing_created, created_dt = (True, None)
                created_key = (missing_created, created_dt or datetime.max)
                return (depth_val, project_val.lower(), created_key)

            out_rows.sort(key=sort_key)

        for out_row in out_rows:
            writer.writerow(out_row)

    logger.info("Merged %s CSV(s) into %s", len(files), output_path)
    return output_path


def merge_field_counts(
    files: List[Path],
    output_path: Path,
    source_column: str | None,
    strip_suffix: str | None,
) -> Optional[Path]:
    if not files:
        return None
    union_headers: OrderedDict[str, None] = OrderedDict()
    rows_out: List[Dict[str, str]] = []
    for file_path in files:
        with file_path.open("r", newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle)
            try:
                raw_header = next(reader)
            except StopIteration:
                continue
            canon_header = canonicalize_headers(raw_header)
            if source_column and source_column in canon_header:
                raise SystemExit(
                    f"Source column '{source_column}' conflicts with an existing column in {file_path}. Choose a different name."
                )
            for col in canon_header:
                if col not in union_headers:
                    union_headers[col] = None
            for row in reader:
                row_dict = {canon_header[idx]: value for idx, value in enumerate(row) if idx < len(canon_header)}
                if source_column:
                    row_dict[source_column] = derive_source_value(file_path, strip_suffix)
                rows_out.append(row_dict)

    if not union_headers:
        return None
    if source_column and source_column not in union_headers:
        union_headers[source_column] = None

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        header_list = list(union_headers.keys())
        writer.writerow(header_list)
        for row in rows_out:
            writer.writerow([row.get(col, "") for col in header_list])

    logger.info("Merged %s field_count file(s) into %s", len(files), output_path)
    return output_path


def merge_link_mods(
    files: List[Path],
    output_path: Path,
    source_column: str | None,
    strip_suffix: str | None,
) -> Optional[Path]:
    if not files:
        return None

    def parse_entries(path: Path) -> List[Dict[str, str]]:
        if path.suffix.lower() == ".csv":
            with path.open("r", newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                return [
                    {
                        "task_gid": row.get("task_gid", ""),
                        "field": row.get("field", ""),
                        "original": row.get("original", ""),
                        "modified": row.get("modified", ""),
                        "full_content": row.get("full_content", ""),
                    }
                    for row in reader
                ]
        entries: List[Dict[str, str]] = []
        current: Dict[str, str] = {}
        collecting_modified: List[str] = []
        collecting_full: List[str] = []
        mode = "header"
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.rstrip("\n")
                if stripped == "" and current:
                    if collecting_modified:
                        current["modified"] = "\n".join(collecting_modified)
                    if collecting_full:
                        current["full_content"] = "\n".join(collecting_full)
                    entries.append(current)
                    current = {}
                    collecting_modified = []
                    collecting_full = []
                    mode = "header"
                    continue
                if mode == "modified":
                    collecting_modified.append(stripped)
                    continue
                if mode == "full_content":
                    collecting_full.append(stripped)
                    continue
                if stripped.startswith("task_gid:"):
                    current["task_gid"] = stripped.split("task_gid:", 1)[1].strip()
                elif stripped.startswith("field:"):
                    current["field"] = stripped.split("field:", 1)[1].strip()
                elif stripped.startswith("original:"):
                    current["original"] = stripped.split("original:", 1)[1].strip()
                elif stripped.startswith("modified:"):
                    mode = "modified"
                    collecting_modified = []
                elif stripped.startswith("full_content:"):
                    mode = "full_content"
                    collecting_full = []
        if current:
            if collecting_modified:
                current["modified"] = "\n".join(collecting_modified)
            if collecting_full:
                current["full_content"] = "\n".join(collecting_full)
            entries.append(current)
        return entries

    rows_out: List[Dict[str, str]] = []
    headers_order: List[str] = ["task_gid", "field", "original", "modified", "full_content"]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    for file_path in files:
        source_value = derive_source_value(file_path, strip_suffix) if source_column else None
        for entry in parse_entries(file_path):
            if source_column:
                entry[source_column] = source_value or ""
            rows_out.append(entry)
    # Add source column at end if needed
    if source_column and source_column not in headers_order:
        headers_order.append(source_column)

    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers_order, extrasaction="ignore")
        writer.writeheader()
        for row in rows_out:
            writer.writerow({col: row.get(col, "") for col in headers_order})

    logger.info("Merged %s link modification file(s) into %s", len(files), output_path)
    return output_path


def main(argv: Optional[Iterable[str]] = None) -> None:
    args = parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO))

    expanded: List[Path] = []
    for pattern in args.inputs:
        matched = [Path(path) for path in glob.glob(pattern)]
        if not matched and Path(pattern).exists():
            matched = [Path(pattern)]
        expanded.extend(matched)
    # Preserve order, drop duplicates
    seen = set()
    unique_files: List[Path] = []
    for path in expanded:
        if path in seen:
            continue
        seen.add(path)
        unique_files.append(path)

    # Restrict to main task CSVs (skip companions)
    task_files = [
        path
        for path in unique_files
        if not path.name.endswith("_field_counts.csv")
        and not path.name.endswith("_link_modifications.csv")
        and not path.name.endswith("_link_modifications.txt")
    ]

    output_path = Path(args.output)
    merge_csv_files(task_files, output_path, args.source_column, args.strip_suffix)

    # Companion: field_counts
    if not args.no_merge_field_counts:
        fc_files = [path.with_name(f"{path.stem}_field_counts.csv") for path in task_files if path.with_name(f"{path.stem}_field_counts.csv").exists()]
        fc_out = Path(args.field_counts_output) if args.field_counts_output else output_path.with_name(f"{output_path.stem}_field_counts.csv")
        merge_field_counts(fc_files, fc_out, args.source_column, args.strip_suffix)

    # Companion: link modifications
    if not args.no_merge_link_mods:
        lm_files: List[Path] = []
        for path in task_files:
            csv_path = path.with_name(f"{path.stem}_link_modifications.csv")
            txt_path = path.with_name(f"{path.stem}_link_modifications.txt")
            if csv_path.exists():
                lm_files.append(csv_path)
            elif txt_path.exists():
                lm_files.append(txt_path)
        lm_out = Path(args.link_mods_output) if args.link_mods_output else output_path.with_name(f"{output_path.stem}_link_modifications.csv")
        merge_link_mods(lm_files, lm_out, args.source_column, args.strip_suffix)


if __name__ == "__main__":  # pragma: no cover
    main()
