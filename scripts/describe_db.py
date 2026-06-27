#!/usr/bin/env python3
"""Read-only introspection of the SQLite database to make investigation easier.

Combines the human context/provenance from `src/lib/table_metadata.py` (what each
table stores, which script populates it, the source API/CSV, and write semantics)
with the live structure introspected from SQLite (columns, foreign keys, indexes,
row counts, and the latest `synced_at`).

Modes:
  (default)            describe every table/view: context + structure + row counts.
  --table <name>       focus on one table/view and print a few sample rows.
  --markdown           emit the data dictionary as Markdown (structure from a fresh
                       canonical schema so the output is reproducible without data).

Never writes to the database.
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from src.lib.database import Database, resolve_default_db_path
from src.lib.logging_utils import configure_logging, resolve_log_level
from src.lib.table_metadata import GROUP_ORDER, TABLE_METADATA, TableMeta

DEFAULT_DB_PATH = "data/asana.sqlite"
DB_QUERIES_DOC = "docs/db-queries.md"
logger = logging.getLogger(__name__)


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default=resolve_default_db_path(), help=f"SQLite file (default: {DEFAULT_DB_PATH}).")
    parser.add_argument("--table", help="Focus on a single table/view and print sample rows.")
    parser.add_argument("--samples", type=int, default=3, help="Sample rows to print with --table (default: 3).")
    parser.add_argument("--markdown", action="store_true", help="Emit the data dictionary as Markdown to stdout.")
    parser.add_argument("--log-level", default="WARNING", help="Logging verbosity (default: WARNING).")
    return parser.parse_args(argv)


# ----------------------------- introspection ----------------------------- #
def existing_objects(conn: sqlite3.Connection) -> Dict[str, str]:
    rows = conn.execute(
        "SELECT name, type FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {row[0]: row[1] for row in rows}


def column_info(conn: sqlite3.Connection, name: str) -> List[Tuple[str, str, bool]]:
    """Return [(column, type, is_pk)] for a table/view."""
    rows = conn.execute(f"PRAGMA table_info({name})").fetchall()
    return [(r[1], r[2] or "", bool(r[5])) for r in rows]


def foreign_keys(conn: sqlite3.Connection, name: str) -> List[str]:
    rows = conn.execute(f"PRAGMA foreign_key_list({name})").fetchall()
    # row: id, seq, table, from, to, on_update, on_delete, match
    return [f"{r[3]} → {r[2]}({r[4]})" for r in rows]


def index_names(conn: sqlite3.Connection, name: str) -> List[str]:
    rows = conn.execute(f"PRAGMA index_list({name})").fetchall()
    return [r[1] for r in rows]


def row_count(conn: sqlite3.Connection, name: str) -> Optional[int]:
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
    except sqlite3.Error:
        return None


def latest_synced_at(conn: sqlite3.Connection, name: str, columns: List[str]) -> Optional[str]:
    if "synced_at" not in columns:
        return None
    try:
        return conn.execute(f"SELECT MAX(synced_at) FROM {name}").fetchone()[0]
    except sqlite3.Error:
        return None


# ----------------------------- console output ----------------------------- #
def _print_object(conn: sqlite3.Connection, meta: TableMeta, present: bool) -> None:
    kind = "VIEW" if meta.is_view else "TABLE"
    print(f"\n{'─' * 78}\n{kind}  {meta.name}")
    print(f"  purpose   : {meta.purpose}")
    print(f"  source    : {meta.source}")
    print(f"  scripts   : {', '.join(meta.scripts)}")
    print(f"  written by: {meta.writer}")
    print(f"  semantics : {meta.write_semantics}")
    if not present:
        print("  (not present in this database)")
        return
    cols = column_info(conn, meta.name)
    colnames = [c[0] for c in cols]
    count = row_count(conn, meta.name)
    synced = latest_synced_at(conn, meta.name, colnames)
    stats = f"  rows={count if count is not None else '?'}"
    if synced:
        stats += f"   latest synced_at={synced}"
    print(stats)
    pk = [c[0] for c in cols if c[2]]
    print(f"  columns   : {', '.join(f'{n} {t}'.strip() for n, t, _ in cols)}")
    if pk:
        print(f"  pk        : {', '.join(pk)}")
    fks = foreign_keys(conn, meta.name)
    if fks:
        print(f"  fks       : {'; '.join(fks)}")
    idxs = [i for i in index_names(conn, meta.name) if not i.startswith("sqlite_autoindex")]
    if idxs:
        print(f"  indexes   : {', '.join(idxs)}")


def run_console(conn: sqlite3.Connection) -> None:
    present = existing_objects(conn)
    print(f"Database overview ({len(present)} tables/views present)")
    for group in GROUP_ORDER:
        members = [m for m in TABLE_METADATA.values() if m.group == group]
        if not members:
            continue
        print(f"\n=== {group} ===")
        for meta in members:
            _print_object(conn, meta, meta.name in present)


def run_single_table(conn: sqlite3.Connection, name: str, samples: int) -> None:
    present = existing_objects(conn)
    meta = TABLE_METADATA.get(name)
    if meta is None:
        known = ", ".join(sorted(TABLE_METADATA))
        raise SystemExit(f"Unknown table/view '{name}'. Known: {known}")
    _print_object(conn, meta, name in present)
    if name not in present or samples <= 0:
        return
    cols = [c[0] for c in column_info(conn, name)]
    rows = conn.execute(f"SELECT * FROM {name} LIMIT {int(samples)}").fetchall()
    print(f"\n  sample rows (up to {samples}):")
    for row in rows:
        pairs = []
        for col, val in zip(cols, row):
            text = "" if val is None else str(val)
            if len(text) > 60:
                text = text[:57] + "…"
            pairs.append(f"{col}={text}")
        print("   - " + ", ".join(pairs))


# ----------------------------- markdown output ----------------------------- #
def _markdown_object(conn: sqlite3.Connection, meta: TableMeta) -> List[str]:
    cols = column_info(conn, meta.name)
    pk = {c[0] for c in cols if c[2]}
    lines = [f"#### `{meta.name}`{' (view)' if meta.is_view else ''}", ""]
    lines.append(f"{meta.purpose}")
    lines.append("")
    lines.append(f"- **Source:** {meta.source}")
    lines.append(f"- **Populated by:** {', '.join(f'`{s}`' for s in meta.scripts)}")
    lines.append(f"- **Written by:** `{meta.writer}`")
    lines.append(f"- **Write semantics:** {meta.write_semantics}")
    fks = foreign_keys(conn, meta.name)
    if fks:
        lines.append(f"- **Foreign keys:** {'; '.join(fks)}")
    lines.append("")
    lines.append("| Column | Type | PK |")
    lines.append("|---|---|---|")
    for col, ctype, is_pk in cols:
        lines.append(f"| `{col}` | {ctype or '—'} | {'✓' if (col in pk or is_pk) else ''} |")
    lines.append("")
    return lines


def render_markdown(conn: sqlite3.Connection) -> str:
    out: List[str] = []
    out.append("# Database Data Dictionary")
    out.append("")
    out.append(
        "> Generated by `python3 -m scripts.describe_db --markdown`. Context/provenance "
        "come from `src/lib/table_metadata.py`; column structure is introspected from the "
        "canonical schema in `src/lib/database.py`. Regenerate after schema changes."
    )
    out.append("")
    out.append(
        "For **live** row counts, latest `synced_at`, and sample rows, run "
        "`python3 -m scripts.describe_db` (optionally `--table <name>`) against a populated "
        "database. For ready-to-run investigative queries see [the query cookbook](db-queries.md)."
    )
    out.append("")
    out.append("All synced tables carry `synced_at` (UTC ISO-8601); soft-deleted rows set `is_deleted=1` rather than being removed.")
    out.append("")
    for group in GROUP_ORDER:
        members = [m for m in TABLE_METADATA.values() if m.group == group]
        if not members:
            continue
        out.append(f"## {group}")
        out.append("")
        for meta in members:
            out.extend(_markdown_object(conn, meta))
    return "\n".join(out).rstrip() + "\n"


def _canonical_schema_connection() -> sqlite3.Connection:
    """A throwaway DB with the full canonical schema (no data) for reproducible structure."""
    tmp = Path(tempfile.mkdtemp()) / "schema.sqlite"
    database = Database(str(tmp))
    conn = database.connect()
    database.initialize(conn)
    return conn


def main(argv: Optional[List[str]] = None) -> None:
    args = parse_args(argv)
    try:
        configure_logging(resolve_log_level(args.log_level))
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    if args.markdown:
        conn = _canonical_schema_connection()
        try:
            print(render_markdown(conn), end="")
        finally:
            conn.close()
        return

    db_path = Path(args.database)
    if not db_path.exists():
        raise SystemExit(f"Database not found at {db_path}. Run the sync scripts first, or use --markdown.")
    conn = sqlite3.connect(str(db_path))
    try:
        if args.table:
            run_single_table(conn, args.table, args.samples)
        else:
            run_console(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
