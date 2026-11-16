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

## File: `toolsets.py`

Feature: Toolset catalog.
Intent: Expose `userenv` and A2A tools behind explicit toolsets so users opt in without adding schema cost to every session.

## File: `hermes_cli/tools_config.py`

Feature: Configurable user environment and A2A toolsets.
Intent: Surface `userenv` and `aegis_a2a` in the standard `hermes tools` flow, disabled by default until enabled per platform, without changing AISOC's dedicated A2A runtime.

## File: `model_tools.py`

Feature: Agent-loop tool ownership.
Intent: Route `aegis_a2a_delegate` through the live `AIAgent`, like `delegate_task`, because it needs parent runtime state and adapter-bound foreground I/O.

## File: `run_agent.py`

Feature: Remote-only A2A delegate dispatch.
Intent: Centralize `aegis_a2a_delegate` dispatch on `AIAgent` so sequential, concurrent and helper execution paths all receive the same parent agent, output adapter and input factory.

## File: `agent/tool_executor.py`

Feature: Tool execution identity binding.
Intent: Bind per-user env identity around sequential and concurrent tool execution (including the default sequential registry path) so terminal and runtime tools still see the calling platform user when cron clears `HERMES_SESSION_*` or execution crosses thread boundaries.

Feature: A2A delegate execution.
Intent: Run `aegis_a2a_delegate` through an agent-loop branch with concise spinner handling instead of the generic registry handler.

## File: `agent/agent_runtime_helpers.py`

Feature: Agent-owned tool dispatch.
Intent: Treat `aegis_a2a_delegate` as agent-owned and wrap runtime helper execution in the same user env identity binding as the main executor.

## File: `agent/display.py`

Feature: Tool progress display.
Intent: Show `aegis_a2a_delegate` with a compact goal preview so foreground delegation stays legible.

## File: `plugins/platforms/slack/adapter.py`

Feature: Slack delegate foreground I/O and stream state.
Intent: Provide the route, input-adapter and output-adapter contracts for foreground delegate loops, keyed by Slack session dimensions and dispatched on the adapter event loop; every runtime-factory call returns a fresh blocking input adapter so cached agents and concurrent threads never share a queue. Track output per route: edit AI deltas (at most once per three seconds; first send and forced flushes at segment boundaries are immediate), split long messages, flush before tool-call/user-input breaks, and never duplicate final AI output.

Feature: Slack foreground routing and steering.
Intent: Capture follow-ups for active delegate loops before normal dispatch so they do not leak into the main session. `/main` and `/exit` are recognized from the original Slack text (thread-context or attachment prefixes must not turn them into remote prompts); every other resolvable gateway command (`/stop`, `/new`, …) skips the loop and is handled by the gateway. While the remote turn is running, a non-command follow-up is folded into it through the loop input's bound A2A session (`bind_a2a_session` / `schedule_steer`) and acknowledged, with no interrupt and no new turn; the stream segment breaks first so the acknowledgement lands after the streamed text. Slash text, remotes without steering, and refused/failed steers keep the next-turn queue, so input is never dropped.

Feature: Slack delegate approval and clarification interactions.
Intent: Keep remote A2A pending state separate from local gateway approval/clarify state; render delegate Block Kit controls (including Other and multi-select), validate channel/thread/user and duplicate-click boundaries, and route button/text responses through the authenticated remote responder, not a local resolver.

Feature: Slack clarify Block Kit prompts.
Intent: Render multi-choice gateway clarify prompts as buttons, resolve authorized clicks through the shared clarify primitive, keep the typed-answer fallback for Other/open-ended answers, and enforce the same user authorization boundary as approval and slash-confirm interactions.

Feature: Slack source identity.
Intent: Keep `<source>` attribution intact across every entry point: slash-command events (including legacy `/hermes` free-form turns) carry the invoking display name; display-name/bot-status lookups unwrap `SlackResponse` payloads so `SessionSource.user_name` is the name rather than the member ID (an invalid bot probe must not poison the shared name cache); a `bot_message` event without `user` keeps `bot_id` as the sender identity, propagates the bot marker through authorization and `SessionSource`, and uses the event-provided bot name without calling the user lookup API.

## File: `plugins/platforms/feishu/adapter.py`

Feature: Feishu delegate foreground I/O, routing and steering.
Intent: Provide isolated per-runtime blocking input adapters and adapter-loop output routing for `aegis_a2a_delegate(is_loop=true)`, keyed by chat, topic and user so cached agents, users and topics never share a queue. Route active-loop text and exact mention-stripped `/main` / `/exit` before normal message, media and chat-guard dispatch; every other resolvable gateway command skips the loop (and its interaction answers) and reaches normal dispatch. While the remote turn is running, a non-command follow-up is folded into it through the bound session's `schedule_steer` (via `BasePlatformAdapter._steer_delegate_turn`) and acknowledged, with no interrupt and no new turn; slash text, remotes without steering, and refused/failed steers keep the next-turn queue.

Feature: Feishu delegate stream state.
Intent: Send the first AI delta immediately, throttle edits of the same message to once per three seconds, force-flush at final/tool/user-input boundaries, split oversized output, fall back to appended messages after an edit failure, and cancel delayed flushes when a route exits or the adapter stops.

Feature: Feishu delegate approval and clarification cards.
Intent: Render remote A2A approval/clarify interactions as interactive cards (payloads keep only IDs/indexes), preserve chat/thread/authorization and duplicate-click checks, route choices and Other text through the remote responder, and accept either tenant-scoped `operator.user_id` or app-scoped `operator.open_id` when matching the stored source identity. Multi-choice gateway clarify prompts use the same card path with typed-answer fallback. The typed-text interaction router runs ahead of the foreground router on every inbound message, so it must tolerate an adapter built without `__init__` (no pending state means pass through to normal dispatch, not raise).

Feature: Feishu WebSocket SDK compatibility and source display name.
Intent: Pass `extra_ua_tags=["channel"]` on current lark-oapi, but let legacy clients that reject the keyword connect with an explicit group-mention delivery warning instead of failing startup. Resolve sender names through Contact v3 using event `open_id` first, cache a successful name across all identity aliases, and keep the source-envelope `uname` key present (empty) when lookup is unavailable.

Feature: Automatic topic creation and root-keyed session routing.
Intent: With `FEISHU_REPLY_THREAD` (default `true`), treat a top-level message's `om_*` ID in regular groups and DMs as the prospective topic root and reply anchor, create the topic with `reply_in_thread=true`, and carry the root metadata through final, progress, approval, clarification, media and error delivery. A main-timeline quote reply (`root_id`, no `thread_id`) is keyed on its OWN message id, not the quoted one — Feishu roots the topic at the reply, so keying on the quoted root splits one conversation in two and sends approval cards to a second topic (the quoted text still reaches the model as reply context); with the switch off and no Jev-forced topic, it keeps the upstream quoted-root key. A later `omt_* + root_id` message reuses the root-keyed session only when that root has active or persisted routing (`_auto_thread_root_session`, the single spelling of the rule); forum and manually created topics keep their real-thread identity. Once an automatic root session was ended by `session_reset` or pruned after restart, a later topic message may start a new session on the real `omt_*` route; AIAgent cache eviction is independent of session continuity, and this lifecycle is independent of the `FEISHU_REPLY_THREAD` switch.

Feature: Topic-creation failure handling.
Intent: At most one flat-message fallback, and only for failures that rule a topic out: root withdrawn or missing (230011/231003), group without topic replies (230071), aggregated-message root (230072). Any other failure (rate limit, card validation, internal error 2200 — seen on a reply Feishu had actually bound) says nothing about topics, so `_auto_thread_failure_action` first asks Feishu whether the root now has a `thread_id` (marking the topic established) and retries the same send in the topic inside `_feishu_send_with_retry` (not in the caller: `send()` does not stop at a failed chunk, so a mid-message failure would be lost); only a second in-topic failure with still no topic sends the flat message. A payload carrying a CardKit `card_id` is never re-sent flat (an entity binds to exactly one message); the card layer owns that retry. Failure logs carry `code`, `msg`, `log_id` and the `reply_to` in use.

Feature: Card output mode.
Intent: Behind opt-in `FEISHU_CARD_OUTPUT` (default false; yaml `feishu.card_output` wins), intercept `send`/`edit_message`/`delete_message` so one agent turn renders as one CardKit card, bracketed by `on_processing_start` / `on_processing_complete`. Outbound metadata decides placement: `hermes_progress` goes to the collapsible panel, anything else is the reply body. Only output produced inside a turn becomes a card (a slash-command reply or cron push never creates an empty-panel card), and any CardKit failure returns the route to the text/post path rather than costing a reply. The synthetic `hermes-card:` handle returned by card-mode sends is never used as a reply target (the stream consumer chains overflow chunks by the returned id, and the reply API rejects it with 99992402); `_feishu_send_with_retry` drops it and lets thread metadata place the message.

Feature: Delegate card rendering and transient notices.
Intent: Render each delegated remote agent into its own card keyed by A2A context ID, sealed on that delegate's turn-final event, so a foreground loop yields one card per exchange. Accumulate streamed deltas synchronously and serialize the open-or-extend decision per owner (delegate events are independent fire-and-forget tasks), track whether an exchange streamed separately from the current text segment so the final event cannot re-append text already shown, drop transport-level `status` events in card mode, and keep `error` visible. Liveness signals and acknowledgements stay out of the answer card: `hermes_card_bypass` or the gateway's `non_conversational` marker route a send to plain text/post (`hermes_progress` still wins), the delegate resolved-acks and expired-approval correction set it at their call sites, and a bypassed notice keeps a real message ID so its edit/delete paths work.

Feature: Card-mode and in-topic attachment delivery.
Intent: A file is never card content, and card mode must not change which files get delivered. Text entry points that skip the gateway's attachment stripping (mid-turn commentary, delegate output) are extracted here with the gateway's own `extract_media` / `extract_local_files` and filters (so `MEDIA:` tags and bare deliverable paths behave as with card output off, and a path in a code fence ships nothing); each file leaves as its own message, the card keeps a one-line receipt, and a per-turn ledger (claimed before upload, released on failure, inert outside a card turn) prevents double delivery on retried sends. Inside a topic, `receive_id_type=thread_id` is rejected for every attachment kind with a bare validation code, so `_send_attachment_message` re-anchors through the reply API on a message in the topic and only then posts flat; other failure codes stay visible and unretried.

Feature: Jev channel-admission gate for unmentioned group messages.
Intent: Judge any unmentioned human group message — whether the mention gate was about to drop it (`group_mention_missing`, distinct from `group_policy_rejected`) or the group admits unaddressed messages (`require_mention: false`, the setting that most needs a scope filter). The drop is deferred until text is extracted; bot senders (bot-to-bot loops) and slash commands (another bot's `/reset` must never reach this agent) skip the gate; the feature flag is read before `_mentions_self` so a disabled gate costs nothing. It fails closed in every direction (feature off, no API key, no agent description, undecided verdict, raising classifier). An admitted message forces a topic reply on its own `om_*` root even with `FEISHU_REPLY_THREAD` off, and the event is marked `jev_channel_autoreply`.

Feature: A busy topic is never interrupted by an unprompted reply.
Intent: With `HERMES_JEV_THREAD_AUTOREPLY` on, an unaddressed message inside a topic is dropped outright when a turn is already running in that topic's session, before Jev is consulted (an @-mentioned follow-up is still queued by `handle_message`). It is scoped to messages carrying a live `thread_id` — top-level messages and main-timeline quote replies open a new topic. It must resolve the SAME session identity dispatch will settle on, so `build_source` runs above the gate and `_auto_thread_root_session` is shared; the probe restores `source.thread_id`, treats a not-yet-existing session as idle, and any failure in the check falls through to the Jev verdict.

Feature: Recent group history injection.
Intent: Behind opt-in `FEISHU_GROUP_HISTORY` (default false; yaml `platforms.feishu.extra.group_history_*` wins), give the agent the conversation it is pulled into when it has no context yet. The adapter owns only the gate and wiring: a non-`p2p`, non-command message whose resolved `source` has no active or persisted session (`_session_exists_for_source`, checked after the auto-thread block fixes identity — event thread flags cannot be trusted because Feishu stamps `thread_id` onto quote-reply chains) snapshots the `group_history_*` settings, picks the backend from `FEISHU_GROUP_HISTORY_SOURCE` (`api` default, or `cli`), and delegates to `tools.feishu_group_history.build_group_history_block`; the resulting block is stored on `MessageEvent.metadata["group_history_block"]` and never touches `text`. Chat-level lists exclude topic replies, so a first-turn message inside a real `omt_*` topic also gets a `<thread_messages>` block (`FEISHU_GROUP_HISTORY_THREAD_LIMIT`). Text batching keeps the first event's metadata, so a turn carries one block. Failures log and degrade to no block; the feature never blocks a turn.

## File: `tools/feishu_group_history.py`

Feature: Group / topic history fetch and rendering, two backends.
Intent: Keep everything below the adapter's gate in one adapter-independent module so the history block has one shape regardless of backend. `GroupHistorySettings` carries the `FEISHU_GROUP_HISTORY_*` knobs (window hours default 24, newest `LIMIT` default 20 capped at the page size 50, topic limit, per-message cap `MSG_MAX_CHARS` default 1000 with 0 = off); `render_history_block` does selection (skip deleted/excluded/empty, newest N, chronological), labelling (`MM-DD HH:MM`, `[reply]`/`[in-topic]` only in the chat listing, `[assistant]` when `sender.id == app_id`, `[bot] name` for peer apps, display names for humans), neutralization via `neutralize_untrusted_inline_text`, and the cap; `build_group_history_block` composes `<group_messages>` plus the de-duplicated `<thread_messages>` block and logs items-returned vs lines-injected.
`ApiHistorySource` calls `im/v1/messages` (`container_id_type=chat|thread`, `ByCreateTimeDesc`, `card_msg_content_type=raw_card_content` — without it a CardKit card is a title-only stub) through the adapter's SDK client. `LarkCliHistorySource` runs `lark-cli im +chat-messages-list` / `+threads-messages-list` (`--as bot --order desc --no-reactions --format json`), accepts only `ok == true` envelopes, uses the CLI's rendered `content`, `sender.name` and `create_time`, ignores inline `thread_replies`, and treats a missing binary, non-zero exit, 30s timeout or non-JSON output as no history. `normalize_history_source` accepts `api`/`cli` (aliases `lark-cli`, `feishu-cli`) and falls back to `api` with a warning, so a typo never silently selects a subprocess. `FEISHU_GROUP_HISTORY_CARD_REGIONS` (default `title,body`; `all`) selects which card regions enter a line, so the per-message cap applies to the answer rather than the execution trace: `parse_raw_card_content` and `parse_cli_card_text` both feed `CardRegions.render` (`【title】 body`); unparsable cards fall back to whole-content. Every path degrades to `""`.

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

Feature: Shared delegate-loop helpers.
Intent: `is_gateway_command_during_delegate_loop(text)` is the one rule for what a foreground loop must not swallow — any resolvable gateway command except `/main` and `/exit`, parsed like `MessageEvent.get_command`. `BasePlatformAdapter._steer_delegate_turn` is the platform-neutral steering step: skip slash text, ask the loop input's bound session to `schedule_steer`, wait at most `DELEGATE_STEER_WAIT_SECONDS`, send `DELEGATE_STEER_ACK` on acceptance, and return False on any refusal/failure so the adapter keeps its next-turn queue. Feishu and Slack share both so timeout, acknowledgement and steerable-input semantics cannot drift.

Feature: Public per-session busy predicate.
Intent: `session_has_running_task()` lets an adapter ask, before starting a turn nobody requested, whether a message would land on work already in flight (the normal path queues follow-ups and never needed it). It heals a stale lock first so one leaked guard cannot silence a topic permanently, and returns False for a missing `_active_sessions` or empty key so a partially constructed adapter cannot turn a bookkeeping gap into silence.

Feature: Feishu automatic-topic reply anchors.
Intent: Distinguish an `om_*` prospective topic root from a real `omt_*` topic when choosing the outbound reply anchor, so a message quoting an earlier one creates its topic under the current message while real-topic reply context is unchanged.

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

## File: `gateway/run.py`

Feature: Stopping a turn blocked in an A2A delegation.
Intent: `_interrupt_and_clear_session` (behind `/stop`, `/new`, `/reset`) also cancels the running agent's A2A delegation — a delegate blocks the turn on a remote task or loop input, neither of which watches the interrupt flag. The cancel runs in a daemon thread so it never blocks the gateway loop or reorders the interrupt, invalidation and reap steps.

Feature: Delegate runtime binding per gateway turn.
Intent: Bind adapter-provided delegate output and input factories onto both fresh and cached agents each turn so Slack/Feishu thread and user runtime state never leaks across cached sessions.

Feature: Source identity envelope for agent-bound messages.
Intent: Prefix Slack and Feishu agent-bound turns with compact structured metadata after command handling and all inbound-context assembly, whenever a trusted user ID exists (Feishu `chat_id` maps to the shared `channel` field; user ID and optional display name on the first line; Slack's shared-session participant prefix is preserved). It must not affect command parsing or other platforms' attribution, and stays distinct from the remote A2A envelope, which carries only parent platform/user identity.

Feature: Adapter-supplied group history preamble.
Intent: Place `MessageEvent.metadata["group_history_block"]` directly under the `<source>` header, above the shared-session `[Name]` prefix, `[Replying to]` quote and media notes — as the outermost layer so existing user-turn blocks stay byte-identical and the history is one detachable preamble (the `channel_context` slot is unsuitable: it sits below the sender prefix and adds a `[New message]` marker).

Feature: Feishu card output stream classification.
Intent: Mark Feishu tool-progress sends `hermes_progress` and the long-running heartbeat `hermes_card_bypass` so a card-rendering adapter can tell execution chrome, the reply and transient notices apart. Both markers are Feishu-scoped and never reach conversation history.

Feature: Plugin slash-command caller identity binding.
Intent: Bind the invoking user's userenv identity (from the adapter's `SessionSource`) around plugin-registered slash-command handlers, mirroring the tool-call path. Plugin dispatch runs inside `_handle_message` before `_set_session_env` binds `HERMES_SESSION_*`, so without this identity-dependent commands (e.g. `/userenv`) fail closed. Identity comes only from the gateway source, never message text, and the ContextVar is reset after the handler returns.

Feature: Fallback eviction limited to real fallbacks.
Intent: After a successful turn, evict the cached agent only when `_fallback_left_config_model()` sees both a model differing from the configured one and `agent._fallback_activated` (set only when the fallback chain actually switched providers), while still respecting intentional `/model` overrides. Comparing models alone made every Jev-routed turn look like a fallback and rebuilt the agent each message, defeating the at-most-once-rebuild contract and restarting codex app-server threads.

Feature: Jev complexity-based turn model routing.
Intent: `_apply_jev_complexity_route` (a module-level function, so the turn-route builder works with a stand-in `self`) mutates the route in place INCLUDING its signature, because the agent-cache key derives from it and a band switch must rebuild the agent — the same boundary a `/model` switch crosses, hence banding is applied only on a decided band. A band on another provider swaps credentials via the per-provider resolver; a same-provider band only re-derives `api_mode`. Every failure (routing off, no band, unresolvable credentials, classifier error, empty message) leaves the route as resolved. The session **id** is threaded through `_resolve_turn_agent_config` so the default `complexity_scope: session` crosses the cache boundary at most once.

Feature: `/model` outranks complexity routing.
Intent: A model chosen explicitly with `/model` is never replaced by a classifier. `_resolve_session_agent_runtime` sets a private `_session_model_pinned` marker on both override paths and the turn router returns before any Jev call (so a pinned session also stops paying for a discarded decision). It is a marker, not a runtime field, so it cannot reach `AIAgent(**runtime)`; `channel_overrides.model` still routes by band.

Feature: Band reasoning effort in turn reasoning resolution.
Intent: Resolve `reasoning_config` AFTER `_resolve_turn_agent_config`, against `turn_route["model"]`, in both main-turn and background-task paths. Precedence: session `/reasoning` > band effort > per-model `agent.reasoning_overrides` > global `agent.reasoning_effort` (an explicit human choice beats a classifier, and the band is the more specific decision). `_apply_jev_complexity_route` returns the effort as `route["band_reasoning_config"]` (also when the band's model is unreachable) rather than applying it, because only the caller sees the session override; it never enters `signature`, so effort-only bands are cache-neutral.

Feature: Master switch and sub-switches for unaddressed-group-message admission.
Intent: `HERMES_JEV_AUTOREPLY` (default off) is checked before the sub-switches, so off means no admission stage at all (Jev is never called; sub-switches are inert, never partially honoured). On, an unaddressed group message is answered ONLY on an explicit allow — denial, transport error, undecided verdict, uncovered category, missing key or empty agent description all end in silence; sub-switches only narrow which messages are eligible. `channel_admission_mode()` resolves the three-way outcome (judge / silence / normal) once so callers cannot re-derive it wrongly and fail open (which matters under `require_mention: false`). DMs, @-mentions, bot senders and slash commands keep their carve-outs. `HERMES_JEV_THREAD_AUTOREPLY` (default off) scopes admission away from existing topics (a message with `thread_id` or `root_id`; a topic's first message is always judged): off, a thread message goes to the normal mention gate, so the flag only narrows. Scope is the gateway; the WORKAGENT A2A service keeps its own rule (anything short of an explicit "no" is answered) because a caller blocks on its reply.

## File: `tools/user_env_store.py`

Feature: Persistent per-user env storage.
Intent: Store user runtime env values in `$HERMES_HOME/users.env.json` keyed by `platform.user_id`, keep `CURRENT_USER_NAME` system-managed, and migrate a single legacy username-keyed entry safely.

## File: `tools/user_env_runtime.py`

Feature: Runtime user env identity.
Intent: Carry platform/user identity through ContextVars and thread propagation, with a stable local runtime scope key that isolates terminal environments by platform and user ID.

## File: `tools/userenv_tool.py`

Feature: User-scoped env management tool.
Intent: Let authenticated runtime users list, set and delete only their own env values, never returning secret values and never deleting `CURRENT_USER_NAME`.

## File: `tools/environments/local.py`

Feature: Local subprocess env overlay.
Intent: Overlay the current user's persisted env into reusable local shells and one-shot spawns without mutating process-global `os.environ`.

## File: `tools/environments/base.py`

Feature: Local shell snapshot hygiene.
Intent: Export user env values only for the active command and unset them before saving the reusable shell snapshot, so secrets do not persist across users or after deletion.

## File: `tools/terminal_tool.py`

Feature: Local terminal environment cache isolation.
Intent: Key the local backend cache as `local::{platform}::{user_id}` when a runtime identity is bound, isolating messaging users while letting user-name changes reuse the same environment.

## File: `tools/delegate_tool.py`

Feature: Delegate subagent user env inheritance.
Intent: Carry the parent's user identity into classic `delegate_task` children so user-scoped env behaves the same for main agents and subagents.

## File: `cron/jobs.py`

Feature: Cron job identity ownership metadata.
Intent: Add normalized `identify` ownership records; legacy `identify=None` jobs stay visible public jobs, malformed identify data is non-public; `id`, `profile` and `profile_name` stay immutable while `identify` can be corrected through raw job updates.

## File: `tools/cronjob_tools.py`

Feature: User-visible cron job filtering.
Intent: Scope cron create/list/update/pause/resume/remove/run/context references to the current runtime user plus legacy public jobs, so one messaging user cannot operate another's jobs.

## File: `cron/scheduler.py`

Feature: Cron runtime user identity restoration.
Intent: Restore platform/user identity from `identify` across cron agent runs, prompt prerun scripts and `no_agent` script execution, so scheduled jobs apply the owner's env without gateway session vars; fail malformed identify records explicitly instead of treating them as public, and keep the legacy one-argument script-runner call for public jobs.

## File: `tools/lazy_deps.py`

Feature: Lazy A2A SDK dependency.
Intent: Register `a2a-sdk[fastapi]==1.1.0` as an opt-in lazy dependency so A2A client and FastAPI server surfaces load on demand without expanding the core install.

## File: `hermes_cli/main.py`

Feature: AISOC and Aegis builtin CLI dispatch.
Intent: Recognize `hermes aisoc` and `hermes aegis` without plugin discovery, register their product-owned parsers, and forward the parsed namespace to each backend, keeping product startup and security policy outside Hermes core.

## File: `pyproject.toml`

Feature: AISOC/Aegis package and A2A server dependency exposure.
Intent: Ship the two product packages as importable distributions and keep the FastAPI portion of the A2A SDK behind the opt-in `a2a` extra, not the default install.

## File: `scripts/a2a_smoke_test.py`

Feature: Official A2A protocol smoke test.
Intent: Exercise Agent Card discovery, single and multi-turn context reuse, polling, streaming, terminal states and optional bearer auth through the installed A2A SDK for AISOC verification.

## File: `hermes_cli/config_defaults.py`

Feature: Jev decision-layer configuration surface.
Intent: Declare the `jev:` section (endpoint, decision model, feature flags, complexity scope, agent description, thresholds, per-band criteria, models and reasoning efforts, including effort-only bands and precedence) so it is discoverable in one place, with both features defaulting off. `TYPESAFE_API_KEY` (and `TYPESAFE_BASE_URL` for self-hosted/proxied gateways) are the only secrets, marked advanced; every other knob is a setting whose canonical home is config.yaml even though it is also readable from the environment.

## File: `hermes_cli/config.py`

Feature: Jev environment-variable recognition.
Intent: Keep `TYPESAFE_MODEL` / `HERMES_JEV_*` keys (including per-band model, provider, reasoning and criteria vars, but not the removed `HERMES_JEV_BUSINESS_SCOPE`, which reports as unknown) known to `.env` reload and doctor. They stay out of the global-env allowlist in `agent/secret_scope.py` so a multiplexing gateway can serve different agent descriptions and band models per profile.

## File: `agent/codex_runtime.py`

Feature: Codex app-server commentary vs final-answer streaming.
Intent: Hold a completed `agentMessage` until the next item of the same turn starts (delta, `item/started`, or a fast tool `item/completed`) — only then is it commentary and emitted, followed by a stream-segment reset and paragraph break — and drop it at the turn boundary (`turn/started`, `turn/completed`, a different turn id), because it was the final answer, which normal final-response delivery owns, matching the Hermes loop. `run_codex_app_server_turn` resets stream delivery tracking at every codex turn start. Otherwise the final answer reaches gateways twice and the streamed-text tracker carries the previous turn.

Feature: Reusable codex app-server session binding.
Intent: `attach_codex_app_server_session(agent, session=None, cwd=None, thread_start_params=None)` builds the owner-bound hooks (approval callback from the calling thread, approval-bypass routing, event bridge) once and creates or re-binds a session on `agent._codex_session`. The lazy creation in `run_codex_app_server_turn` and session-level owners (the `/codex` plugin) both use it, so one codex thread can move onto a rebuilt AIAgent.

Feature: Turn input de-duplication.
Intent: codex echoes each `turn/start` input as a `userMessage` item. Hermes has already flushed that user turn, so `run_codex_app_server_turn` drops the first projected user message when it matches the turn input before splicing and persisting (otherwise history and state.db store it twice and replay it twice after switching back). Later user items such as mid-turn steer input are kept.

## File: `agent/transports/codex_app_server_session.py`

Feature: Session state accessors and hook re-binding.
Intent: Expose read-only `cwd`, `closed`, `turn_active`, `effective_policy` and `rebind(approval_callback, on_event, request_routing)` so an owner above one AIAgent can check reusability, close safely, and repoint callbacks to a new agent between turns without touching private state; the subprocess and thread are unaffected.

Feature: Per-thread `thread/start` overrides and reported policy.
Intent: `CodexAppServerSession(thread_start_params=...)` merges stable fields (`sandbox`, `approvalPolicy`, `config`) into the request (session cwd always authoritative) and exposes them read-only so an owner can tell whether a thread matches the requested sandbox — a sandbox choice without editing `~/.codex/config.toml`. Hermes sends no permissions itself (codex resolves them from its config and project trust), so `ensure_started()` records what codex reports in the `thread/start` response (permission profile, approval policy, sandbox, network access, writable roots, /tmp and $TMPDIR writability) as `effective_policy` and logs those real values; Hermes' `HERMES_TERMINAL_SECURITY_MODE` mapping is only the documented requested profile.

## File: `agent/chat_completion_helpers.py`

Feature: Fallback activation honors a named custom provider's declared `api_mode`.
Intent: `try_activate_fallback` reads `api_mode` from the named custom provider entry (`custom_providers:` / `providers:`, the field `_resolve_named_custom_runtime` honors on the primary path) before falling back to built-in heuristics, so a Responses-only third-party endpoint (e.g. a GLM Coding Plan key entitled only on `/responses`) is not sent to `/chat/completions` after failover and rejected with `403 model_access_denied`. Built-in providers are unaffected.

## File: `agent/auxiliary_client.py`

Feature: Named custom providers honor `raw_codex` in `resolve_provider_client`.
Intent: For a named custom provider declaring `api_mode: codex_responses`, skip the `CodexAuxiliaryClient` wrap when the caller passes `raw_codex=True` (as the main loop's fallback activation and init routing do, since they call `responses.create()` directly; the wrapper has no `.responses` and drops `_custom_headers`), matching `_needs_codex_wrap` and the `openai-codex` branch. Auxiliary callers without `raw_codex` still get the wrapper.

## File: `agent/model_metadata.py`

Feature: DeepSeek V4 family hardcoded context fallback.
Intent: A single `deepseek-v4` family key at 1M makes every V4 slug (point releases, dated snapshots, vendor-prefixed and mixed-case forms) resolve to 1M instead of the 128K `deepseek` catch-all. Older DeepSeek ids keep 128K, legacy `deepseek-chat` / `deepseek-reasoner` stay at 1M, and endpoint-scoped overrides (NVIDIA NIM's 262,144) and live/cached metadata still take precedence.

## File: `agent/relay_runtime.py`

Feature: Thread-aware subscriber flush.
Intent: `close_session()` is synchronous but the gateway calls it on its event-loop thread, where nemo-relay's `subscribers.flush()` raises `RuntimeError`. `_flush_subscribers()` keeps the blocking `flush()` on loop-free threads and on a loop thread schedules `flush_async()` as a task the runtime holds a reference to, logging failures with the session id and skipping bindings without `flush_async` (DEBUG log). On a loop thread the barrier is best effort: `close_session()` returns before the flush completes. `shutdown()` runs one blocking `flush()` after closing remaining sessions whenever no loop runs on the calling thread (cancelled deferred tasks are dropped by nemo-relay's bridge); on a loop thread it never blocks. The gateway exits via `os._exit`, skipping `atexit`, so delivery there depends on the awaits in `_finalize_shutdown_agents`.

## File: `plugins/observability/nemo_relay/__init__.py`

Feature: Thread-aware subscriber flush on plugin configuration release.
Intent: `_ProcessPluginConfiguration.release()` can run on an event-loop thread, where `flush()` raises. `_flush_relay_subscribers()` follows the `RelayRuntime` rule — blocking `flush()` without a running loop, otherwise a `flush_async()` task whose failure a done callback logs — and the activation close still runs.
