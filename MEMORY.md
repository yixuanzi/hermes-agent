# Project Memory

This file records project-level working memory for this repository.

## Aegis Change-Intent Summary Rule

- Scope: When a task modifies non-test code outside `aisoc/`、`workagent/`、`aegis/`、`aegis_common/`、`plugins/ext-tools/`、`plugins/rbac-guard/`、`plugins/aegis_a2a/`、`plugins/aegis_products/` and `plugins/aegis_source/`., maintain the detailed final change intent summary in `hermes_change_log_by_aegis.md`.
- Exclusions: Test-only changes do not require an entry in `hermes_change_log_by_aegis.md`.
- Recording unit: The summary is organized per file, using the target file as the unit of record.
- Update behavior: If a file already has a relevant `Feature` / `Intent` pair, edit that pair in place to reflect the latest final intent instead of appending a new task log entry.
- Add behavior: Add a new `Feature` / `Intent` pair only when the changed capability, behavior, or responsibility is not already represented for that file.
- Required detail: Each recorded file must explain what capability, behavior, or responsibility changed, and why the final change exists.
- File target: All qualifying updates must be maintained in the repository-root `hermes_change_log_by_aegis.md`.
- Review gate: After code changes for a task are complete, perform a review before deciding whether the task is truly complete or needs more edits.
- Boundary: This rule lives only in this repository `MEMORY.md`. It is not mirrored into `AGENTS.md`.
