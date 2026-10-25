#!/usr/bin/env python3
import csv
from pathlib import Path
from collections import defaultdict

exports_dir = Path('/Users/kperez/Desktop/Bitbucket/asana_jira_migration/exports')
mappings1_path = exports_dir / 'mappings.csv'
mappings2_path = exports_dir / 'mappings2.csv'
output_path = exports_dir / 'mappings_combined.csv'

# Read both files
data = {}
for source_file in [('mappings.csv', mappings1_path), ('mappings2.csv', mappings2_path)]:
    source_name, file_path = source_file
    with open(file_path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            project_gid = row['project_gid']
            if project_gid not in data:
                data[project_gid] = []
            data[project_gid].append((source_name, row))

# Process data according to logic
output_rows = []
for project_gid, entries in data.items():
    # Check if any entry has a "Mapped" status
    mapped_entries = [(source, row) for source, row in entries if row.get('mapping_status') == 'Mapped']

    if mapped_entries:
        # If there are mapped entries, include all of them with source
        for source, row in mapped_entries:
            output_rows.append((source, row))
    else:
        # If there are no mapped entries, include only the first one (dedup unmapped)
        source, row = entries[0]
        output_rows.append((source, row))

# Write combined output
output_rows.sort(key=lambda x: (x[1]['project_gid']))

with open(output_path, 'w', newline='') as f:
    # Get fieldnames from first row
    fieldnames = list(output_rows[0][1].keys()) + ['source']
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()

    for source, row in output_rows:
        row['source'] = source
        writer.writerow(row)

print(f"Combined mappings file created: {output_path}")
print(f"Total rows: {len(output_rows)}")
print(f"  From mappings.csv: {sum(1 for s, _ in output_rows if s == 'mappings.csv')}")
print(f"  From mappings2.csv: {sum(1 for s, _ in output_rows if s == 'mappings2.csv')}")
