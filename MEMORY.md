# Project Memory

This file records project-level working memory for this repository.

## Aegis Change-Intent Summary Rule

- Scope: When a task modifies non-test code outside `aisoc/`、`workagent/` and `aegis/`, maintain the detailed final change intent summary in `hermes_change_log_by_aegis.md`.
- Exclusions: Changes inside `aisoc/`,Changes inside `workagent/`, changes inside `aegis/`, and test-only changes do not require an entry in `hermes_change_log_by_aegis.md`.
- Recording unit: The summary is organized per file, using the target file as the unit of record.
- Update behavior: If a file already has a relevant `Feature` / `Intent` pair, edit that pair in place to reflect the latest final intent instead of appending a new task log entry.
- Add behavior: Add a new `Feature` / `Intent` pair only when the changed capability, behavior, or responsibility is not already represented for that file.
- Required detail: Each recorded file must explain what capability, behavior, or responsibility changed, and why the final change exists.
- File target: All qualifying updates must be maintained in the repository-root `hermes_change_log_by_aegis.md`.
- Review gate: After code changes for a task are complete, perform a review before deciding whether the task is truly complete or needs more edits.
- Boundary: This rule lives only in this repository `MEMORY.md`. It is not mirrored into `AGENTS.md`.

## A2A interaction and runtime ownership (2026-08)

- The current `hermes.interaction.v1` A2A service implementation belongs to `workagent/backend/`; 
- Workagent keeps approval and clarification on the existing `TaskState.WORKING` task. `InteractionRegistry` validates task/context/interaction IDs, reuses the existing approval/clarify resolvers, and fails closed on timeout, cancellation, cleanup, unknown IDs, stale responses, or an unavailable response channel.
- `tools/a2a_delegate_tool.py` is the caller-side bridge: it reads the optional Agent Card extension, parses streaming and polling metadata, de-duplicates by interaction ID/event kind, and schedules authenticated responses on the session owner event loop. A remote service that does not declare the extension must not receive fabricated UI controls or automatic approval.
- A2A delegate UI state is separate from ordinary local gateway approval/clarify state. Slack, Feishu, and Aegis delegate controls call the remote responder by interaction ID; ordinary local controls continue using their existing local resolver.
- Feishu callback identity is tenant/app scoped: a stored source user ID may match either callback `operator.user_id` or `operator.open_id`, while chat/thread and operator authorization checks remain mandatory.
- A2A userenv isolation is intentional: Workagent temporarily sets `agent.platform = "{platform}_a2a"` for the execution surface but sets `_user_env_platform = "{platform}"` for identity binding. Terminal/userenv storage therefore remains `platform.user_id`; responding to an interaction resumes the same `context_id` agent turn and does not create a new `_a2a` userenv partition.
- Module documentation: `workagent/backend/docs/a2a-interaction-extension.md`, `aegis/docs/a2a-delegate-interaction.md`, and the cross-module overview `aegis/docs/a2a_delegate_and_slack_io_adapters.md`.
