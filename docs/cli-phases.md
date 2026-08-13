# Interactive CLI Phases

The interactive CLI organizes the Asana-to-Jira migration into seven project
management phases. Use this page as the lifecycle overview, then open the phase
guide for its intended outcomes, menu actions, scripts, artifacts, and completion
criteria.

Launch the CLI with:

```bash
python3 -m scripts.interactive_cli --log-level INFO
```

The active client profile and Jira environment appear above the main menu. Project
and team scope is selected for each operation rather than stored for the session.

## 1. Client & Environment

Select the client, configure access, and confirm whether Jira commands target
Sandbox or Production before doing migration work.

See [Client & Environment](phases/01-client-environment.md).

## 2. Discover & Refresh Asana

Build and refresh the local Asana snapshot in dependency order: metadata, tasks,
and then task-derived resources.

See [Discover & Refresh Asana](phases/02-discover-refresh-asana.md).

## 3. Assess & Plan Migration

Produce stakeholder review material and make project, field, section, membership,
and migration-scope decisions.

See [Assess & Plan Migration](phases/03-assess-plan-migration.md).

## 4. Prepare Jira

Establish user mappings, project associations, Jira hierarchy, and project-level
fields needed before task migration.

See [Prepare Jira](phases/04-prepare-jira.md).

## 5. Migrate Projects

Execute task migration waves through the selected CSV or direct-API path, restore
Jira mappings, and transfer attachments.

See [Migrate Projects](phases/05-migrate-projects.md).

## 6. Maintain & Validate

Refresh changing data, inspect readiness, reconcile mappings, and validate the
state of migrated work.

See [Maintain & Validate](phases/06-maintain-validate.md).

## 7. Advanced & Recovery

Perform deliberate state resets, credential migration, and exceptional recovery
operations outside the normal migration flow.

See [Advanced & Recovery](phases/07-advanced-recovery.md).

## Existing Command References

The phase guides intentionally overlap these existing script-oriented references
while the lifecycle documentation is evaluated:

- [Core Sync Commands](sync-commands.md)
- [Migration Reports](reports.md)
- [Atlassian / Jira Integration](jira-integration.md)
- [Unmapped Scripts Reference](../UNMAPPED_SCRIPTS.md)
