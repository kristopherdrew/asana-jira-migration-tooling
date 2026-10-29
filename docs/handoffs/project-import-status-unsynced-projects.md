# Handoff: unsynced project GIDs in the Project Import Status report

Status: open. Parked for later. The report omits these projects and logs a warning. Nothing has been changed to fix the sync.

## Problem

Task memberships (`task_projects`) reference 14 project GIDs that have no row in `projects`. Each of these GIDs has at least one mapped Jira task on a selected site, so the Project Import Status report cannot show them. The report logs a warning naming them (`scripts/export_project_import_status.py`, `_load_site_mappings`). The workspace-wide count appears in the Summary tab as "Unsynced project GIDs referenced by tasks".

Unsynced GIDs (Roblox profile DB, `data/asana_client_roblox.sqlite`):

```
1218409368486380  1218409473411397  1218409574675511  1218409656650185
1218409687483855  1218840127135235  1218840127835995  1218840128168059
1218840156221862  1218840156665473  1218840156825479  1218840180354204
1218840221240892  1218840221528881
```

Task counts range from 1 to 17 per GID. Most are mapped to `archive_jira` or `production_jira`. Their section name is "Untitled section" in every case checked.

## What we know

- No row in `projects`. The GIDs also have no `team_project_jira_mappings`, `project_jira_decisions`, or `project_members` rows.
- Task permalinks contain the same GIDs. For example, `https://app.asana.com/1/5153924692276/project/1218409473411397/task/1217077856825005`. So they are real Asana projects, not corrupt IDs.
- Some tasks are cross-listed. Their permalinks point to a different project GID than the membership that created the row.
- `projects` has 3,250 rows and includes archived projects (322 archived) and personal projects (433 flagged `is_personal_project = 1`). Its `synced_at` ranges from 2026-06-12 to 2026-09-10.
- `scripts/get_projects.py` fills `projects` from `fetch_workspace_projects` (`/workspaces/{gid}/projects`) or `fetch_team_projects` (`/teams/{gid}/projects`), in `src/tasks/projects.py`. Any project the token's user cannot see through those endpoints is never stored.
- `scripts/get_tasks.py` writes membership rows for every project a task belongs to. It does not check that the project exists in `projects`. Its existing candidate logic (`build_missing_project_candidate`) only covers projects that are in `projects` but have no tasks.

## Working hypothesis (not yet confirmed)

These are projects the sync token's user cannot see through the workspace or team project endpoints. Likely candidates are private projects, or projects the user is not a member of, that contain tasks which are also visible through another project.

## Next steps

1. **Confirm against the live API (read-only).** For each GID, run `GET /projects/{gid}` with the profile's Asana token. A 200 response means the project exists and is readable, so it can be fetched into `projects`. A 403 or 404 means the token cannot see it. Use the profile's token via `src/lib/config.py`. Do not write any data during this check.
2. **Fix, depending on step 1.**
   - If readable: add a targeted fetch that upserts these GIDs into `projects` (a small script or a flag on `get_projects.py`, e.g. `--project-gids`).
   - If forbidden: decide whether the token user should be added as a member. `scripts/add_service_user_to_projects.py` already does this for an explicit project list. Note that Asana may still refuse for private projects.
3. **Prevent recurrence.** Have `get_projects.py` or `get_tasks.py` report `task_projects` GIDs with no `projects` row at sync time, similar to the existing missing-project candidate logic.
4. **Decide on report behaviour** once the cause is known. The report currently omits these projects. Showing them as "(not synced)" rows would make mapped Jira tasks visible in the Projects and Multi-Site tabs.

## How to re-check

```sql
SELECT DISTINCT tp.project_gid
FROM task_projects tp
JOIN tasks t ON t.gid = tp.task_gid AND t.is_deleted = 0
WHERE tp.project_gid NOT IN (SELECT gid FROM projects);
```

Run this on a copy of the profile DB, not the live one. Re-run `scripts/export_project_import_status.py` with `--jira-site` and read the warning or the Summary metric.
