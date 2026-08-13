# Phase 1: Client & Environment

## Purpose

Establish the operating context for the migration. Every later phase depends on
the correct client profile, credentials, per-client SQLite database, and Jira
environment being active.

## What Happens in This Phase

The CLI shows the active client and Jira environment in its session header. Profile
selection also selects the database used by scripts launched during the session.
The Jira environment toggle applies to every subsequent Jira command; individual
menu actions do not ask again.

This phase contains CLI-native operations rather than standalone scripts:

- **Switch active client profile** selects an existing `profiles/<slug>.env`, writes
  the `.asana_jira_active` marker, and activates that profile's database.
- **Create a new client profile** collects the client name, Asana workspace and
  credentials, optional Jira settings, and SSL preference. It validates available
  credentials before saving.
- **Toggle Jira environment** switches the session between Sandbox and Production.
- **View or update credentials** edits the active profile. Secret tokens use macOS
  Keychain storage when available and otherwise fall back to the profile file.

## Expected Result

- The intended client name is shown in the CLI header.
- The correct per-client database path is active.
- Asana credentials are valid.
- Jira credentials and URLs are configured if later phases will use Jira.
- The header clearly shows Sandbox or Production as intended.

## Before Moving On

Do not begin discovery or migration if the client or Jira environment is uncertain.
Production selection matters only to Jira operations, but selecting the wrong client
can direct every sync and report to the wrong database.

## Related Documentation

- [Requirements and profile configuration](../../README.md#requirements)
- [Interactive Orchestrator](../../README.md#interactive-orchestrator)
- [Sensitive Local Data](../../README.md#sensitive-local-data)
