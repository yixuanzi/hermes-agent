# Hermes Change Log by Ages

Use this file as a file-centric summary of modified Python files when a task
changes non-test `.py` code outside `aisoc/` and `aegis/`.

This file is not a chronological task diary. Keep it organized by file so a
future agent can quickly see which files have changed, which feature points
were touched, and why those changes exist.

Required summary structure:

- one or more `## File: \`path\`` blocks
- at least one `Feature` and `Intent` pair inside each file block
- multiple `Feature` / `Intent` pairs are allowed for the same file

Template:

```md
## File: `path/to/file.py`
- Feature:
  - What changed in this file.
- Intent:
  - Why this change was made.
- Feature:
  - Another change point in the same file.
- Intent:
  - Why that change point exists.

## File: `another/file.py`
- Feature:
  - Another file-level summary point.
- Intent:
  - Intent for that file-level point.
```

## File: `agent/agent_runtime_helpers.py`
- Feature:
  - Bind the current runtime user identity around helper-level tool dispatch and
    honor `agent._user_env_platform` when cron or other non-messaging contexts
    need to execute as the original messaging user.
- Intent:
  - Make delegated, background, and cron-triggered tool calls resolve the same
    user-scoped env partition as the originating user instead of falling back
    to anonymous or `platform="cron"` execution.
- Feature:
  - Switch the agent-loop dispatch branch from the historical
    `delegate_ext` name to `a2a_delegate`.
- Intent:
  - Keep the agent-level interception path aligned with the renamed
    agent-to-agent delegation tool so the runtime can inject session-scoped I/O
    adapters before the tool executes.

## File: `agent/display.py`
- Feature:
  - Add `a2a_delegate`-specific preview and spinner/cute-message rendering.
- Intent:
  - Give CLI/TUI users a readable summary for delegated A2A work instead of
    showing it as an opaque generic tool call while the child session is
    running in the foreground.

## File: `agent/tool_executor.py`
- Feature:
  - Special-case `a2a_delegate` in the sequential executor, including its own
    spinner label and dispatch call to `agent._dispatch_a2a_delegate(...)`.
- Intent:
  - Preserve the richer interactive UX and runtime wiring that delegated
    foreground sessions need, instead of sending them through the plain registry
    dispatch path.
- Feature:
  - Wrap sequential and concurrent registry-dispatched tools in
    `bind_current_user_env_identity(...)`.
- Intent:
  - Ensure every tool call, including delegated/background work and cron runs,
    sees the correct per-user env identity during execution.

## File: `cron/jobs.py`
- Feature:
  - Introduce normalized `identify` ownership records plus parsing and
    visibility helpers for `platform + user_id`.
- Intent:
  - Persist who owns a cron job so later listing, update, and execution paths
    can distinguish private user-owned jobs from legacy/public ones.
- Feature:
  - Normalize and validate stored `identify` payloads during job read/write
    flows.
- Intent:
  - Prevent malformed or drifting cron ownership metadata from silently
    broadening access or breaking later runtime identity restoration.

## File: `cron/scheduler.py`
- Feature:
  - Parse `job["identify"]`, fail malformed ownership records, and inject the
    recovered `user_id`, `user_name`, and `_user_env_platform` into the cron
    `AIAgent`.
- Intent:
  - Let scheduler-time tools load the original messaging user's env partition
    while still keeping `agent.platform == "cron"` for cron-specific behavior
    elsewhere in Hermes.

## File: `gateway/platforms/slack.py`
- Feature:
  - Add delegate foreground route state, Slack input/output adapters, and
    session-key-aware route matching based on channel, thread, user, and chat
    type.
- Intent:
  - Turn a Slack thread into a frontstage delegated conversation channel so
    follow-up user messages go to the active child session instead of spawning
    a fresh main-agent turn.
- Feature:
  - Translate delegated output events into Slack-friendly streaming behavior,
    including delta buffering, segment breaks, session tracking, fallback send
    paths, and idle/close cleanup.
- Intent:
  - Keep delegated streaming output readable and resilient in Slack while
    avoiding duplicate finals, interleaved user replies, and stale foreground
    route state.

## File: `gateway/run.py`
- Feature:
  - Add per-turn binding/clearing for delegated foreground runtime adapters and
    wire Slack thread metadata into the current agent before the turn runs.
- Intent:
  - Keep the `a2a_delegate` tool UI-agnostic by letting the gateway inject the
    correct Slack-scoped input/output adapters at runtime rather than hardcode
    Slack logic into the tool layer.
- Feature:
  - Preserve route fidelity for Slack delegated foreground turns as the gateway
    builds session context for each event.
- Intent:
  - Ensure delegated output and subsequent user input stay attached to the
    correct Slack thread and user lane during concurrent gateway processing.

## File: `hermes_cli/main.py`
- Feature:
  - Add `hermes aisoc` and `hermes aegis` subcommands to the main Hermes CLI
    and delegate parser/startup ownership to `aisoc.backend.main` and
    `aegis.backend.main`.
- Intent:
  - Expose AISOC and Aegis as first-class Hermes entrypoints while keeping the
    product-specific argument surface and launch behavior centralized in their
    own backend entry modules.
- Feature:
  - Move AISOC parser/options out of the giant core CLI file and into the AISOC
    backend-owned `configure_aisoc_parser(...)` path.
- Intent:
  - Avoid duplicated option definitions in Hermes core so later AISOC
    parameters such as `--module` and `-p/--profile` can evolve in one place.

## File: `model_tools.py`
- Feature:
  - Reclassify `a2a_delegate` as an agent-loop tool after the rename from
    `delegate_ext`.
- Intent:
  - Keep delegation on the agent-controlled dispatch path because it depends on
    live agent state and runtime adapters that generic registry dispatch cannot
    reconstruct.

## File: `run_agent.py`
- Feature:
  - Add `AIAgent._dispatch_a2a_delegate(...)` as the single call site for the
    renamed delegation tool and forward the runtime output adapter,
    `session_id`, and remote/local delegation arguments into the tool module.
- Intent:
  - Centralize how Hermes turns a model-issued `a2a_delegate` call into a
    real delegated execution so later caller surfaces share the same contract.
- Feature:
  - Resolve the delegated input adapter lazily and only when loop mode is
    active.
- Intent:
  - Avoid prematurely claiming foreground input resources for one-shot
    delegation calls while still allowing loop-mode sessions to enter a
    sustained interactive child conversation.

## File: `tools/a2a_delegate_tool.py`
- Feature:
  - Consolidate the agent-to-agent delegation implementation into the current
    `a2a_delegate` module, covering remote registry discovery, agent-card
    capability summarization, local child-agent execution, remote A2A session
    execution, and the unified tool schema.
- Intent:
  - Provide one reusable delegation contract that can be called from Hermes
    core, AISOC, Aegis, and Slack without each surface needing its own
    execution engine.
- Feature:
  - Add delegated foreground-loop support, runtime input/output protocol hooks,
    session reuse, cancellation handles, idle auto-close behavior, and Slack-
    compatible output event shaping.
- Intent:
  - Let delegated sessions continue in the same frontstage conversation with
    real follow-up input and streaming output instead of being limited to a
    single fire-and-forget tool result.
- Feature:
  - Evolve the schema and runtime contract from the old `delegate_ext`
    parameter set to the current `a2a_delegate` naming and `session_id` /
    identify-aware behavior.
- Intent:
  - Keep the tool callable from newer AISOC/Aegis entrypoints and Slack routes
    while preserving enough compatibility for earlier delegation flows.

## File: `tools/cronjob_tools.py`
- Feature:
  - Capture the current runtime user's identity into newly created jobs and
    filter list/update/remove/run visibility through `_visible_cron_jobs(...)`.
- Intent:
  - Prevent users from reading or mutating other users' private cron jobs while
    keeping legacy public jobs visible for compatibility.
- Feature:
  - Restrict `context_from` and direct job references to the caller-visible job
    set.
- Intent:
  - Close cross-user reference paths that would otherwise let one user's cron
    job implicitly consume or mutate another user's scheduled context.

## File: `tools/delegate_tool.py`
- Feature:
  - Pass the parent agent's `platform`, `user_id`, and `user_name` into local
    subagents when building delegated child agents.
- Intent:
  - Make classic `delegate_task` children inherit the caller's runtime identity
    so user-scoped env injection still works when work fans out to subagents.

## File: `tools/environments/base.py`
- Feature:
  - Inject ContextVar-backed session vars and current user env values into the
    subprocess environment assembled by `_make_run_env(...)`.
- Intent:
  - Bridge Hermes' in-process session/user identity into child processes,
    because `ContextVar` state does not automatically cross the subprocess
    boundary.

## File: `tools/environments/local.py`
- Feature:
  - Add current user env injection to local foreground/background process
    startup and per-profile HOME isolation for subprocesses.
- Intent:
  - Ensure local commands receive the right caller-scoped env values even when
    Hermes reuses the same local backend machinery across turns.
- Feature:
  - Overlay user env variables into the reusable shell snapshot before each
    command and explicitly unset them again before snapshot persistence.
- Intent:
  - Stop one user's exported variables from leaking into another user's later
    local-shell reuse while still letting same-user updates and deletions take
    effect immediately.

## File: `tools/terminal_tool.py`
- Feature:
  - Change local environment reuse from the global `"default"` bucket to the
    runtime `identity.runtime_scope_key` when a user-scoped identity exists.
- Intent:
  - Isolate local terminal state per `platform + user_id` without changing the
    existing reuse semantics for Docker/SSH/benchmark override environments.

## File: `tools/user_env_runtime.py`
- Feature:
  - Introduce `UserEnvIdentity`, `bind_current_user_env_identity(...)`, and the
    `runtime_scope_key` abstraction for `local::{platform}::{user_id}`.
- Intent:
  - Give Hermes one runtime identity carrier that can be reused by tool
    execution, local-terminal partitioning, and subprocess env injection.

## File: `tools/user_env_store.py`
- Feature:
  - Normalize persistent user env storage to the canonical `platform.user_id`
    key and store the mutable display name as reserved `CURRENT_USER_NAME`
    inside the value payload.
- Intent:
  - Decouple env partitioning from mutable usernames so renames no longer split
    a user's env history or terminal isolation bucket.
- Feature:
  - Add legacy-key migration from `platform.user_id.user_name` and keep
    `CURRENT_USER_NAME` refreshed during load/set/delete flows.
- Intent:
  - Preserve backward compatibility with older stores while moving the runtime
    toward stable user-id-based binding.

## File: `tools/userenv_tool.py`
- Feature:
  - Scope `list`, `set`, and `delete` to the currently bound runtime user and
    hide the reserved `CURRENT_USER_NAME` field from normal listings/counts.
- Intent:
  - Expose a clean "manage only my env" user experience while reserving the
    system-maintained identity fields needed for runtime injection.

## File: `toolsets.py`
- Feature:
  - Expose `a2a_list` and `a2a_delegate` through Hermes core/delegation/a2a
    toolset definitions.
- Intent:
  - Make agent-to-agent delegation discoverable and selectable from standard
    Hermes agents plus AISOC/Aegis-specific runtime profiles without custom
    hardcoding in each caller.
