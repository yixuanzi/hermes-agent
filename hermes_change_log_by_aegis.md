# Hermes Change Intent Summary by Aegis

Final summary for recording change intent when a task modifies non-test code
outside `aisoc/`、`workagent/`、`aegis/`、`plugins/ext-tools/` and `plugins/rbac-guard/`.

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

Feature: Opt-in remote A2A delegation.
Intent: Provide `aegis_a2a_list` and remote-only `aegis_a2a_delegate` behind the opt-in `aegis_a2a` toolset, load the SDK lazily, return clear unavailable/configuration errors, and keep the default core tool schema from growing. Stale model arguments must not re-enable removed local delegate behavior. The resolved `agent_name` rides on the delegate session purely for platform presentation; it must never participate in routing, session identity, or authorization.

Feature: Foreground loop and source identity envelope.
Intent: Keep one remote A2A session alive across foreground input (reuse context/task state; default context ids are a timestamp plus a two-digit random suffix to reduce collisions), stream status, AI deltas, tool calls, final output and follow-up input events, and return explicit exit reasons for `/main`, input closure, timeout, errors and interruption without injecting synthetic user messages into the main conversation. Every remote turn is prefixed with a compact source envelope rebuilt from the parent Agent's platform, user ID and display name (optional context only on the initial turn); this stays separate from the gateway's channel envelope so channel data is never assumed to be forwarded.

Feature: `hermes.interaction.v1` client bridge and interaction-only output.
Intent: Read the optional Agent Card extension, parse approval/clarify metadata from streaming and polling updates, de-duplicate events, and send authenticated responses with task/context/interaction IDs on the session owner loop; fail closed when the remote does not declare or cannot serve the response channel. When `is_delegate_output` is false, keep the output adapter bound and mark the session interaction-only so every event except remote `approval_request`/`clarify_request` and their `_resolved` events is filtered at one gate — otherwise a silent delegation swallows the approval card and the remote approval can only time out.

Feature: Polling deadline, heartbeat and stream fallback.
Intent: Default to a 120-second polling deadline (`A2A_POLL_TIMEOUT` / `A2A_POLL_INTERVAL` override it; explicit session arguments win; HTTP and remote approval timeouts are unchanged). Pause the deadline while a supported approval/clarify is pending, restore it after the matching resolved event, and clear stale markers on terminal states. Refresh the parent Agent's activity timestamp on every SSE event and successful `get_task` poll so the gateway inactivity timeout does not kill a turn whose delegate is still reporting. If the SSE stream raises after this turn's task ID is known, hand the last snapshot to polling instead of failing; errors before any task is known, and stream errors after a user stop, still propagate.

Feature: Mid-turn steering.
Intent: Read the optional `steer_path` and expose `schedule_steer(text)`, which posts guidance to the remote task of the turn in flight and resolves to whether the remote accepted it. The turn's own task ID is tracked separately from `task_id` so a loop never steers a finished task; it returns `None` when the remote does not declare steering or no turn is running, so callers keep the next-turn queue. In loop mode the session is bound to the foreground input adapter so platform adapters can reach it. (Native A2A messages cannot do this: the SDK serializes requests per task.)

Feature: Stopping a delegation.
Intent: The active-delegate cancel handle stops a delegation in either state: a running turn gets an A2A `cancel_task` for that turn's task; a loop idle between turns closes its input adapter instead, and returns the interrupted payload rather than waiting out the 25-minute input timeout. `has_live_task` reports either case. `cancel_active_a2a_delegate(parent_agent)` gives callers that hold only the agent (the gateway `/stop` path) the same stop; it ignores non-delegation handles and never raises.

Feature: Agent-loop dispatch entry.
Intent: `dispatch_for_agent(agent, args)` is the one call site `AIAgent._dispatch_a2a_delegate` forwards to, so the sequential, concurrent and helper paths all hand the delegate the same parent agent and the gateway-bound `_delegate_ext_output_adapter` / `_delegate_ext_input_factory`.

## File: `toolsets.py`

Feature: Opt-in `userenv` and `aegis_a2a` toolsets.
Intent: Expose the user-env tool and the Aegis A2A tools behind explicit toolsets so users opt in without adding schema cost to every session. The `aegis_` names keep them apart from upstream's `a2a` platform-plugin toolset, whose `a2a_list` the fork's tool used to shadow.

Feature: Fork names stay out of shared metrics.
Intent: `BUILTIN_TOOL_NAMES` / `BUILTIN_TOOLSET_NAMES` are the names shared metrics may export, and upstream's `hermes.shared_metrics.v4` schema enumerates only upstream's. `_FORK_TOOLSET_NAMES` keeps the fork's toolsets and their tools out, so they record as `custom` / `plugin` and an opted-in upload never carries a value the schema rejects.

## File: `hermes_cli/tools_config.py`

Feature: Configurable user environment and A2A toolsets.
Intent: Surface `userenv` and `aegis_a2a` in the standard `hermes tools` flow, off by default until enabled per platform and never part of the core set, without changing AISOC's dedicated A2A runtime.

## File: `model_tools.py`

Feature: Agent-loop tool ownership.
Intent: List `aegis_a2a_delegate` in `_AGENT_LOOP_TOOLS` so registry dispatch never runs it directly: like `delegate_task` it needs the live `AIAgent`'s runtime state and adapter-bound foreground I/O.

## File: `run_agent.py`

Feature: Remote-only A2A delegate dispatch.
Intent: `AIAgent._dispatch_a2a_delegate` is a one-line forward to `tools.a2a_delegate_tool.dispatch_for_agent`, so the facade stays thin while sequential, concurrent and helper execution share one dispatch.

Feature: Host runtime reported during owner sessions.
Intent: `_current_main_runtime` reports the host `api_mode` while an owner-attached codex session runs (see `agent/codex_owner_runtime.py`), so auxiliary routing follows the provider the user configured.

## File: `agent/tool_executor.py`

Feature: Tool-execution identity binding.
Intent: Every tool call reaches its executor through `_dispatch_authorized_once`, which binds the agent's platform user (`call_as_agent_user`) for the call. One binding point covers the sequential, concurrent and helper paths, so terminal and runtime tools see the calling user even when cron cleared `HERMES_SESSION_*` or execution crossed threads.

Feature: A2A delegate sequential handling.
Intent: `aegis_a2a_delegate` spins in quiet mode, stops the spinner on failure, and is exempt from the sequential deadline like `delegate_task`, because a foreground loop blocks by design.

## File: `agent/agent_runtime_helpers.py`

Feature: Agent-owned tool dispatch.
Intent: Count `aegis_a2a_delegate` with `delegate_task` among the agent-owned tools so helper execution routes it to the agent instead of the registry stub. Identity binding lives in `agent/tool_executor.py`'s single dispatch entry, so this helper no longer wraps it.

## File: `agent/inline_tool_executors.py`

Feature: Inline executor for the A2A delegate.
Intent: One `aegis_a2a_delegate` entry routes both the sequential and concurrent paths to `agent._dispatch_a2a_delegate`.

## File: `agent/display.py`

Feature: Tool progress display.
Intent: Preview `aegis_a2a_delegate` by its goal and give it a compact 🤝 row that reuses the localized `delegate` verb, so foreground delegation stays legible without new locale keys.

## File: `plugins/platforms/slack/adapter.py`

Feature: Delegate mixin wiring.
Intent: The adapter only composes `SlackDelegateMixin` and calls `_route_inbound_to_delegate` before `handle_message`; the delegate behaviour lives in `adapter_aegis_delegate.py` so the over-cap adapter barely grows.

## File: `plugins/platforms/slack/adapter_aegis_delegate.py`

Feature: Slack delegate foreground I/O and stream state.
Intent: Provide the route, input-adapter and output-adapter contracts for foreground delegate loops, keyed by Slack session dimensions and dispatched on the adapter event loop; every runtime-factory call returns a fresh blocking input adapter so cached agents and concurrent threads never share a queue. Track output per route: edit AI deltas (at most once per three seconds; first send and forced flushes at segment boundaries are immediate), split long messages, flush before tool-call/user-input breaks, and never duplicate final AI output.

Feature: Slack foreground routing and steering.
Intent: Capture follow-ups for active delegate loops before normal dispatch so they do not leak into the main session. `/main` and `/exit` are recognized from the original Slack text (thread-context or attachment prefixes must not turn them into remote prompts); every other resolvable gateway command (`/stop`, `/new`, …) skips the loop and is handled by the gateway. While the remote turn is running, a non-command follow-up is folded into it through the loop input's bound A2A session (`bind_a2a_session` / `schedule_steer`) and acknowledged, with no interrupt and no new turn; the stream segment breaks first so the acknowledgement lands after the streamed text. Slash text, remotes without steering, and refused/failed steers keep the next-turn queue, so input is never dropped.

Feature: Slack delegate approval and clarification interactions.
Intent: Keep remote A2A pending state separate from local gateway approval/clarify state; render delegate Block Kit controls (including Other and multi-select), validate channel/thread/user and duplicate-click boundaries, and route button/text responses through the authenticated remote responder, not a local resolver. Delegate controls carry `a2a_delegate|` action values routed by `_route_delegate_approval_action` / `_route_delegate_clarify_action` and `hermes_clarify_submit`. Local gateway clarify buttons ship in v0.4 and are not duplicated.

Feature: Slack source identity.
Intent: A `bot_message` event without `user` keeps `bot_id` as the sender identity, propagates the bot marker through authorization and `SessionSource`, and uses the event-provided bot name without calling the user lookup API; a failed bot probe never caches the raw id as a display name. `SlackResponse` unwrapping and slash-command display names ship in v0.4.

## File: `plugins/platforms/feishu/adapter.py`

Feature: Fork mixin wiring.
Intent: The adapter composes `FeishuAegisMixin` and only calls its seams: `_jev_gate` after `_admit`, `_aegis_inbound` between `build_source` and dispatch, `_auto_thread_route` / `_send_in_pending_topic` inside `_feishu_send_with_retry`, and the card-mode hooks in `send`, `edit_message` and `delete_message`. Fork behaviour lives in the `adapter_aegis*.py` siblings so the over-cap adapter barely grows.

## File: `plugins/platforms/feishu/adapter_aegis.py`

Feature: Feishu fork composition and settings.
Intent: `FeishuAegisMixin` composes the fork siblings and adds `hermes_delegate_kind` to `_CARD_ACTION_ROUTES`; `_init_aegis_state` and `load_aegis_settings` (`AegisFeishuSettings`, `config.yaml` winning over env) keep fork state and settings out of upstream's dataclass. `_aegis_inbound` orders the inbound steps: Jev admission, topic keying, delegate routing, history metadata.

## File: `plugins/platforms/feishu/adapter_aegis_delegate.py`

Feature: Feishu delegate foreground I/O, routing and steering.
Intent: Provide isolated per-runtime blocking input adapters and adapter-loop output routing for `aegis_a2a_delegate(is_loop=true)`, keyed by chat, topic and user so cached agents, users and topics never share a queue. Route active-loop text and exact mention-stripped `/main` / `/exit` before normal message, media and chat-guard dispatch; every other resolvable gateway command skips the loop (and its interaction answers) and reaches normal dispatch. While the remote turn is running, a non-command follow-up is folded into it through the bound session's `schedule_steer` (via `DelegateLoopAdapterMixin._steer_delegate_turn`) and acknowledged, with no interrupt and no new turn; slash text, remotes without steering, and refused/failed steers keep the next-turn queue.

Feature: Feishu delegate stream state.
Intent: Send the first AI delta immediately, throttle edits of the same message to once per three seconds, force-flush at final/tool/user-input boundaries, split oversized output, fall back to appended messages after an edit failure, and cancel delayed flushes when a route exits or the adapter stops. The flush is split into throttle, send-group and edit-or-resend helpers to stay under the complexity cap.

Feature: Feishu delegate approval and clarification cards.
Intent: Render remote A2A approval/clarify interactions as interactive cards (payloads keep only IDs/indexes), preserve chat/thread/authorization and duplicate-click checks, route choices and Other text through the remote responder, and accept either tenant-scoped `operator.user_id` or app-scoped `operator.open_id` when matching the stored source identity. Local multi-choice gateway clarify cards ship in v0.4 (`adapter_choice_cards`) and are not duplicated. The typed-text interaction router runs ahead of the foreground router on every inbound message, so it must tolerate an adapter built without `__init__` (no pending state means pass through to normal dispatch, not raise).

## File: `plugins/platforms/feishu/adapter_aegis_topics.py`

Feature: Automatic topic creation and root-keyed session routing.
Intent: With `FEISHU_REPLY_THREAD` (default `true`), treat a top-level message's `om_*` ID in regular groups and DMs as the prospective topic root and reply anchor, create the topic with `reply_in_thread=true`, and carry the root metadata through final, progress, approval, clarification, media and error delivery. A main-timeline quote reply (`root_id`, no `thread_id`) is keyed on its OWN message id, not the quoted one — Feishu roots the topic at the reply, so keying on the quoted root splits one conversation in two and sends approval cards to a second topic (the quoted text still reaches the model as reply context); with the switch off and no Jev-forced topic, it follows v0.4 (the reply stays flat, #20548). A later `omt_* + root_id` message reuses the root-keyed session only when that root has active or persisted routing (`_auto_thread_root_session`, the single spelling of the rule); forum and manually created topics keep their real-thread identity. Once an automatic root session was ended by `session_reset` or pruned after restart, a later topic message may start a new session on the real `omt_*` route; AIAgent cache eviction is independent of session continuity, and this lifecycle is independent of the `FEISHU_REPLY_THREAD` switch.

Feature: Topic-creation failure handling.
Intent: At most one flat-message fallback, and only for failures that rule a topic out: root withdrawn or missing (230011/231003), group without topic replies (230071), aggregated-message root (230072). Any other failure (rate limit, card validation, internal error 2200 — seen on a reply Feishu had actually bound) says nothing about topics, so `_auto_thread_failure_action` first asks Feishu whether the root now has a `thread_id` (marking the topic established) and retries the same send in the topic inside `_feishu_send_with_retry` (not in the caller: `send()` does not stop at a failed chunk, so a mid-message failure would be lost); only a second in-topic failure with still no topic sends the flat message. A payload carrying a CardKit `card_id` is never re-sent flat (an entity binds to exactly one message); the card layer owns that retry. Failure logs carry `code`, `msg`, `log_id` and the `reply_to` in use.

## File: `plugins/platforms/feishu/adapter_aegis_admission.py`

Feature: Feishu WebSocket SDK compatibility and source display name.
Intent: Pass `extra_ua_tags=["channel"]` on current lark-oapi, but let legacy clients that reject the keyword connect with an explicit group-mention delivery warning instead of failing startup. Resolve sender names through Contact v3 using event `open_id` first, cache a successful name across all identity aliases, and keep the source-envelope `uname` key present (empty) when lookup is unavailable. `build_feishu_ws_client` is a module function.

Feature: Jev channel-admission gate for unmentioned group messages.
Intent: Judge any unmentioned human group message — whether the mention gate was about to drop it (`group_mention_missing`, distinct from `group_policy_rejected`) or the group admits unaddressed messages (`require_mention: false`, the setting that most needs a scope filter). The drop is deferred until text is extracted; bot senders (bot-to-bot loops) and slash commands (another bot's `/reset` must never reach this agent) skip the gate; the feature flag is read before `_mentions_self` so a disabled gate costs nothing. It fails closed in every direction (feature off, no API key, no agent description, undecided verdict, raising classifier). An admitted message forces a topic reply on its own `om_*` root even with `FEISHU_REPLY_THREAD` off, and the event is marked `jev_channel_autoreply`. `_jev_gate` runs after `_admit` and tells an allowed-but-unmentioned sender apart from a policy rejection.

Feature: Master switch and sub-switches for unaddressed-group-message admission.
Intent: `HERMES_JEV_AUTOREPLY` (default off) is checked before the sub-switches, so off means no admission stage at all (Jev is never called; sub-switches are inert, never partially honoured). On, an unaddressed group message is answered ONLY on an explicit allow — denial, transport error, undecided verdict, uncovered category, missing key or empty agent description all end in silence; sub-switches only narrow which messages are eligible. `channel_admission_mode()` resolves the three-way outcome (judge / silence / normal) once so callers cannot re-derive it wrongly and fail open (which matters under `require_mention: false`). DMs, @-mentions, bot senders and slash commands keep their carve-outs. `HERMES_JEV_THREAD_AUTOREPLY` (default off) scopes admission away from existing topics (a message with `thread_id` or `root_id`; a topic's first message is always judged): off, a thread message goes to the normal mention gate, so the flag only narrows. Scope is the gateway; the WORKAGENT A2A service keeps its own rule (anything short of an explicit "no" is answered) because a caller blocks on its reply. `agent/jev_policy.py` resolves the switches; this module applies them.

Feature: A busy topic is never interrupted by an unprompted reply.
Intent: With `HERMES_JEV_THREAD_AUTOREPLY` on, an unaddressed message inside a topic is dropped outright when a turn is already running in that topic's session, before Jev is consulted (an @-mentioned follow-up is still queued by `handle_message`). It is scoped to messages carrying a live `thread_id` — top-level messages and main-timeline quote replies open a new topic. It must resolve the SAME session identity dispatch will settle on, so `build_source` runs above the gate and `_auto_thread_root_session` is shared; the probe restores `source.thread_id`, treats a not-yet-existing session as idle, and any failure in the check falls through to the Jev verdict.

Feature: Recent group history injection.
Intent: Behind opt-in `FEISHU_GROUP_HISTORY` (default false; yaml `platforms.feishu.extra.group_history_*` wins), give the agent the conversation it is pulled into when it has no context yet. The adapter owns only the gate and wiring: a non-`p2p`, non-command message whose resolved `source` has no active or persisted session (`_session_exists_for_source`, checked after the auto-thread block fixes identity — event thread flags cannot be trusted because Feishu stamps `thread_id` onto quote-reply chains) snapshots the `group_history_*` settings, picks the backend from `FEISHU_GROUP_HISTORY_SOURCE` (`api` default, or `cli`), and delegates to `tools.feishu_group_history.build_group_history_block`; the resulting block is stored on `MessageEvent.metadata["group_history_block"]` and never touches `text`. Chat-level lists exclude topic replies, so a first-turn message inside a real `omt_*` topic also gets a `<thread_messages>` block (`FEISHU_GROUP_HISTORY_THREAD_LIMIT`). Text batching keeps the first event's metadata, so a turn carries one block. Failures log and degrade to no block; the feature never blocks a turn.

## File: `plugins/platforms/feishu/adapter_aegis_cards.py`

Feature: Card output mode.
Intent: Behind opt-in `FEISHU_CARD_OUTPUT` (default false; yaml `feishu.card_output` wins), intercept `send`/`edit_message`/`delete_message` so one agent turn renders as one CardKit card, bracketed by `on_processing_start` / `on_processing_complete`. Outbound metadata decides placement: `hermes_progress` goes to the collapsible panel, anything else is the reply body. Only output produced inside a turn becomes a card (a slash-command reply or cron push never creates an empty-panel card), and any CardKit failure returns the route to the text/post path rather than costing a reply. The synthetic `hermes-card:` handle returned by card-mode sends is never used as a reply target (the stream consumer chains overflow chunks by the returned id, and the reply API rejects it with 99992402); `_feishu_send_with_retry` drops it and lets thread metadata place the message. Hooked through v0.4's `send_chunks`.

Feature: Delegate card rendering and transient notices.
Intent: Render each delegated remote agent into its own card keyed by A2A context ID, sealed on that delegate's turn-final event, so a foreground loop yields one card per exchange. Accumulate streamed deltas synchronously and serialize the open-or-extend decision per owner (delegate events are independent fire-and-forget tasks), track whether an exchange streamed separately from the current text segment so the final event cannot re-append text already shown, drop transport-level `status` events in card mode, and keep `error` visible. Liveness signals and acknowledgements stay out of the answer card: `hermes_card_bypass` or the gateway's `non_conversational` marker route a send to plain text/post (`hermes_progress` still wins), the delegate resolved-acks and expired-approval correction set it at their call sites, and a bypassed notice keeps a real message ID so its edit/delete paths work.

## File: `plugins/platforms/feishu/adapter_aegis_card_media.py`

Feature: Card-mode and in-topic attachment delivery.
Intent: A file is never card content, and card mode must not change which files get delivered. Text entry points that skip the gateway's attachment stripping (mid-turn commentary, delegate output) are extracted here with the gateway's own `extract_media` / `extract_local_files` and filters (so `MEDIA:` tags and bare deliverable paths behave as with card output off, and a path in a code fence ships nothing); each file leaves as its own message, the card keeps a one-line receipt, and a per-turn ledger (claimed before upload, released on failure, inert outside a card turn) prevents double delivery on retried sends. Inside a topic, `receive_id_type=thread_id` is rejected for every attachment kind with a bare validation code, so `_send_attachment_message` re-anchors through the reply API on a message in the topic and only then posts flat; other failure codes stay visible and unretried. The in-topic re-anchoring sits in `_send_uploaded_key` and replaces v0.4's audio-only 99992402 retry.

## File: `tools/feishu_group_history.py`

Feature: Group / topic history fetch and rendering, two backends.
Intent: Keep everything below the adapter's gate in one adapter-independent module so the history block has one shape regardless of backend. `GroupHistorySettings` carries the `FEISHU_GROUP_HISTORY_*` knobs (window hours default 24, newest `LIMIT` default 20 capped at the page size 50, topic limit, per-message cap `MSG_MAX_CHARS` default 1000 with 0 = off); `render_history_block` does selection (skip deleted/excluded/empty, newest N, chronological), labelling (`MM-DD HH:MM`, `[reply]`/`[in-topic]` only in the chat listing, `[assistant]` when `sender.id == app_id`, `[bot] name` for peer apps, display names for humans), neutralization via `neutralize_untrusted_inline_text`, and the cap; `build_group_history_block` composes `<group_messages>` plus the de-duplicated `<thread_messages>` block and logs items-returned vs lines-injected.
`ApiHistorySource` calls `im/v1/messages` (`container_id_type=chat|thread`, `ByCreateTimeDesc`, `card_msg_content_type=raw_card_content` — without it a CardKit card is a title-only stub) through the adapter's SDK client. `LarkCliHistorySource` runs `lark-cli im +chat-messages-list` / `+threads-messages-list` (`--as bot --order desc --no-reactions --format json`), accepts only `ok == true` envelopes, uses the CLI's rendered `content`, `sender.name` and `create_time`, ignores inline `thread_replies`, resolves a bare binary name through `hermes_platform.resolver.locate_command` (a configured path is used as given), and treats a missing binary, non-zero exit, 30s timeout or non-JSON output as no history. `normalize_history_source` accepts `api`/`cli` (aliases `lark-cli`, `feishu-cli`) and falls back to `api` with a warning, so a typo never silently selects a subprocess. `FEISHU_GROUP_HISTORY_CARD_REGIONS` (default `title,body`; `all`) selects which card regions enter a line, so the per-message cap applies to the answer rather than the execution trace: `parse_raw_card_content` and `parse_cli_card_text` both feed `CardRegions.render` (`【title】 body`); unparsable cards fall back to whole-content. Every path degrades to `""`.

## File: `plugins/platforms/feishu/feishu_cardkit.py`

Feature: CardKit three-element card engine.
Intent: Own the card lifecycle: create the JSON 2.0 entity, deliver it as `msg_type=interactive` with `type=card` + `card_id`, then mutate in place — full-element replace for the collapsible trace, the streaming `content` endpoint for the body, panel + settings patches to finalize. Keep `update_multi=true` (streaming refuses an exclusive card) and a strictly increasing `sequence` per card. Send the body's complete text to `PUT /elements/:id/content` as a plain string (a JSON wrapper returns code 0 and renders literally) and keep each update a prefix superset of the previous for the typewriter effect.

Feature: Card size budgeting and markdown fidelity.
Intent: Measure the 30 KB limit in UTF-8 bytes (CJK costs three per character); truncate on character boundaries, keep the body's head and the trace's tail, and roll an overflowing body onto a continuation card instead of clipping. De-indent fence markers so a fence inside a list item still renders as code, render each trace step as an explicit list item, and leave the reply body unmodified.

Feature: Execution-trace step segmentation.
Intent: One progress message is one step even when it spans lines. Fold a single-line command onto its tool line as inline code, keep a genuine multi-line script as a block attached to that step, and do not fold a headerless block (back-to-back terminal calls) onto the previous command.

Feature: Card update resilience and delivery recovery.
Intent: Re-queue a rejected element update (bounded) and re-open `streaming_mode` once when Feishu closes it after idle; serialize card creation per route. A rejected delivery is not always undelivered and a card binds to exactly one message, so the same `card_id` is never re-sent: `_deliver_entity` adopts the card when the rejection proves it landed (230099 with ErrCode 200780) or when the first entity's topic-creating reply failed with an unknown outcome (2200 or a raise) and the topic probe found the topic; otherwise `_create_and_deliver` retries once with a fresh entity addressed to the topic. The topic rule applies to the first entity only — after one failure a topic could hold either card, so the route falls back to text.

Feature: Card ownership and block addressing.
Intent: Each card has exactly one owner and is sealed when the owner changes, with an owner-scoped close for delegate loops (one A2A context, hence one owner tag, across every turn). Each send is an editable block within an area returned as a synthetic `hermes-card:` handle (never the real card message ID), so the gateway's send-then-edit pattern maps onto card regions and cannot reach the card through the `im` API; blocks can be retracted so a superseded preview does not linger. When a handle comes back as `reply_to`, a new card session uses the turn's own reply anchor instead.

## File: `plugins/platforms/feishu/plugin.yaml`

Feature: Feishu optional env contract.
Intent: Declare `FEISHU_CARD_OUTPUT`, the card title overrides and the attachment-receipt line, and the `FEISHU_GROUP_HISTORY*` knobs (`_LIMIT`, `_HOURS`, `_MSG_MAX_CHARS`, `_THREAD_LIMIT`, `_SOURCE`, `_CLI_BIN`, `_CARD_REGIONS`) as optional env so operators can discover and tune them, with defaults explicit: card output and history injection are off unless enabled, the history backend is the lark SDK API unless `cli`, and an unconfigured deployment gets plain text/post delivery and the first group message alone.

## File: `gateway/platforms/base.py`

Feature: Delegate-loop helper re-export.
Intent: Platform plugins import the delegate-loop helpers from the published `gateway.platforms.base`; they are defined in `gateway/platforms/base_delegate_loop.py` so this over-cap facade does not grow.

Feature: Feishu automatic-topic reply anchors.
Intent: Distinguish an `om_*` prospective topic root from a real `omt_*` topic when choosing the outbound reply anchor, so a message quoting an earlier one creates its topic under the current message while real-topic reply context is unchanged. The choice lives in `_reply_anchor_for_event`.

## File: `gateway/platforms/base_delegate_loop.py`

Feature: Shared delegate-loop helpers.
Intent: `is_gateway_command_during_delegate_loop(text)` is the one rule for what a foreground loop must not swallow — any resolvable gateway command except `/main` and `/exit`, parsed like `MessageEvent.get_command`. `DelegateLoopAdapterMixin._steer_delegate_turn` is the platform-neutral steering step: skip slash text, ask the loop input's bound session to `schedule_steer`, wait at most `DELEGATE_STEER_WAIT_SECONDS`, send `DELEGATE_STEER_ACK` on acceptance, and return False on any refusal/failure so the adapter keeps its next-turn queue. Feishu and Slack share both so timeout, acknowledgement and steerable-input semantics cannot drift.

Feature: Public per-session busy predicate.
Intent: `session_has_running_task()` lets an adapter ask, before starting a turn nobody requested, whether a message would land on work already in flight (the normal path queues follow-ups and never needed it). It heals a stale lock first so one leaked guard cannot silence a topic permanently, and returns False for a missing `_active_sessions` or empty key so a partially constructed adapter cannot turn a bookkeeping gap into silence.

## File: `agent/jev_client.py`

Feature: Typed Jev (System One) decision client.
Intent: One typed, calibrated decision surface — `noul` / `choice` / `score` over `POST {base_url}/v1/systemone` — for judgements made BEFORE the agent loop that must not become a generation call. Normalize all three primitives to one shape (a `score` reports the band its probability mass sits in; `noul` reports its probability as confidence). Every failure path (no key, unreachable host, non-JSON, missing answer) returns `None` rather than raising, so a failing classifier never drops a message or strands a turn. A short TTL+LRU memo keyed by the exact request coalesces repeats without becoming durable state. `ask_detailed` / `JevCallStats` report elapsed time, memo vs wire counts and failure class so the policy layer logs outcome and latency on one line (`ask` stays the simple contract; a memo hit legitimately reports ~0ms).

## File: `agent/jev_policy.py`

Feature: Jev channel-admission and complexity-routing policy.
Intent: Hold both decisions and their configuration in one place so the gateway, the Feishu adapter and the WORKAGENT A2A executor share identical semantics. Settings resolve env first (`TYPESAFE_*`, `HERMES_JEV_*`), then `config.yaml`'s `jev:` section, then a default, always through `agent.secret_scope` so a multiplexing gateway never serves one profile's agent description or band models to another. Both features are off by default and need full configuration (channel autoreply: a non-empty `agent_description`; complexity routing: at least one band). The removed `HERMES_JEV_BUSINESS_SCOPE` / `jev.business_scope` is detected in the "nothing to judge relevance against" warning, which names the rename (`agent_description`).

Feature: Channel admission verdict.
Intent: Verdict is `(in_scope OR delegatable OR addressed) AND wants_answer`, with each proposition a separate `noul` asked in one forward pass (folding them into compound questions collapses calibration). `in_scope` and `delegatable` are judged against the shared `agent` description plus an optional `remote_agents` inventory, since a request this agent hands to a specialist is its business too. `addressed` is a disjunct — naming the assistant is as deliberate as an @-mention — and stays near zero if the description never names the assistant. `wants_answer` is the AND term; its question names `agent` so the "directed at a specific named person" exclusion excludes only other people, not this assistant. A response missing ANY asked proposition is undecided, never defaulted.

Feature: Complexity routing.
Intent: A `choice` over `low` / `medium` / `high` / `other`, where each band's criterion is deployment-supplied (`HERMES_JEV_CRITERIA_<BAND>` / `jev.criteria.<band>`, env-over-config per band; a blank criterion falls back to the built-in default because an undescribed option loses calibration). Criteria live in the options, not the instructions, so an override cannot contradict the instruction text. `other` is the non-configurable residual that keeps the agent's default model (`resolve_tier_model` rejects `jev.models.other`); `judge_complexity` returns `"other"` rather than `None` so it can be remembered as a decision. There is no confidence floor: `other` is the explicit "cannot place this" signal, and confidence stays on log lines as a diagnostic only. The state is the closed set {request, agent}: `agent` describes the executor and is stable per agent, while channel, sender and a tool inventory are deliberately excluded. A narrow agent description narrows what gets re-routed.

Feature: Complexity decision frequency (`complexity_scope`).
Intent: Default `session` rates a session once (the first message is normally the task, and not switching models mid-conversation preserves the prompt cache); the alternative rates every turn. The band is memoized in a bounded in-process memo keyed by session **id** (a reset re-rates); an undecided turn is not remembered so one transport error cannot pin a session to the default model, and the band is stored rather than the resolved model so re-pointing a band reaches existing sessions. A caller with no session identity degrades to per-turn rating.

Feature: Per-band model, provider and reasoning effort.
Intent: `HERMES_JEV_MODEL_<BAND>`, `HERMES_JEV_PROVIDER_<BAND>` and `HERMES_JEV_REASONING_<BAND>` override `jev.models.<band>` field by field (not entry by entry, which would silently drop a config provider). A provider with no model is dropped with a warning; a band exists when it has a model OR an effort, so effort-only bands are possible. `TierModel.reasoning_effort` uses `hermes_constants.parse_reasoning_effort` (one vocabulary; unknown values warn and read as unset) and is not validated against the model — the transport layer clamps unsupported levels.

Feature: Band application on the A2A surface.
Intent: `apply_tier_to_agent` swaps a band pinned to another provider with `AIAgent.switch_model` (model, provider, credentials, `base_url`, `api_mode` atomically, restoring the previous runtime if the rebuild raises) and captures the agent's whole runtime and reasoning config as a baseline, so an unbanded turn restores provider and effort too; undoing a same-provider band stays a plain model assignment. The effort is applied even when the band's model is unreachable. Any failure leaves the agent on its current runtime. `_provider_ids_match` compares provider ids at one granularity (a bare name and its `custom:` form match; a lone `custom` identifies none, so an unverifiable provider keeps the profile model), reading `requested_provider` first.

## File: `gateway/run_aegis_delegate.py`

Feature: Stopping a turn blocked in an A2A delegation.
Intent: `_interrupt_running_turn` (behind `/stop`, `/new`, `/reset` and eviction) also cancels the running agent's A2A delegation — a delegate blocks the turn on a remote task or loop input, neither of which watches the interrupt flag. The cancel runs on a daemon thread that carries the profile context, so it never blocks the gateway loop or reorders the interrupt, invalidation and reap steps (wired from `gateway/run_agent_cache.py`).

Feature: Delegate runtime binding per gateway turn.
Intent: Bind adapter-provided delegate output and input factories onto both fresh and cached agents each turn so Slack/Feishu thread and user runtime state never leaks across cached sessions. Every turn (`gateway/run_turn_runner.py`) binds the adapter's `build_delegate_foreground_runtime` output adapter and input factory; Slack DMs that treat top-level messages as sessions key the route on the event message id.

## File: `gateway/run_source_envelope.py`

Feature: Source identity envelope for agent-bound messages.
Intent: Prefix Slack and Feishu agent-bound turns with compact structured metadata after command handling and all inbound-context assembly, whenever a trusted user ID exists (Feishu `chat_id` maps to the shared `channel` field; user ID and optional display name on the first line; Slack's shared-session participant prefix is preserved). It must not affect command parsing or other platforms' attribution, and stays distinct from the remote A2A envelope, which carries only parent platform/user identity. Built in `_prepare_inbound_message_text` (`gateway/run_inbound.py`), so Slack replay and relay follow-ups carry it too; `uname` is always present, possibly empty.

Feature: Adapter-supplied group history preamble.
Intent: Place `MessageEvent.metadata["group_history_block"]` directly under the `<source>` header, above the shared-session `[Name]` prefix, `[Replying to]` quote and media notes — as the outermost layer so existing user-turn blocks stay byte-identical and the history is one detachable preamble (the `channel_context` slot is unsuitable: it sits below the sender prefix and adds a `[New message]` marker).

## File: `gateway/run_feishu_card_markers.py`

Feature: Feishu card output stream classification.
Intent: Mark Feishu tool-progress sends `hermes_progress` and the long-running heartbeat `hermes_card_bypass` so a card-rendering adapter can tell execution chrome, the reply and transient notices apart. Both markers are Feishu-scoped and never reach conversation history. Applied from `gateway/run_turn.py`.

## File: `gateway/run_user_env_identity.py`

Feature: Plugin slash-command caller identity binding.
Intent: Bind the invoking user's userenv identity (from the adapter's `SessionSource`) around plugin-registered slash-command handlers, mirroring the tool-call path. Plugin dispatch runs inside `_handle_message` before `_set_session_env` binds `HERMES_SESSION_*`, so without this identity-dependent commands (e.g. `/userenv`) fail closed. Identity comes only from the gateway source, never message text, and the ContextVar is reset after the handler returns. Wired from `gateway/run_inbound_hooks.py` as `caller_user_env_identity(source)`.

## File: `gateway/run_inbound_hooks.py`

Feature: `pre_gateway_dispatch` session key.
Intent: The hook passes `session_key` (the route key the gateway uses for the message's canonicalized source; `""` when a bare runner cannot derive one) as an additive keyword field. Plugin slash commands run before the turn binds `HERMES_SESSION_KEY`, so a command that must find its session (ext-tools `/codex`, `/claude`) reads the captured key instead of the runner's private `_session_key_for_source`.

## File: `gateway/run_turn.py`

Feature: Fallback eviction limited to real fallbacks.
Intent: After a successful turn, evict the cached agent only when `_run_agent_evict_on_fallback` sees both a model differing from the configured one and `agent._fallback_activated` (set only when the fallback chain actually switched providers), while still respecting intentional `/model` overrides. Comparing models alone made every Jev-routed turn look like a fallback and rebuilt the agent each message, defeating the at-most-once-rebuild contract and restarting codex app-server threads.

## File: `gateway/run_jev_routing.py`

Feature: Jev complexity-based turn model routing.
Intent: Routing runs inside `_resolve_turn_agent_config(session_id=, model_pinned=)` (`gateway/run_turn_prepare.py`, also the turn runner and background tasks) and re-points the route — model, provider runtime and signature — at the decided band before fast-mode overrides, because the agent-cache key derives from the signature and a band switch must rebuild the agent, the same boundary a `/model` switch crosses. A band on another provider swaps credentials via the per-provider resolver and uses that provider's request overrides; a same-provider band only re-derives `api_mode`. Every failure (routing off, no band, unresolvable credentials, classifier error, empty message) keeps the session route. The session id is threaded through so the default `complexity_scope: session` crosses the cache boundary at most once; background tasks are rated per turn.

Feature: `/model` outranks complexity routing.
Intent: A model chosen explicitly is never replaced by a classifier: a session `/model` override or a launch policy pins the model (decided from session state, `session_model_pinned`, not a runtime marker) and the turn router returns before any Jev call, so a pinned session also stops paying for a discarded decision. `channel_overrides.model` still routes by band.

## File: `gateway/run_config_loaders.py`

Feature: Band reasoning effort in turn reasoning resolution.
Intent: `_resolve_session_reasoning_config(band_reasoning=)` is resolved after the route, against the model the turn runs, in main-turn and background paths. Precedence: session `/reasoning` > local policy > band effort > per-model `agent.reasoning_overrides` > global `agent.reasoning_effort` (an explicit human choice beats a classifier, and the band is the more specific decision); a one-turn reasoning override and API request options still win. The band's effort comes back as `band_reasoning_config` and never enters the route signature, so effort-only bands are cache-neutral.

## File: `tools/user_env_store.py`

Feature: Persistent per-user env storage.
Intent: Store user runtime env values in `$HERMES_HOME/users.env.json` keyed by `platform.user_id`, keep `CURRENT_USER_NAME` system-managed, and migrate a single legacy username-keyed entry safely. Reads accept a UTF-8 BOM (`utf-8-sig`) so a file touched by Windows tooling still loads.

## File: `tools/user_env_runtime.py`

Feature: Runtime user env identity.
Intent: Carry platform/user identity through ContextVars and thread propagation, with a stable local runtime scope key that isolates terminal environments by platform and user ID.

Feature: `call_as_agent_user`.
Intent: The one helper `agent/tool_executor.py` uses to bind the agent's platform user around a tool call and always restore the previous binding.

## File: `tools/userenv_tool.py`

Feature: User-scoped env management tool.
Intent: Let authenticated runtime users list, set and delete only their own env values, never returning secret values and never deleting `CURRENT_USER_NAME`.

## File: `tools/environments/local.py`

Feature: Per-spawn user env overlay.
Intent: `_scrubbed_env`, which every background spawn and the local run env pass through, adds the bound user's persisted env per spawn and never writes `os.environ`. A store that cannot be read degrades to no overlay.

## File: `tools/environments/base.py`

Feature: Local shell snapshot hygiene.
Intent: The command wrapper exports the user's values only for the active command and unsets them before the bootstrap and every snapshot re-dump (with `tools/environments/base_session_env.py`), so secrets never persist across commands, users, or after deletion.

## File: `tools/environments/base_session_env.py`

Feature: User env kept out of snapshots.
Intent: Unset the bound user's exported values before the session bootstrap and every snapshot re-dump, so the reusable shell snapshot never carries one user's secrets into another command or user.

## File: `tools/terminal_tool.py`

Feature: Local terminal environment cache isolation.
Intent: `_env_cache_key` keys the local backend as `local::{platform}::{user_id}` when a runtime identity is bound, so messaging users never share a shell while a user whose display name changes keeps theirs.

## File: `tools/delegate_tool.py`

Feature: Delegate subagent user env inheritance.
Intent: Classic `delegate_task` children get the parent's `user_id` / `user_name` and its env platform, so user-scoped env behaves the same for main agents and subagents.

## File: `cron/jobs.py`

Feature: Cron job identity ownership metadata.
Intent: `identify` is validated on create and update (helpers in `cron/jobs_identify.py`) and persisted only when set, so public jobs keep their stored shape. A malformed owner is flagged on read and never treated as public; `id`, `profile` and `profile_name` stay immutable and `update_job` still rejects them.

## File: `cron/jobs_identify.py`

Feature: Cron ownership helpers.
Intent: `build_job_identify`, `parse_job_identify` and `is_job_visible_to_identity` are the one definition of a job owner record and its visibility (legacy jobs without `identify` stay public; malformed records are non-public), shared by the cron tools, the scheduler, the AISOC/Workagent cron services and ext-tools `/cron_prompt`.

## File: `tools/cronjob_tools.py`

Feature: User-visible cron job filtering.
Intent: Create records the caller as owner; list, every job action and `context_from` see only the caller's and public jobs (`tools/cronjob_visibility.py`), so one messaging user cannot operate another's jobs. A name matching several jobs is resolved among the caller's own jobs only, so the ambiguity error never lists another user's job IDs. Webhook triggers stay unfiltered.

## File: `tools/cronjob_visibility.py`

Feature: Caller-scoped cron job lookup.
Intent: `caller_identity`, `visible_jobs`, `get_visible_job` and `resolve_visible_job_ref` apply the current runtime user's visibility to cron job reads in one place, so every cron tool action filters identically.

Feature: `resolve_caller_job(ref)`.
Intent: The plugin-facing caller-scoped job lookup with the same rules as the `cronjob` tool, used by ext-tools `/cron_prompt`: another user's job, or one with a malformed owner, reads as not found and is never named.

## File: `tools/cronjob_job_args.py`

Feature: Owner recorded on job creation.
Intent: New jobs created through the cron tool carry the caller's `identify`, never one taken from model-supplied arguments.

## File: `cron/scheduler.py`

Feature: Cron runs act as the job owner.
Intent: `run_job` binds the job's owner for the whole fire through a decorator and always undoes it; the cron agent carries the owner as `_user_env_platform` / user while staying `platform="cron"`, which v0.4 checks for cron behaviour such as approvals. A malformed owner fails that run explicitly instead of running as public.

## File: `cron/scheduler_prompt.py`

Feature: Prompt prerun scripts run as the owner.
Intent: Direct `_build_job_prompt` calls bind the job owner around prerun scripts too, so a script reads the owner's env even outside a full `run_job` fire.

## File: `cron/monitor.py`

Feature: Monitor scripts run as the owner.
Intent: Monitor and `no_agent` scripts bind the job owner like agent runs; a malformed owner fails only that monitor run, never the scheduler tick.

## File: `cron/scheduler_delivery.py`

Feature: Feishu topic delivery for cron output.
Intent: An explicit Feishu target on the origin chat inherits the origin topic, like Slack threads; the lost-thread warning fires only for the origin chat, and only dict responses are read for `thread_fallback`, so an SDK response object cannot crash bookkeeping and cause a duplicate send.

## File: `hermes_cli/main.py`

Feature: AISOC, Aegis and Workagent builtin CLI dispatch.
Intent: `main.py` only calls the builder in `hermes_cli/subcommands/products.py` and re-exports `cmd_aegis` / `cmd_aisoc` / `cmd_workagent`, listed as builtins so no plugin discovery is needed; product startup and security policy stay in each product's backend.

## File: `hermes_cli/subcommands/products.py`

Feature: `hermes aegis` / `hermes aisoc` / `hermes workagent`.
Intent: Attach each product's own parser and delegate to its backend `cmd_*` with lazy imports, so the product CLIs ship without growing `hermes_cli/main.py` or relying on plugin discovery.

## File: `pyproject.toml`

Feature: Opt-in A2A SDK and fork app packaging.
Intent: Keep `a2a-sdk[fastapi]` behind the opt-in `a2a` extra (out of `[all]`), pin the same version in the `test` group so the A2A suites run against the real SDK, and ship the aegis/aisoc/workagent packages through `packages.find`. `hermes_self_restart` needs no entry: v0.4's `setup.py` ships every root module.

## File: `pm/extras.py`

Feature: A2A extra anchor.
Intent: Register `a2a.client` / `a2a.types` as the `a2a` extra's import anchor so PM can tell whether the opt-in SDK is installed (v0.4 replaced `tools/lazy_deps.py` registration).

## File: `scripts/a2a_smoke_test.py`

Feature: Official A2A protocol smoke test.
Intent: Exercise Agent Card discovery, single and multi-turn context reuse, polling, streaming, terminal states and optional bearer auth through the installed A2A SDK for AISOC verification.

## File: `hermes_cli/config_defaults.py`

Feature: Jev decision-layer configuration surface.
Intent: Expose the `jev:` section in `DEFAULT_CONFIG` and spread the Jev secrets into `OPTIONAL_ENV_VARS`, line-neutral: the definitions live in `hermes_cli/config_defaults_jev.py` so this over-cap file only wires them in.

## File: `hermes_cli/config_defaults_jev.py`

Feature: Jev `jev:` config section, secrets and env names.
Intent: Declare the section `agent/jev_policy.py` reads (endpoint, decision model, feature flags, complexity scope, agent description, thresholds, per-band criteria, models and efforts; both features off by default), the `TYPESAFE_API_KEY` / `TYPESAFE_BASE_URL` setup entries (the only secrets, marked advanced) and the non-secret `TYPESAFE_MODEL` / `HERMES_JEV_*` names, in a sibling so the over-cap config files only wire it in.

## File: `hermes_cli/config.py`

Feature: Jev environment-variable recognition.
Intent: Union `JEV_ENV_KEYS` into `_EXTRA_ENV_KEYS` so `.env` reload and doctor know `TYPESAFE_*` / `HERMES_JEV_*` (the removed `HERMES_JEV_BUSINESS_SCOPE` still reports as unknown). They stay out of the global-env allowlist in `agent/secret_scope.py` so a multiplexing gateway can serve different agent descriptions and band models per profile.

## File: `agent/codex_runtime.py`

Feature: Owner-attached codex sessions.
Intent: `attach_codex_app_server_session(agent, session=None, *, cwd, thread_start_params, projection_limits)` lets a plugin backend (ext-tools `/codex`, `/claude`) create or re-bind its own thread across AIAgent rebuilds, with the approval hooks `_ensure_codex_session` builds. Hermes never retires an owner-attached session, never sends it Hermes' model, effort or tier, and never stores its thread id as the session's resumable `codex_thread_id`; Hermes' own session (by identity) keeps upstream behaviour.

Feature: Per-turn stream reset.
Intent: Each codex turn resets stream-delivery tracking and the pending segment break first, as the loop does per API call, so an interrupted stream's text is never compared against the next turn's.

Feature: Final-answer delivery and turn-input echo.
Intent: Not ported from aegis_v0.3 because v0.4 covers both: the gateway suppresses the final send by exact delivered text (#74248 / #80519), and `run_turn` records `submitted_user_text` so persistence drops exactly the codex echo.

Feature: `owner_session_approval(agent)`.
Intent: Non-codex owner sessions (Claude Code over ACP) get Hermes' codex approval policy: no callback where nobody can answer, auto-approve only under an explicit bypass, so `/claude` fails closed instead of reading terminal-tool internals.

## File: `agent/codex_owner_runtime.py`

Feature: Owner session runtime.
Intent: `enter_owner_codex_runtime` / `leave_owner_codex_runtime` switch an agent onto its owner-attached codex session at a turn boundary and back, so plugin backends no longer swap `api_mode` or publish the auxiliary runtime themselves. While an owner session runs, `_current_main_runtime` and the per-turn auxiliary publish report the host `api_mode`, so titles, review forks and compression checks are never downgraded to `codex_responses` on a `chat_completions` or `anthropic_messages` provider. Re-entering follows a host change made with `/model`; a native `codex_app_server` agent is never taken over.

## File: `agent/turn_context.py`

Feature: Host runtime during owner sessions.
Intent: The per-turn auxiliary-runtime publish uses the host `api_mode` recorded by `agent/codex_owner_runtime.py` while an owner-attached session runs the turn.

## File: `agent/transports/codex_app_server_session.py`

Feature: Session owner surface.
Intent: `thread_start_params` pins sandbox, approval policy and config per thread without editing `~/.codex/config.toml` (Hermes' cwd and personality still win). `effective_policy` records what codex actually applied from the `thread/start` or `thread/resume` response. Read-only `cwd`, `closed`, `turn_active`, `thread_start_params` and `rebind(...)` let an owner check reusability and move a thread to a new agent between turns without touching private state.

## File: `agent/transports/codex_event_projector.py`

Feature: Optional `ProjectionLimits`.
Intent: A session owner may clamp command output (head + tail), long tool-argument strings and reasoning in what its threads write to Hermes history, while the full turn stays in codex's own thread. Only `/codex` threads pass limits (read from `bridge.codex_*` config when the session is created); without limits the projection is unchanged.

## File: `agent/auxiliary_client.py`

Feature: Named custom providers honor `raw_codex` in `resolve_provider_client`.
Intent: For a named custom provider declaring `api_mode: codex_responses`, return the bare client to `raw_codex=True` callers (the main client and the fallback chain call `responses.create()` directly; the wrapper has no `.responses` and drops `_custom_headers`). Auxiliary callers without `raw_codex` still get `CodexAuxiliaryClient`.

## File: `agent/model_metadata.py`

Feature: DeepSeek V4 family context fallback.
Intent: A `deepseek-v4` family key gives every V4 point release and dated snapshot the 1M window instead of the 128K `deepseek` catch-all, and marks a point release cached at the catch-all as stale. Upstream's explicit V4 entries, pre-V4 ids and NVIDIA's endpoint-scoped 262,144 are unchanged.

## File: `hermes_state.py`

Feature: Session ownership for per-account consoles.
Intent: Expose `SessionDB.set_session_user_id` (implemented in `hermes_state_owner.py`) so a console can attach or clear an account on an existing session row.

## File: `hermes_state_owner.py`

Feature: Session owner writes.
Intent: Attach or clear a session's account after the row exists, used by the AISOC/Aegis consoles to scope their private session lists.

## File: `hermes_state_sessions.py`

Feature: Owner-scoped session listing.
Intent: `list_sessions_rich` / `session_count` take `user_id` and `include_ownerless` (default `True`: owned plus ownerless history; `False`: strictly private) through the shared WHERE builder, so counts, the last-active query and the pinned back-fill agree.

## File: `hermes_cli/plugins_context.py`

Feature: `ctx.session_id()`.
Intent: Plugins identify the calling session without private manager state: the classic CLI's session counts even before its first turn built an agent; elsewhere it is the turn's bound `HERMES_SESSION_ID` (resolved by `hermes_cli/plugin_session.py`).

## File: `hermes_cli/plugin_session.py`

Feature: Calling-session id for plugin contexts.
Intent: `context_session_id(manager)` backs `ctx.session_id()` so ext-tools `/codex` and `/claude` find their session through the published plugin surface instead of `get_plugin_manager()._cli_ref`.

## File: `hermes_cli/data/plugin_api.json`

Feature: The "Aegis fork" published names.
Intent: The fork's bundled plugins import nothing outside the published list. The fork groups list only fork-owned plugin-facing surfaces (user-env identity and store, cron caller visibility, the codex owner-session surface and `agent.codex_owner_runtime`) and stable generic helpers (`spawn_owned`, `release_process_group_once_empty`, `hermes_subprocess_env`, approval-bypass queries, `neutralize_untrusted_inline_text`), each with no `ctx` or upstream equivalent; underscore names are never published. Documented in `aegis/docs/hermes/plugin-api.md` and the regenerated public-api page.

## File: `scripts/boundary_lint_baseline.json`

Feature: Intended Feishu/Slack fork crossings.
Intent: The fork's `adapter_aegis*.py` siblings import the fork's own core modules (`agent.jev_policy`, `tools.feishu_group_history`) and helpers the adapters already used (`gateway.session.build_session_key`, `utils.env_float`); added by hand, never with `--update`, so no other crossing is baselined.

## File: `scripts/install_root_lint_baseline.json`

Feature: Product direct-script startup.
Intent: The aegis/aisoc/workagent backends keep the `sys.path` bootstrap that lets `python <app>/backend/main.py` run directly; their install root otherwise comes from `project_root_str()`.
