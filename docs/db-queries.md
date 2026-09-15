# Database Query Cookbook

Ready-to-run SQL for investigating the local `data/asana.sqlite` snapshot. Pair this with
the [data dictionary](data-model.md) (table purpose / provenance) and the live introspection
tool `python3 -m scripts.describe_db` (row counts, columns, sample rows).

Run a query with the sqlite3 CLI:

```bash
sqlite3 -header -column data/asana.sqlite "SELECT ..."
# or open an interactive shell
sqlite3 data/asana.sqlite
```

← Back to [README](../README.md)

> Soft-deleted rows (`task_comments`, `task_attachments`, `project_updates`) keep
> `is_deleted=1`. Always filter `is_deleted = 0` unless you specifically want history.

## Projects

**Projects with status updates/comments but no tasks** — the task-less, narrative-only projects
that are otherwise easy to miss:
```sql
SELECT p.gid, p.name, p.team_name,
       SUM(CASE WHEN pu.kind = 'status_update' THEN 1 ELSE 0 END) AS status_updates,
       SUM(CASE WHEN pu.kind = 'comment'       THEN 1 ELSE 0 END) AS comments
FROM projects p
JOIN project_updates pu ON pu.project_gid = p.gid AND pu.is_deleted = 0
WHERE COALESCE(p.num_tasks, 0) = 0
GROUP BY p.gid
ORDER BY status_updates DESC, comments DESC;
```

**A single project's narrative in chronological order** (export preview — each row is a future
Jira comment):
```sql
SELECT created_at, kind, status_type, title, substr(text, 1, 80) AS preview
FROM project_updates
WHERE project_gid = '<project_gid>' AND is_deleted = 0
ORDER BY created_at;
```

**Status-update volume by type across all projects:**
```sql
SELECT status_type, COUNT(*) AS n
FROM project_updates
WHERE kind = 'status_update' AND is_deleted = 0
GROUP BY status_type ORDER BY n DESC;
```

**Tasks per project (top 25):**
```sql
SELECT p.name, p.team_name, COUNT(DISTINCT tp.task_gid) AS tasks
FROM task_projects tp
JOIN projects p ON p.gid = tp.project_gid
GROUP BY tp.project_gid
ORDER BY tasks DESC
LIMIT 25;
```

## Users & activity

**Most active users** (tasks created / completed / commented on):
```sql
SELECT u.name, u.email,
       COUNT(DISTINCT tc.gid)                                   AS comments,
       (SELECT COUNT(*) FROM tasks t WHERE t.created_by_gid = u.gid)   AS created,
       (SELECT COUNT(*) FROM tasks t WHERE t.completed_by_gid = u.gid) AS completed
FROM users u
LEFT JOIN task_comments tc ON tc.created_by_gid = u.gid AND tc.is_deleted = 0
GROUP BY u.gid
ORDER BY created DESC
LIMIT 25;
```

**Users with no Atlassian mapping** (migration gap — their comments can't resolve an author):
```sql
SELECT u.gid, u.name, u.email
FROM users u
LEFT JOIN user_atlassian_accounts ua ON ua.user_gid = u.gid
WHERE ua.user_gid IS NULL AND u.is_active = 1
ORDER BY u.name;
```

## Jira migration status

**Tasks already mapped to a Jira issue vs not:**
```sql
SELECT CASE WHEN jm.task_gid IS NULL THEN 'unmapped' ELSE 'mapped' END AS state,
       COUNT(DISTINCT t.gid) AS tasks
FROM tasks t
LEFT JOIN task_jira_mappings jm ON jm.task_gid = t.gid
GROUP BY state;
```

**Team/project → Jira epic/issue mappings created so far:**
```sql
SELECT kind, COUNT(*) AS created
FROM team_project_jira_mappings
GROUP BY kind;
```

## Custom fields

**Custom field usage per project** (uses the prebuilt view):
```sql
SELECT project_name, custom_field_name, value_count
FROM view_project_custom_field_configurations
WHERE value_count > 0
ORDER BY value_count DESC
LIMIT 50;
```

## Data freshness

**Tasks changed in the last 14 days for selected projects** — replace the values in the
`selected_projects` CTE and the cutoff with the exact UTC cutoff used by the API export:
```sql
WITH selected_projects(project_gid) AS (
    VALUES ('<project_gid_1>'), ('<project_gid_2>')
),
selected_task_projects AS (
    SELECT tp.task_gid,
           GROUP_CONCAT(tp.project_gid, ';') AS selected_project_gids,
           GROUP_CONCAT(p.name, ';') AS selected_project_names
    FROM task_projects tp
    JOIN selected_projects sp ON sp.project_gid = tp.project_gid
    LEFT JOIN projects p ON p.gid = tp.project_gid
    GROUP BY tp.task_gid
)
SELECT t.gid AS task_gid,
       t.name,
       t.modified_at,
       t.synced_at,
       t.completed,
       t.completed_at,
       t.parent_gid,
       t.permalink_url,
       stp.selected_project_gids,
       stp.selected_project_names
FROM tasks t
JOIN selected_task_projects stp ON stp.task_gid = t.gid
WHERE t.modified_at IS NOT NULL
  AND julianday(t.modified_at) > julianday('<utc_cutoff>')
ORDER BY t.modified_at DESC, t.gid;
```

Use the same cutoff for the live API comparison command:
```bash
python3 -m scripts.export_recent_task_changes_api \
  --project-gids-file inputs/project_gids.txt \
  --since 2026-09-01T12:00:00Z
```

**Latest sync time per core table** — quickly see how stale a snapshot is:
```sql
SELECT 'tasks'           AS tbl, MAX(synced_at) FROM tasks
UNION ALL SELECT 'projects',        MAX(synced_at) FROM projects
UNION ALL SELECT 'task_comments',   MAX(synced_at) FROM task_comments
UNION ALL SELECT 'project_updates', MAX(synced_at) FROM project_updates
UNION ALL SELECT 'users',           MAX(synced_at) FROM users;
```
