# Phase 4: Assess & Plan Migration

## Purpose

Turn the Asana snapshot into review material and migration decisions. This phase is
for stakeholder conversations, scope approval, data-quality review, and agreement on
how Asana structures should be represented in Jira.

## Recommended Flow

Use **Guided migration readiness review** for the primary stakeholder workbook. It
can pull current migration decisions from Jira, generates the field-and-section
review, and offers the project last-updated report. Run the other reports when a
specific planning question needs more detail.

## Menu Actions and Scripts

- **Guided migration readiness review** orchestrates
  `scripts.atlassian.get_jira_migration_decisions`,
  `scripts.export_field_section_review_report`, and optionally
  `scripts.export_project_last_updated_report`.
- **Export all reports workbook (.xlsx)** runs `scripts.export_all_reports_xlsx` to create
  a broad multi-tab workbook covering projects, users, teams, fields, and portfolios.
- **Export field & section migration review** runs
  `scripts.export_field_section_review_report` for project-owner or team-admin review
  of sections, field values, members, and cross-project similarities.
- **Export project details report** runs `scripts.export_project_details_report` for
  project-level scope and lifecycle information.
- **Export project last-updated report** runs
  `scripts.export_project_last_updated_report` to identify recent project activity.
- **Export project members report** runs `scripts.export_project_members_report` to
  review project membership and member coverage.
- **Export users report** runs `scripts.export_users` to review user activity and
  Atlassian-account readiness.
- **Export team memberships report** runs `scripts.export_team_memberships` to review
  user-to-team membership.
- **Export team report** runs `scripts.export_team_report` for team-level scope.
- **Export custom field usage report** runs
  `scripts.export_custom_field_usage_report` to identify populated fields that may
  require Jira treatment.
- **Export portfolio report** runs `scripts.export_portfolio_report` for portfolio
  hierarchy and migration-scoping review.
- **Preview or create Team/Project Jira hierarchy (API)** runs
  `scripts.atlassian.create_team_project_jira_issues`. It creates Asana teams as Epics and
  projects as Tasks through the Jira REST API. Start with dry-run output, confirm field
  resolution and scope, and only then create issues in the intended Jira environment.
  Successful creation persists mappings directly.
- **Pull migration decisions from Jira** runs
  `scripts.atlassian.get_jira_migration_decisions` and stores the current project
  decisions in SQLite for reports to consume.

## Expected Result

Stakeholders have agreed which projects will migrate, how fields and sections will
map, which users need Atlassian identities, and which projects are inactive,
archival, or excluded. Record decisions before relying on them in later reports.

## Detailed Script References

- [Migration Reports](../reports.md)
- [Pull Asana Migration Decisions From Jira](../jira-integration.md#pull-asana-migration-decisions-from-jira)
