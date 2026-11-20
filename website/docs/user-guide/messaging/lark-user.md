# Lark (personal auto-reply)

The `lark_user` platform answers **your own** Lark/Feishu messages for you: direct messages sent to
you and group messages that @mention you. It does not use a bot. It polls through the local
[`lark-cli`](https://github.com/larksuite/cli) with your **user** identity, starts a Hermes session per
conversation, and replies from your own account with a fixed AI-disclosure prefix.

Only a turn's **final answer** is ever sent: tool progress, interim commentary and error notices
never reach the other person. By default every reply is held for your review: Hermes posts the draft to your own Lark chat (the
chat with yourself), and nothing goes to the other person until you release it.

> Use the [Feishu / Lark](./feishu.md) platform when you want a bot that people talk to. Use
> `lark_user` when people message *you* and you want an assistant to draft or send your replies.

## Prerequisites

- `lark-cli` on `PATH`, logged in as you: `lark-cli auth login`, then check
  `lark-cli auth status` shows the user identity as available.
- The user token needs these scopes: `search:message`, `im:message.p2p_msg:get_as_user`,
  `im:message.group_msg:get_as_user`, `im:message.send_as_user`, `im:message`.

The user token expires if it is not refreshed (about a week). When `lark-cli` rejects it, the
platform stops polling and reports `auth_required` in `hermes gateway status`; run
`lark-cli auth login` and restart the gateway.

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

With `reply.delivery: review` (the default), each reply arrives in your chat with yourself as
**📝 待审核回复 #n**, showing the sender, the original message and the draft. Send one of these
messages in that same chat:

| Command | Effect |
|---|---|
| `/send [n]` | Send draft *n* (default: the newest) |
| `/drop [n]` | Discard draft *n* |
| `/edit n <text>` | Send `<text>` instead of draft *n* |
| `/list` | List pending drafts |

Commands are only accepted from you, in your own chat. Drafts older than `review.ttl` are
discarded. Set `reply.delivery: auto` (globally or per chat) to send replies directly.

## Configuration reference

All keys live under `platforms.lark_user` in `config.yaml`.

```yaml
platforms:
  lark_user:
    enabled: true
    cli_bin: lark-cli            # name on PATH or an absolute path
    lark_cli_profile: ""         # lark-cli --profile (one Lark account per Hermes profile)

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
      chat: ""                   # empty = your chat with yourself
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
