# Hermes Change Intent Summary by Aegis

Final summary for recording change intent when a task modifies non-test code
outside `aisoc/`、`workagent/` and `aegis/`.

Maintain this file as the current per-file summary of change intent. Do not
backfill prior historical repository changes, and do not add task-completion log
entries when an existing file block can be updated instead.

## Required Summary Structure

- one or more `## File: \`path\`` blocks
- at least one `Feature` and `Intent` pair inside each file block
- multiple `Feature` / `Intent` pairs are allowed for the same file
- one file block per path; update an existing block in place when that file is
  changed again

## Field Meanings

- `Feature`: The capability, behavior, or responsibility affected by the change.
- `Intent`: Why the final change exists, what constraint should be preserved, or what regression should be avoided.

## Update Rule

When a qualifying task changes a file already listed here, edit the matching
`Feature` / `Intent` pair directly. Add a new pair only for a genuinely new
capability, behavior, or responsibility. Add a new file block only when no block
for that path exists.

## Template

```md
## File: `path/to/file`

Feature: Describe the affected capability, behavior, or responsibility.
Intent: Explain why this change exists and what it must preserve.
```

## Minimal Example

```md
## File: `cli.py`

Feature: Slash-command dispatch behavior.
Intent: Keep command routing predictable while extending an existing command flow without changing unrelated command semantics.
```

## File: `tools/a2a_delegate_tool.py`

Feature: Remote A2A agent discovery and opt-in delegation.
Intent: Provide `a2a_list` and remote-only `a2a_delegate` behind the opt-in `a2a` toolset, use the SDK lazily, return clear unavailable/configuration errors, and avoid growing the default core tool schema.

Feature: Remote A2A foreground loop.
Intent: Keep a single remote A2A session alive across foreground Slack input, reuse context/task state, seed default remote context ids with a timestamp plus a two-digit random suffix to reduce concurrent delegate collisions, stream delegate status, AI deltas, final output, tool calls, and follow-up input events, and return explicit loop exit reasons for `/main`, input closure, timeout, errors, and interruption without injecting synthetic user messages into the main conversation.

Feature: Remote A2A source identity envelope.
Intent: Prefix every remote A2A turn with a compact source envelope rebuilt from the parent Agent's platform, user ID, and display name, place optional context only on the initial turn, and keep this remote identity contract separate from the gateway's Slack channel envelope so channel data is never assumed to be forwarded implicitly.

Feature: Hermes A2A interaction client bridge.
Intent: Read the optional `hermes.interaction.v1` Agent Card extension, parse approval/clarify metadata from both streaming and polling task updates, de-duplicate interaction events, and schedule authenticated responses with task/context/interaction IDs on the session owner loop. Preserve fail-closed behavior when the remote service does not declare or cannot serve the response channel.

Feature: Interaction-aware A2A task deadline.
Intent: Pause the caller-side polling deadline while a supported approval or clarification is pending, restore the remaining deadline after the matching resolved event, and clear stale pending markers when the remote task reaches a terminal state without adding a new interaction event protocol.

Feature: Configurable A2A polling timing.
Intent: Use a 120-second client polling deadline by default and allow process-level overrides through `A2A_POLL_TIMEOUT` and `A2A_POLL_INTERVAL`, while preserving explicit session arguments and leaving HTTP and remote approval/clarify timeouts unchanged.

## File: `toolsets.py`

Feature: Toolset catalog.
Intent: Expose `userenv` and A2A tools behind explicit toolsets so users can opt in without adding schema cost to every Hermes session.

## File: `hermes_cli/tools_config.py`

Feature: Configurable user environment and A2A toolsets.
Intent: Surface `userenv` and `a2a` in the standard `hermes tools` flow while keeping both disabled by default until a user explicitly enables them for a platform, without changing AISOC's dedicated A2A runtime.

## File: `model_tools.py`

Feature: Agent-loop tool ownership.
Intent: Route `a2a_delegate` through the live `AIAgent` instance, like `delegate_task`, because it needs parent runtime state and adapter-bound foreground I/O.

## File: `run_agent.py`

Feature: Remote-only A2A delegate dispatch.
Intent: Centralize `a2a_delegate` dispatch on `AIAgent` so sequential, concurrent, and helper execution paths receive the same parent agent, output adapter, and input factory, while stale model arguments cannot re-enable removed local delegate behavior.

## File: `agent/tool_executor.py`

Feature: Tool execution identity binding.
Intent: Bind per-user env identity around sequential and concurrent tool execution, including the default sequential registry-tool path, so terminal and runtime tools still see the calling platform user when cron jobs clear `HERMES_SESSION_*` and when execution crosses helper or thread boundaries.

Feature: A2A delegate execution.
Intent: Add an agent-loop execution branch and concise spinner handling for `a2a_delegate` without sending it through the generic registry handler.

## File: `agent/agent_runtime_helpers.py`

Feature: Agent-owned tool dispatch.
Intent: Treat `a2a_delegate` as an agent-owned tool and wrap runtime helper execution in the same user env identity binding as the main executor path.

## File: `agent/display.py`

Feature: Tool progress display.
Intent: Show `a2a_delegate` with a compact goal preview so foreground delegation activity is legible without adding verbose UI text.

## File: `plugins/platforms/slack/adapter.py`

Feature: Slack delegate foreground I/O.
Intent: Provide route, input adapter, and output adapter contracts for foreground delegate loops, keyed by Slack session dimensions and dispatched through the adapter event loop.

Feature: Slack delegate stream state.
Intent: Track delegate output per route, edit AI deltas, split long Slack messages, flush before tool-call/user-input segment breaks, avoid duplicating final AI output, and return a fresh blocking input adapter from each runtime factory call so cached agents and concurrent Slack threads do not share foreground input queues.

Feature: Slack foreground message routing.
Intent: Capture follow-up messages for active delegate loops before normal message dispatch so delegate conversations do not leak into the main Slack session, while routing mentioned `/main` and `/exit` commands from the original Slack text so thread context or attachment prefixes do not turn loop-exit commands into remote prompts.

Feature: Slack slash-command source identity.
Intent: Preserve the invoking Slack display name when constructing slash-command events, including legacy `/hermes` free-form turns, so downstream source envelopes retain the same user identity fields as normal Slack messages.

Feature: Slack bot-message source identity fallback.
Intent: Preserve `bot_id` as the sender identity when a Slack `bot_message` event omits `user`, propagate the bot marker through authorization and `SessionSource`, and use event-provided bot names without passing a bot ID to the user lookup API so `<source>` attribution remains available for peer-bot mentions.

Feature: Slack clarify Block Kit prompts.
Intent: Render multi-choice gateway clarify prompts as Slack buttons, resolve authorized button clicks through the shared clarify primitive, preserve the typed-answer fallback for Other/open-ended responses, and enforce the same gateway user authorization boundary used by Slack approval and slash-confirm interactions.

Feature: Slack delegate delta edit throttling.
Intent: Limit edits to the same Slack delegate output message to at most once every three seconds, while preserving immediate first sends and forced flushes at segment boundaries to reduce queued Slack updates.

Feature: Slack delegate approval and clarification interactions.
Intent: Keep remote A2A pending state separate from local gateway approval/clarify state, render delegate Block Kit controls including Other and multi-select, validate channel/thread/user and duplicate-click boundaries, and route button/text responses through the authenticated remote interaction responder rather than a local resolver.

## File: `plugins/platforms/feishu/adapter.py`

Feature: Feishu delegate foreground I/O.
Intent: Provide isolated per-runtime blocking input adapters and adapter-loop output routing for `a2a_delegate(is_loop=true)`, keyed by Feishu chat, topic, and user dimensions so cached agents, different users, and different topics never share a foreground queue.

Feature: Feishu delegate stream state.
Intent: Send the first AI delta immediately, throttle edits of the same Feishu message to at most once every three seconds, force-flush at final/tool/user-input segment boundaries, split oversized output, fall back to appended messages after edit failure, and cancel delayed flushes when a route exits or the adapter stops.

Feature: Feishu foreground routing and clarify cards.
Intent: Route active-loop text and exact mention-stripped `/main` or `/exit` controls before normal message, media, and chat-guard dispatch; render multi-choice clarify prompts as Feishu interactive cards whose payloads retain only IDs/indexes, then resolve only authorized same-chat callbacks through the shared clarify primitives while preserving typed-answer fallback.

Feature: Feishu WebSocket SDK compatibility.
Intent: Preserve Channel signaling with `extra_ua_tags=["channel"]` on current lark-oapi versions while allowing legacy clients that reject that keyword to establish a connection with an explicit group-mention delivery upgrade warning instead of failing adapter startup.

Feature: Feishu source display-name resolution.
Intent: Resolve sender names through Contact v3 using event `open_id` before less reliable ID forms, cache a successful name across all event identity aliases, and keep the Feishu source-envelope `uname` key present with an empty value when lookup is unavailable.

Feature: Feishu delegate approval and clarification cards.
Intent: Render remote A2A approval/clarify interactions with interactive cards, preserve chat/thread/authorization and duplicate-click checks, route choices and Other text through the remote responder, and accept either tenant-scoped callback `operator.user_id` or app-scoped `operator.open_id` when matching the stored source identity so legitimate delegate clicks are not rejected.

## File: `gateway/run.py`

Feature: Delegate runtime binding per gateway turn.
Intent: Bind adapter-provided delegate output and input factories onto both fresh and cached agents each turn, preventing Slack thread/user runtime state from leaking across cached sessions.

Feature: Slack / Feishu source identity envelope for agent-bound messages.
Intent: Prefix Slack and Feishu DM/channel/group messages that become agent-bound turns with compact structured metadata after gateway command handling and all inbound-context assembly, mapping Feishu `chat_id` to the shared `channel` field and keeping the user ID and optional display name on the first line without affecting command parsing or other-platform attribution. This main-Agent envelope is intentionally distinct from the remote A2A envelope, which carries only parent platform/user identity and does not automatically receive the channel.


## File: `tools/user_env_store.py`

Feature: Persistent per-user env storage.
Intent: Store user runtime env values in `$HERMES_HOME/users.env.json` keyed by `platform.user_id`, keep `CURRENT_USER_NAME` system-managed, and migrate a single legacy username-keyed entry safely.

## File: `tools/user_env_runtime.py`

Feature: Runtime user env identity.
Intent: Carry platform/user identity through ContextVars and thread propagation, with a stable local runtime scope key that isolates terminal environments by platform and user ID.

## File: `tools/userenv_tool.py`

Feature: User-scoped env management tool.
Intent: Let authenticated runtime users list, set, and delete only their own env values while never returning secret values and never deleting `CURRENT_USER_NAME`.

## File: `tools/environments/local.py`

Feature: Local subprocess env overlay.
Intent: Overlay the current user's persisted env values into both reusable local shells and one-shot local subprocess spawns without mutating process-global `os.environ`, so terminal execution consistently sees the active runtime user's env.

## File: `tools/environments/base.py`

Feature: Local shell snapshot hygiene.
Intent: Export user env values only for the active command and unset them before saving the reusable shell snapshot so secrets do not persist across users or after deletion.

## File: `tools/terminal_tool.py`

Feature: Local terminal environment cache isolation.
Intent: Use `local::{platform}::{user_id}` as the local backend cache key when a runtime user identity is bound, preserving isolation across messaging users while allowing user-name changes to reuse the same environment.

## File: `tools/delegate_tool.py`

Feature: Delegate subagent user env inheritance.
Intent: Carry parent user identity into classic `delegate_task` children so user-scoped env behavior is consistent between main agents and subagents.

## File: `cron/jobs.py`

Feature: Cron job identity ownership metadata.
Intent: Add normalized `identify` ownership records while preserving legacy `identify=None` jobs as visible public jobs and treating malformed identify data as non-public; keep `id`, `profile`, and `profile_name` immutable while allowing `identify` to be corrected through raw job updates.

## File: `tools/cronjob_tools.py`

Feature: User-visible cron job filtering.
Intent: Scope cron tool create/list/update/pause/resume/remove/run/context references to the current runtime user plus legacy public jobs, preventing one messaging user from operating another user's jobs.

## File: `cron/scheduler.py`

Feature: Cron runtime user identity restoration.
Intent: Restore platform/user identity from `identify` across cron agent runs, prompt prerun scripts, and `no_agent` script execution so scheduled jobs apply the owning user's env even without gateway session vars, and fail malformed identify records explicitly instead of treating them as public. Preserve the legacy one-argument script-runner call for public jobs so existing integrations remain compatible.

## File: `tools/lazy_deps.py`

Feature: Lazy A2A SDK dependency.
Intent: Register `a2a-sdk[fastapi]==1.1.0` as an opt-in lazy dependency so A2A client and FastAPI server surfaces are available on demand without expanding the core install footprint.

## File: `hermes_cli/main.py`

Feature: AISOC and Aegis builtin CLI dispatch.
Intent: Recognize `hermes aisoc` and `hermes aegis` without plugin discovery, register their product-owned parsers, and forward the parsed namespace to each backend while keeping product startup and security policy outside the Hermes core.

## File: `pyproject.toml`

Feature: AISOC/Aegis package and A2A server dependency exposure.
Intent: Ship the two product packages as importable distributions and keep the FastAPI portion of the A2A SDK behind the existing opt-in `a2a` extra rather than adding it to the default installation.

## File: `scripts/a2a_smoke_test.py`

Feature: Official A2A protocol smoke test.
Intent: Exercise Agent Card discovery, single and multi-turn context reuse, polling, streaming, terminal states, and optional bearer authentication through the installed A2A SDK for AISOC verification.
