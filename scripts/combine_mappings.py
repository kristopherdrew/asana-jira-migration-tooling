#!/usr/bin/env python3
"""
Combine multiple mapping CSVs with deduplication logic.

For projects with no mapping (empty jira_project_key): keep only one record.
For projects with at least one mapping: keep all records, add source_file column.
"""

import csv
import sys
from pathlib import Path
from collections import defaultdict


def combine_mappings(input_files, output_file):
    """
    Combine CSVs with deduplication.

    Args:
        input_files: List of input CSV file paths
        output_file: Output CSV file path
    """
    all_rows = []
    fieldnames = None

    for input_path in input_files:
        with open(input_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            if fieldnames is None:
                fieldnames = reader.fieldnames

            for row in reader:
                row['source_file'] = Path(input_path).name
                all_rows.append(row)

    if not all_rows:
        print("No rows found in input files", file=sys.stderr)
        return

    # Build mapping: project_gid -> (has_mapping, rows)
    project_rows = defaultdict(lambda: {'has_mapping': False, 'rows': []})

    for row in all_rows:
        project_gid = row['project_gid']
        has_mapping = bool(row.get('jira_project_key', '').strip())

        if has_mapping:
            project_rows[project_gid]['has_mapping'] = True

        project_rows[project_gid]['rows'].append(row)

    # Build output: deduplicate unmapped projects, keep only mapped rows for projects with mappings
    output_rows = []
    for project_gid, data in project_rows.items():
        if data['has_mapping']:
            # Keep only mapped rows for projects with mappings
            mapped_rows = [r for r in data['rows'] if r.get('jira_project_key', '').strip()]
            output_rows.extend(mapped_rows)
        else:
            # Keep only first row for projects with no mappings
            output_rows.append(data['rows'][0])

    # Sort: mapped status (Mapped first), task count (descending), project_gid (ascending)
    output_rows.sort(
        key=lambda r: (
            r.get('mapping_status', '') != 'Mapped',  # False (Mapped) sorts before True (Not Mapped)
            -int(r.get('asana_task_count', '0') or '0'),  # Negative for descending
            r.get('project_gid', '')  # Ascending to group duplicate projects
        )
    )

    # Write output
    output_fieldnames = fieldnames + ['source_file']
    with open(output_file, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=output_fieldnames)
        writer.writeheader()
        writer.writerows(output_rows)

    print(f"Combined {len(all_rows)} rows into {len(output_rows)} rows")
    print(f"Output written to {output_file}")


def main(argv=None):
    """CLI entrypoint."""
    if argv is None:
        argv = sys.argv[1:]

    if not argv:
        print("Usage: combine_mappings.py <input1.csv> [input2.csv ...] [-o output.csv]")
        sys.exit(1)

    output_file = "combined_mappings.csv"
    input_files = []

    i = 0
    while i < len(argv):
        if argv[i] == '-o' and i + 1 < len(argv):
            output_file = argv[i + 1]
            i += 2
        else:
            input_files.append(argv[i])
            i += 1

    if not input_files:
        print("Error: no input files specified", file=sys.stderr)
        sys.exit(1)

    combine_mappings(input_files, output_file)


if __name__ == '__main__':
    main()
