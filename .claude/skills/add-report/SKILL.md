---
name: add-report
description: Use when adding a new CSV report to this repo or adding/changing a tab in the combined multi-tab .xlsx workbook (export_all_reports_xlsx.py). Specializes the add-cli-script checklist for the report conventions — date formatting, the xlsx_writer, and the REPORT_SPECS registration that folds a report into the combined workbook.
---

# Add a CSV report (and wire it into the combined workbook)

A report is a CLI script first — so **follow the [add-cli-script](../add-cli-script/SKILL.md)
skill for the full scaffold** (place code in `src/`, entrypoint in `scripts/`, register in the
interactive CLI's **Reports** menu, mirror a test, document it). This skill adds only the
report-specific conventions on top.

## Report conventions
- **Reads SQLite, writes CSV.** Reports read `data/asana.sqlite` and emit a CSV; they make no
  API calls, so they do **not** take `--workers`. Standard flags: `--database` (default
  `data/asana.sqlite`), `--output-dir` (default `exports/`), `--filename` (default
  `<name>_<timestamp>.csv`), `--log-level`, and `--workspace-gid` *only if* the report builds
  Asana URLs. Add `--team-gid` if it can be scoped to one team.
- **Dates as `YYYY-MM-DD`.** Every date column exports zero-padded `YYYY-MM-DD` — reuse
  `src/lib/date_formatting.py`; don't hand-roll formatting.
- **Atlassian-aware user columns.** Resolve people to `*_name` / `*_email` /
  `*_atlassian_status` (joining `users` + `user_atlassian_accounts`), leaving them blank when
  unmapped — match the shape used by the existing reports (see Team Report / Project Details).
- **Document it** in [docs/reports.md](../../../docs/reports.md) (purpose, inputs, outputs,
  flags) and add the one-line index entry in `AGENTS.md`.

## Wire it into the combined workbook (`scripts/export_all_reports_xlsx.py`)
If the report belongs in the stakeholder workbook (every non-Jira report does), add it to the
combiner — otherwise it won't appear in the `.xlsx`:
1. Import the module in the `from scripts import (...)` block.
2. Add an entry to **`REPORT_SPECS`**: `(module, "<temp>.csv", <accepts_--workspace-gid>,
   [("<Sheet Title>", "<produced filename>")])`. A report that writes companion CSVs (like the
   portfolio summary + items) contributes multiple `(title, filename)` tuples — list each.
   The combiner runs each `main()` into a temp dir with
   `--database/--output-dir/--filename/--log-level` (and `--workspace-gid` when the flag is
   `True`), so the third tuple field **must** match whether the report actually accepts
   `--workspace-gid`.
3. The `Overview` index tab (generation time, source DB, per-tab row counts) is built
   automatically from the sheet list — no manual edit.
4. Output is written by the dependency-free `src/lib/xlsx_writer.py` (OOXML via `zipfile`) —
   **no third-party package.** Numeric cells are auto-typed; long all-digit ids (Asana GIDs)
   are kept as text to avoid Excel's 15-digit precision loss. Headers are bold + frozen.

## Verify
- Run the report standalone and open the CSV.
- Run `python3 -m scripts.export_all_reports_xlsx --database data/asana.sqlite` and confirm the
  new tab appears with the right row count on the `Overview` sheet.
- Run the suite: `python3 -m unittest discover tests`.
