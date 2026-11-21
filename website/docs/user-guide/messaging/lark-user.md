# Lark (personal auto-reply)

The `lark_user` platform answers **your own** Lark/Feishu messages for you: direct messages sent to
you and group messages that @mention you. It does not use a bot. It polls through the local
[`lark-cli`](https://github.com/larksuite/cli) with your **user** identity, starts a Hermes session per
conversation, and replies from your own account with a fixed AI-disclosure prefix.

Only a turn's **final answer** is ever sent: tool progress, interim commentary and error notices
never reach the other person. By default every reply is held for your review: a review card with
**Approve**, **Revise** (with a notes box; the agent rewrites and the new draft comes back for
review) and **Reject** (no reply). Nothing goes to the other person until you approve it.

> Use the [Feishu / Lark](./feishu.md) platform when you want a bot that people talk to. Use
> `lark_user` when people message *you* and you want an assistant to draft or send your replies.

## Prerequisites

- `lark-cli` on `PATH`, logged in as you **for Hermes**: `HERMES_HOME=~/.hermes lark-cli auth login`,
  then check `HERMES_HOME=~/.hermes lark-cli whoami --as user` shows you. lark-cli keeps a separate
  `hermes` workspace (`~/.lark-cli/hermes/`) that it uses whenever a non-empty `HERMES_HOME` is in
  the environment, as it always is inside the gateway; a plain `lark-cli auth login` from a terminal
  lands in the default `local` workspace, which the plugin never reads. The two workspaces can hold
  different apps, so the same person shows a different appId and open_id in each: look up open_ids
  for `allow_from` with `HERMES_HOME` set too. The gateway log records the workspace and app the
  plugin uses (`lark_user: lark-cli workspace=… app=…`).
- The user token needs these scopes: `search:message`, `im:message.p2p_msg:get_as_user`,
  `im:message.group_msg:get_as_user`, `im:message.send_as_user`, `im:message`.

- For review cards, a **review app**: a Lark app used for nothing else (not the Feishu platform's
  app, not lark-cli's). Card clicks only reach an app's event connection, and an app with several
  connections hands each click to just one of them. In the developer console: enable the bot,
  grant `im:message:send_as_bot` and `im:message`, under *Events & Callbacks → Callback
  configuration* choose the long connection and add `card.action.trigger`, include yourself in
  the availability scope, publish. Put the credentials in `~/.hermes/.env`:
  `LARK_USER_INTERACTION_APP_ID=…` and `LARK_USER_INTERACTION_APP_SECRET=…`. Without a review
  app, review falls back to text commands.

The user token expires if it is not refreshed (about a week). When `lark-cli` rejects it, the
platform stops polling and reports `auth_required` in `hermes gateway status`; run
`HERMES_HOME=~/.hermes lark-cli auth login` and restart the gateway.

## Minimal setup

```yaml
platforms:
  lark_user:
    enabled: true
    allow_from: [ou_xxx, ou_yyy]      # who may get a reply in DMs (open_ids)
    group_allow_from: [oc_zzz]         # groups where an @mention gets a reply (chat_ids)
```

Restart the gateway. Your existing messages are treated as history: only messages that arrive
after the first poll are answered.

## How replies are released (review mode)

With `reply.delivery: review` (the default) every reply waits for you. `review.interactive`
picks how; unset, it is `card` when the review app is configured and `text` otherwise.

**Cards.** The review bot sends you, in its chat with you, a card per draft: the sender, the
original message, the draft, a notes box and three buttons.

| Button | Effect |
|---|---|
| ✅ Approve | Sends the reply under your name (prefix and suffix added); the card turns green |
| ✏️ Revise | Sends your notes and the draft back into the conversation's own session; the agent's rewrite arrives as a new card marked with its revision number. Notes are required |
| 🚫 Reject | No reply; the card turns grey |

Only a click on that draft's own card, in the review bot's chat with you, counts; a decided card
has no buttons, and a failed send keeps them so you can retry.

**Text commands** (no review app). The draft is posted to your chat with yourself; answer there:

| Command | Effect |
|---|---|
| `/send [n]` | Approve draft *n* (default: the newest) |
| `/drop [n]` | Reject draft *n* |
| `/revise n <notes>` | Have the agent rewrite draft *n* |
| `/edit n <text>` | Send `<text>` instead of draft *n* |
| `/list` | List pending drafts |

Drafts older than `review.ttl` expire. Set `reply.delivery: auto` (globally or per chat) to send
replies directly.

## Configuration reference

All keys live under `platforms.lark_user` in `config.yaml`.

```yaml
platforms:
  lark_user:
    enabled: true
    cli_bin: lark-cli            # name on PATH or an absolute path
    lark_cli_profile: ""         # lark-cli --profile within its "hermes" workspace (one Lark account per Hermes profile)

    # Who may trigger a reply
    dm_policy: allowlist         # allowlist | open | disabled
    allow_from: []               # DM senders (open_id) for allowlist
    group_policy: allowlist      # allowlist | open | disabled
    group_allow_from: []         # group chat_ids for allowlist
    allow_bots: false            # answer messages sent by bots
    allow_external: false        # answer senders from other tenants

    poll:
      interval: 15s              # minimum 5s; each poll is several API calls
      idle_interval: 60s         # used after idle_after empty polls in a row
      idle_after: 10
      jitter: 0.2
      active_hours: ""           # e.g. "19:00-09:00"; empty = always (wraps midnight)
      max_backoff: 10m
      sources:
        dm: { enabled: true }
        mention: { enabled: true, include_at_all: false }

    max_message_age: 10m         # older messages are never answered
    text_batch_delay_seconds: 2  # a sender's burst of messages becomes one turn
    rate_limit: { per_chat: 5 }  # replies per chat per minute (0 = unlimited)

    session:
      dm_scope: per_chat         # per_chat | per_message
      group_scope: per_chat_user # per_chat | per_chat_user | per_thread | per_message

    reply:
      delivery: review           # review | auto
      format: post               # text | post (Markdown) | card
      target: reply              # reply (quote the message) | thread | send (new message)
      at_sender: false           # @mention the sender in group replies
      prefix: "[🤖 AI 代答] "     # required, cannot be empty
      suffix: ""
      card: { title: "🤖 {user_name} 的助理" }

    review:
      interactive: ""            # card | text; empty = card when the review app is configured
      chat: ""                   # text mode: empty = your chat with yourself
      ttl: 24h

    context:                     # recent group messages given to the agent with an @mention
      enabled: false
      scope: thread              # recent | thread (adds the mention's topic)
      limit: 20                  # 1-50 chat messages
      hours: 24
      thread_limit: 20           # 0-50 topic messages
      msg_max_chars: 1000
      when: new_session          # new_session | always

    chats:                       # per-chat overrides (enabled, scopes, reply.*, context.*), keyed by chat_id
      oc_team: { group_scope: per_thread, reply: { delivery: auto }, context: { enabled: true } }
      oc_noisy: { enabled: false }
```

`prefix`, `suffix` and the card title accept `{user_name}`, `{sender_name}`, `{time}` and `{date}`.

### Session isolation

| Scope | One session per |
|---|---|
| DM `per_chat` | conversation with each person |
| DM `per_message` | message (no memory between messages) |
| group `per_chat` | group, shared by everyone who mentions you |
| group `per_chat_user` | person in each group (follows the gateway's `group_sessions_per_user`, on by default) |
| group `per_thread` | topic; a mention outside a topic starts its own |
| group `per_message` | mention |

### Group context

With `context.enabled`, an @mention reaches the agent with the group's recent messages (and, for
`scope: thread`, the topic's) in `<group_messages>` / `<thread_messages>` blocks before the
message. They are read as you, so any group you are in works. Each line is flattened and stripped
of `<`/`>`, so a message cannot close or forge a block.

### Open access

`dm_policy: open` or `group_policy: open` lets anyone who can message you get a reply under your
name. The gateway refuses to start with either unless `LARK_USER_ALLOW_ALL_USERS=true` (or
`GATEWAY_ALLOW_ALL_USERS=true`) is set in `.env`.

## Safety

- **Your own messages never start a turn**, including the replies sent for you, so the platform
  cannot answer itself. Messages starting with your prefix are ignored too.
- **@all is ignored** unless `include_at_all: true`.
- **Senders cannot run gateway commands**: `/new`, `/model` and the like arrive as plain text.
- **No live mentions from the agent**: `<at …>` tags in the agent's reply are defused, so a
  prompt-injected `@all` cannot page a group under your name.
- **No terminal by default**: the platform's default toolset (`hermes-lark_user`) drops `terminal`
  and process tools, because anyone who can DM you reaches this agent. Opt back in with
  `platform_toolsets.lark_user`.

## Limits

- Messages are found through Lark's search index, which lags about 15 seconds; with the default
  interval a reply starts 15–30 seconds after a message arrives.
- Replies are sent once the turn finishes; there is no streamed or in-progress output. Do not
  enable `display.platforms.lark_user.streaming`: streamed text is not the final answer and is dropped.
- Only text is read; images and files in incoming messages are not downloaded.

The plugin's own README (`plugins/platforms/lark_user/README.md`) explains every option in detail.
