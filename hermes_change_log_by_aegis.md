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

Feature: Delegate agent name for platform presentation.
Intent: Carry the resolved `agent_name` onto the delegate session so a platform output adapter that renders a delegation as its own surface can title it. Presentation only: this attribute must never participate in routing, session identity, or authorization.

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

Feature: Slack SDK user display-name resolution.
Intent: Unwrap SlackResponse API payloads when resolving human display names and bot status so SessionSource.user_name carries the display name instead of the member ID; avoid poisoning the shared name cache when a bot probe receives an invalid response.

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

Feature: Feishu automatic topic creation and root-keyed session routing.
Intent: For top-level messages in regular groups and direct messages, use `FEISHU_REPLY_THREAD` (default `true`) to treat the `om_*` message ID as the prospective topic root and reply anchor, create the topic with `reply_in_thread=true`, and carry the root metadata through final, progress, approval, clarification, media, and error delivery. Reuse the root-keyed session when a later `omt_* + root_id` message has active or persisted routing, preserve existing forum and manually-created topic behavior, and perform at most one flat-message fallback when automatic topic creation fails.

Feature: Feishu automatic-topic lifecycle boundaries.
Intent: Keep AIAgent cache eviction independent from session continuity, while making the reset/prune boundary explicit: once an automatic root session has been ended by `session_reset` or its routing entry has been pruned after restart, a later topic message may create a new session and retain the real `omt_*` route rather than incorrectly binding an unknown human topic to the old root session. Keep this lifecycle behavior independent from the `FEISHU_REPLY_THREAD` topic-creation switch.

Feature: Feishu card output mode.
Intent: Behind opt-in `FEISHU_CARD_OUTPUT` (default false; yaml `feishu.card_output` takes precedence), intercept `send`, `edit_message`, and `delete_message` so one agent turn renders as one CardKit card instead of a fan of text/post bubbles, with the card bracketed to the turn by `on_processing_start` / `on_processing_complete`. Classify each send by outbound metadata: `hermes_progress` is execution chrome and belongs in the collapsible panel, anything else is the reply body. Only output produced inside a turn becomes a card, so a slash-command reply or cron push does not create a card whose panel is permanently empty, and any CardKit failure returns the route to the existing text/post path rather than costing the user a reply.

Feature: Feishu delegate card rendering.
Intent: Render each delegated remote agent into its own card keyed by A2A context ID, sealed on that delegate's turn-final event so a foreground loop produces one card per exchange rather than piling every follow-up answer into the first card. Accumulate streamed deltas synchronously before any await and serialize the open-or-extend decision per owner, because delegate events are scheduled as independent fire-and-forget tasks and would otherwise each open their own body block and fragment the head of the answer. Track whether an exchange streamed separately from the current text segment, so a tool boundary cannot make the turn-final event re-append text already on screen. Drop transport-level `status` events in card mode, since they describe the delegation machinery and the agent narrates the real outcome itself; keep `error` visible.

Feature: Feishu transient notice delivery.
Intent: Keep liveness signals and acknowledgements out of the answer card. `hermes_card_bypass` and the gateway's existing `non_conversational` marker both route a send to the plain text/post path, with `hermes_progress` taking precedence so tool chrome still reaches the panel. The delegate interaction-resolved acks and the expired-approval correction set the marker at their own call sites, so a button press cannot splice a notice into the middle of a streaming answer. A bypassed notice keeps a real Feishu message ID, so its own edit and delete paths continue to work.

Feature: Feishu card-mode attachment delivery.
Intent: A file is never card content, and card mode must not change which files get delivered. The gateway strips attachments out of a reply before the adapter sees it, but card output adds text entry points that skip that pass — mid-turn commentary and delegate output reach `send` directly — so anything they carry is extracted here instead of rendering as a literal path inside the card. Extraction reuses the gateway's own `extract_media` / `extract_local_files` and their delivery filters rather than a local pattern, so explicit `MEDIA:` tags and bare deliverable paths behave exactly as they do with card output off, and a path inside a code fence still ships nothing. Each file leaves as its own message through the existing per-type senders, and the card body keeps a one-line receipt naming it, which also guarantees the body is never blank. A per-turn ledger, claimed before the upload and released when it fails, is what keeps the two entry points — the gateway's own dispatch and this safety net — from delivering the same file twice when a send is retried with identical content; it is scoped to the turn so a later request to resend the same file still works, and it is inert whenever no card turn is live.

Feature: Feishu attachment delivery inside a topic.
Intent: A file the user asks for in a Feishu topic has to arrive. The normal send path keys a message in a topic on `receive_id_type=thread_id`, which Feishu accepts for text and cards but rejects for every attachment kind — audio, file, media, image, and a post carrying one — with a bare field-validation code, so the upload succeeded and the message that would have carried it was dropped. `_send_attachment_message` reads that code as "re-anchor", not "give up": it re-sends through the reply API anchored on a message inside the topic, which places the same upload there without complaint, and only if that also fails does it drop the topic and post flat in the chat, on the grounds that a file in the wrong place beats no file. This replaces a narrower workaround that covered only audio, and leaves every other failure code reported to the caller unretried so a real rejection is still visible.

Feature: Jev channel-admission gate for unmentioned group messages.
Intent: Decide whether an unaddressed group message is the agent's business before answering it. The gate is deliberately NOT tied to the mention-drop path: it judges any unmentioned human group message, whether the mention gate was about to drop it (`group_mention_missing`) or the group admits unaddressed messages outright (`require_mention: false`). Binding it to the drop path alone left `require_mention: false` — the configuration that answers every message in a group, and therefore the one that most needs a business-scope filter — completely unguarded. `_admit` keeps its "reject reason" contract and reports a mention miss as `group_mention_missing`, distinct from `group_policy_rejected`, so a sender the group policy rejected stays rejected either way. The drop is deferred until the text has been extracted (the admission step has none); the gate is skipped for a bot sender, where an unprompted reply invites a bot-to-bot loop, and for a slash command, where an unaddressed `/reset` typed at another bot must never reach this agent's dispatch. The feature flag is read before `_mentions_self`, which can parse a post payload, so a disabled gate costs nothing on the inbound path. The gate fails closed in every direction: feature off, no API key, no business scope, undecided verdict, or a raising classifier all reproduce the pre-Jev drop. An admitted message forces a topic reply on its own `om_*` root even when `FEISHU_REPLY_THREAD` is off, because that switch expresses where an *invited* answer goes and an uninvited one belongs under the message that prompted it; the event is marked `jev_channel_autoreply` so downstream code can tell the two apart.

## File: `plugins/platforms/feishu/feishu_cardkit.py`

Feature: CardKit three-element card engine.
Intent: Own the card lifecycle for Feishu card output: create the JSON 2.0 entity, deliver it as `msg_type=interactive` carrying `type=card` plus `card_id`, then mutate it in place — full-element replace for the collapsible execution trace, the streaming `content` endpoint for the rich-text body, and a panel patch plus settings patch to finalize. Keep `update_multi=true`, because JSON 2.0 supports shared cards only and the streaming endpoint refuses an exclusive card, and keep `sequence` strictly increasing per card.

Feature: Streaming body text contract.
Intent: Send the element's complete text to `PUT /elements/:id/content` as a plain string. A JSON wrapper such as `{"text": ...}` is accepted with code 0 and then rendered literally, so the wrapper braces and escaped newlines appear in the chat — a successful response code is not evidence of correct rendering. Keep each update a prefix superset of the previous one, which is what produces the native typewriter animation instead of a whole-element replace.

Feature: Card size budgeting.
Intent: Measure the 30 KB card limit in UTF-8 bytes, not characters: CJK text costs three bytes per character, so a character-based cap allowed a long Chinese answer to exceed the real limit and have the whole update rejected. Truncate on a character boundary, keep the body's head so its prefix stays stable for the typewriter, keep the trace's tail so recent steps survive, and roll an overflowing body onto a continuation card instead of silently clipping the answer.

Feature: Markdown fidelity for the Feishu renderer.
Intent: De-indent fenced code-block markers so a fence indented inside a list item still renders as code, and render each execution-trace step as an explicit list item because a single newline is a soft break the renderer may collapse. Leave the reply body unmodified: it is the agent's own markdown, where headings, lists, tables, and fences all depend on the exact line structure arriving intact.

Feature: Execution-trace step segmentation.
Intent: Treat one progress message as one step even when it spans several lines. The gateway renders a main-agent `terminal` call as a header line plus a fenced command block, so a line-by-line pass bulleted the command inside the code box and counted each fence line as its own step, inflating the step summary. Fold a single-line command onto its tool line as inline code — matching how the delegate adapter already renders a tool call — keep a genuine multi-line script as a block attached to that same step with its own lines untouched, and do not fold a headerless block (emitted for back-to-back terminal calls) onto the preceding command, because it is a separate call.

Feature: Card update resilience.
Intent: Re-queue a rejected element update instead of dropping it — bounded, so a permanent failure cannot spin — and re-open `streaming_mode` before retrying once when Feishu closes it after an idle period, so a turn that pauses on a slow tool does not lose the remainder of its answer. Serialize card creation per route so concurrent progress and content writers cannot each open a card for the same turn.

Feature: Card speaker and exchange boundaries.
Intent: Give each card exactly one owner and seal it when the owner changes, and expose an owner-scoped close for the case owner change cannot cover: a delegate foreground loop keeps one A2A context, and therefore one owner tag, across every turn.

Feature: Card block addressing.
Intent: Represent each send as an editable block within an area and return a synthetic `hermes-card:` handle rather than the real card message ID, so the gateway's send-then-edit streaming pattern maps onto card regions and the gateway can never reach the card through the `im` message API. Support retracting a block, so a superseded preview does not remain on screen beside its replacement.

## File: `plugins/platforms/feishu/plugin.yaml`

Feature: Feishu card output env contract.
Intent: Declare `FEISHU_CARD_OUTPUT`, the card title overrides and the attachment-receipt line as optional env so operators can discover and opt into card rendering and localize its user-visible strings, and state the default explicitly: card output is off unless enabled, and text/post delivery remains what an unconfigured deployment gets.

## File: `gateway/platforms/base.py`

Feature: Feishu automatic-topic reply anchors.
Intent: Distinguish an `om_*` prospective topic root from a real `omt_*` topic when selecting the outbound reply anchor, so a user message that quotes an earlier message creates its automatic topic under the current message while existing real-topic reply-context behavior remains unchanged.

## File: `agent/jev_client.py`

Feature: TypeSafe Jev (System One) decision client.
Intent: Give Hermes one typed, calibrated decision surface — `noul` / `choice` / `score` over `POST {base_url}/v1/systemone` — for judgements that happen BEFORE the agent loop and must not become a generation call. Normalize all three primitives to one shape so a `score` reports the band its probability mass actually sits in rather than the rounded expected value, and so `noul` reports its probability as its own confidence. Every failure path — no key, unreachable host, non-JSON body, missing or unparseable answer — returns `None` rather than raising, because a classifier that fails must never drop a user's message or strand a turn without a model. A short TTL+LRU memo keyed by the exact request coalesces repeat decisions (retried deliveries, the same text judged twice) without becoming durable state.

Feature: Per-call telemetry (`ask_detailed` / `JevCallStats`).
Intent: Report elapsed time, how many answers came from the memo, how many went over the wire, and the failure class, so the policy layer can state outcome AND latency on one operator-facing line. `ask` stays the simple contract and delegates. The cache/api marker exists because a memo hit legitimately reports ~0ms and would otherwise read as a bug in the log.

## File: `agent/jev_policy.py`

Feature: Jev channel-admission and complexity-routing policy.
Intent: Hold the two decisions and their configuration in one place so the gateway, the Feishu adapter and the WORKAGENT A2A executor share identical semantics instead of each inventing their own. Settings resolve env-var-first (`TYPESAFE_*`, `HERMES_JEV_*`), then `config.yaml`'s `jev:` section, then a default, and every read goes through `agent.secret_scope` so a multiplexing gateway cannot serve one profile's business scope or band models to another. Both features are off by default and gated on being *fully* configured — channel autoreply additionally requires a non-empty business scope, complexity routing at least one band model — so a half-configured deployment keeps its pre-Jev behavior rather than acting on a judgement it cannot make. Channel admission is two separate propositions ("is this our topic" and "does it want an answer") asked in one forward pass: folded into a single question both signals collapse toward the middle and the threshold stops meaning anything. A band below `min_confidence` is discarded, which is also what keeps a conversation from oscillating between models and throwing away the prompt cache every turn.

Feature: Complexity decision frequency (`complexity_scope`).
Intent: Let an operator choose whether a session is rated once or every turn, defaulting to `session` because the first message of a session is normally the task and because not switching models mid-conversation is what preserves the prompt cache. The per-session band lives in a bounded in-process memo keyed by the session **id** (not the routing key), so a reset mints a new id and re-rates; an undecided or low-confidence turn is deliberately NOT remembered, so one transport error cannot pin a whole session to the default model. The band is stored rather than the resolved model, so re-pointing a band at another model reaches existing sessions on their next turn. A caller with no session identity (the one-shot background-task path) degrades to per-turn rating, which is the correct reading of a single-turn task.

## File: `gateway/run.py`

Feature: Delegate runtime binding per gateway turn.
Intent: Bind adapter-provided delegate output and input factories onto both fresh and cached agents each turn, preventing Slack thread/user runtime state from leaking across cached sessions.

Feature: Slack / Feishu source identity envelope for agent-bound messages.
Intent: Prefix Slack and Feishu DM/channel/group messages that become agent-bound turns with compact structured metadata after gateway command handling and all inbound-context assembly, including Slack shared channels and threads whenever a trusted user ID is available. Map Feishu `chat_id` to the shared `channel` field, keep the user ID and optional display name on the first line, preserve Slack's human-readable shared-session participant prefix, and avoid affecting command parsing or other-platform attribution. This main-Agent envelope is intentionally distinct from the remote A2A envelope, which carries only parent platform/user identity and does not automatically receive the channel.

Feature: Feishu card output stream classification.
Intent: Mark Feishu tool-progress sends with `hermes_progress` and the long-running heartbeat with `hermes_card_bypass`, so an adapter that renders a whole turn as one card can tell execution chrome, the reply itself, and a transient status notice apart. Both markers are scoped to Feishu so no other platform's progress or status metadata changes shape, and neither is presentation state that reaches conversation history.

Feature: Plugin slash-command caller identity binding.
Intent: Bind the invoking user's userenv identity (from the adapter's `SessionSource`) around plugin-registered slash-command handlers for the duration of the call, mirroring the tool-call path in `agent/tool_executor.py`. Plugin command dispatch runs inside `_handle_message` before `_set_session_env` binds `HERMES_SESSION_*`, so without this binding identity-dependent plugin commands (e.g. `/userenv`) would see no caller and fail closed. Identity must always come from the gateway source, never from message text, and the ContextVar must be reset after the handler returns.

Feature: Jev complexity-based turn model routing.
Intent: Let a turn run on a model sized to the difficulty of the request. `_apply_jev_complexity_route` is a module-level function, not a method, because the turn-route builder is exercised with a stand-in `self` and must not gain instance-state requirements. It mutates the route in place INCLUDING the signature, since the agent-cache key is derived from it and a band switch has to rebuild the agent rather than reuse one bound to the previous model — the same cache boundary a `/model` switch crosses, which is why banding is confidence-gated rather than applied to every turn. A band pinned to another provider swaps credentials through the existing per-provider resolver; a band on the session's own provider only re-derives `api_mode` for the model being switched to. Every failure — routing off, no band, unresolvable credentials, classifier error, empty message — leaves the route exactly as the session resolved it. The turn's session **id** is threaded through `_resolve_turn_agent_config` so the policy layer can decide a band once per session under the default `complexity_scope: session`; under that scope a conversation crosses the agent-cache boundary at most once, which is the point of making it the default.

Feature: Cross-provider band switching on a long-lived agent.
Intent: A band pinned to another provider used to be skipped on the A2A surface on the grounds that credentials belong to the profile — but `AIAgent.switch_model` already exists for exactly this, and it swaps model, provider, credentials, `base_url` and `api_mode` atomically, restoring the previous runtime if the client rebuild raises. `apply_tier_to_agent` now resolves the band's provider and uses that swap, so all three bands work on a surface where only same-provider bands did. The captured baseline is the agent's whole runtime, not just its model, so an unbanded turn restores the provider too; undoing a same-provider band stays a plain model assignment rather than a client rebuild, which also keeps the helper usable on surfaces whose agent has no `switch_model`. Every failure path — unresolvable credentials, a raising swap, a missing `switch_model` — leaves the agent on its current runtime.

Feature: Provider-identity comparison for band routing.
Intent: A provider id exists at two granularities — the full id a profile requests (`custom:glm`) and the namespace a runtime canonicalizes it to (`custom`) — and comparing one against the other rejected a band pinned to the provider the agent was ALREADY running on. On the A2A surface that silently disabled every custom-provider band: the turn kept the profile model, so only the band that happened to match the default appeared to work. `_provider_ids_match` now reduces both sides to a comparable key, treating a bare name and its `custom:` form as one provider and treating a lone `custom` as identifying none (so an unverifiable provider keeps the profile model rather than guessing an endpoint). The comparison reads `requested_provider` first, and the skip warning names both sides so the mismatch is diagnosable from one log line.

Feature: Per-field band resolution across env and config.yaml.
Intent: `HERMES_JEV_MODEL_<BAND>` and `HERMES_JEV_PROVIDER_<BAND>` express exactly what `jev.models.<band>` expresses, so they must override it field by field rather than entry by entry. The earlier whole-entry precedence meant setting only the model env var silently discarded a provider configured in config.yaml — a misconfiguration with no diagnostic, since the band still resolved and simply ran on the wrong credentials. A band that ends up with a provider but no model is dropped with a warning instead of being half-applied, because a provider alone cannot route.

Feature: `/model` outranks complexity routing.
Intent: A model the user chose explicitly with `/model` must not be silently replaced by a classifier — that makes the command look broken. `_resolve_session_agent_runtime` marks the resolved runtime with a private `_session_model_pinned` flag on BOTH override paths (the fast path where the override carries its own api_key, and the fall-through where it does not), and the turn router returns before making any Jev call, so a pinned session also stops paying for a decision it would discard. The flag is deliberately a marker rather than a runtime field: `_resolve_turn_agent_config` builds the agent runtime from an explicit key list, so it can never reach `AIAgent(**runtime)`, and the other consumers of that dict either read known keys or filter by whitelist. Scope is the session command only; a `channel_overrides.model` still routes by band.

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

## File: `plugins/ext-tools/__init__.py`

Feature: Stateless extension host for tools and slash commands.
Intent: Register plugin-owned capabilities (the `cron_prompt` tool and the `/userenv` slash command) in one place so new stateless surface arrives as one module per capability without growing the Hermes core tool schema or command registry.

## File: `plugins/ext-tools/userenv_cmd.py`

Feature: `/userenv` self-service env slash command.
Intent: Let authenticated runtime users list, get, set, and delete only their own persisted env variables through the gateway command path, so secret values never enter the LLM conversation context. Identity comes from the userenv ContextVar bound by the gateway (never from message text), missing identity fails closed for data operations while help text stays available, `get` responses mask values to the first/last four characters, and storage reuses `tools/user_env_store.py` with `CURRENT_USER_NAME` remaining system-managed.

## File: `plugins/ext-tools/plugin.yaml`

Feature: ext-tools plugin manifest.
Intent: Declare the plugin's provided tools and commands (`cron_prompt` tool, `/userenv` command) so discovery surfaces what the plugin contributes without inspecting code.

## File: `hermes_cli/config_defaults.py`

Feature: Jev decision-layer configuration surface.
Intent: Declare the `jev:` section so the endpoint, decision model, feature flags, complexity scope, business scope, thresholds and per-band models are discoverable and documented in one place, with both features defaulting off. Register `TYPESAFE_API_KEY` as the only secret in the set (and `TYPESAFE_BASE_URL` beside it for self-hosted or proxied System One gateways), marked advanced so it does not crowd the ordinary setup flow. The remaining knobs are settings, not credentials, and their canonical home stays config.yaml even though each is also readable from the environment.

## File: `hermes_cli/config.py`

Feature: Jev environment-variable recognition.
Intent: Keep the `TYPESAFE_MODEL` / `HERMES_JEV_*` keys (including the per-band model and provider vars) known to `.env` reload and doctor so an operator who pins them per deployment is not warned about unknown variables. They are deliberately left out of the global-env allowlist in `agent/secret_scope.py`: a multiplexing gateway should be able to serve two business scopes and two sets of band models from one process, which requires these to stay profile-scoped.
