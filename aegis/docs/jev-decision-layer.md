# Jev decision layer

Two judgements that happen **before** the agent loop runs, both backed by
[TypeSafe Jev](https://typesafe.ai/) ("System One"): a typed-decision model that
answers propositions, categorical choices and ordinal ratings in a single
forward pass and returns *calibrated* probabilities instead of prose.

| # | Feature | Flag | Surfaces |
|---|---------|------|----------|
| 1 | **Channel admission** — answer a group message that did not `@`-mention the bot, when it is the agent's business | `HERMES_JEV_CHANNEL_AUTOREPLY` (+ `HERMES_JEV_THREAD_AUTOREPLY` inside topics; `HERMES_JEV_AUTOREPLY` is the master switch for the stage — **gateway only**) | Feishu/Lark gateway, WORKAGENT A2A service |
| 2 | **Complexity routing** — classify each turn low/medium/high/`other` against configurable criteria and run it on that band's model (`other` → the default model) | `HERMES_JEV_COMPLEXITY_ROUTING` (+ `HERMES_JEV_CRITERIA_{LOW,MEDIUM,HIGH}` to define the bands) | Gateway (all platforms), WORKAGENT A2A service |

Both are **off by default** and independent — enabling one does not enable the
other. Both **fail safe**: with no API key, no business scope, no band model, a
unreadable answer, or any transport error, every surface keeps exactly the
behavior it had before Jev existed.

## Configuration

Every setting resolves **env var → `config.yaml` `jev:` → default**. Only
`TYPESAFE_API_KEY` is a secret; the rest are settings whose canonical home is
`config.yaml`, mirrored as env vars because they are commonly pinned per
deployment.

```bash
# ~/.hermes/.env  — secret + endpoint
TYPESAFE_API_KEY=vk-…
TYPESAFE_BASE_URL=https://api.typesafe.ai      # with or without a trailing /v1
TYPESAFE_MODEL=jev-latest
HERMES_JEV_TIMEOUT=8

# Feature 1
HERMES_JEV_AUTOREPLY=true               # false (default) = no admission stage at all
HERMES_JEV_CHANNEL_AUTOREPLY=true       # inert unless AUTOREPLY is on
HERMES_JEV_THREAD_AUTOREPLY=false       # also judge inside topics
HERMES_JEV_BUSINESS_SCOPE="Security operations: alert triage, vulnerability and asset management, incident response, policy and compliance questions."
HERMES_JEV_RELEVANCE_THRESHOLD=0.7

# Feature 2
HERMES_JEV_COMPLEXITY_ROUTING=true
HERMES_JEV_COMPLEXITY_SCOPE=session      # session (default) | turn
# What each band MEANS. Empty = the built-in generic criterion.
HERMES_JEV_CRITERIA_LOW="A greeting, or one lookup answered from a single tool call."
HERMES_JEV_CRITERIA_MEDIUM="Triaging one alert, one query, or a short script."
HERMES_JEV_CRITERIA_HIGH="An incident investigation, a fleet-wide assessment, or an architecture plan."
HERMES_JEV_MODEL_LOW=gpt-5-mini
HERMES_JEV_MODEL_MEDIUM=gpt-5
HERMES_JEV_MODEL_HIGH=claude-opus-5
HERMES_JEV_PROVIDER_HIGH=anthropic        # optional, per band

HERMES_JEV_AGENT_DESCRIPTION="Hermes security-operations assistant for a SOC team."
```

```yaml
# ~/.hermes/config.yaml — same knobs, lower precedence
jev:
  base_url: https://api.typesafe.ai
  model: jev-latest
  timeout: 8.0
  autoreply: false               # true = the gate is mandatory
  channel_autoreply: false
  thread_autoreply: false        # also judge inside topics
  complexity_routing: false
  complexity_scope: session      # session | turn
  business_scope: ""
  agent_description: ""    
  remote_agents: ""
  relevance_threshold: 0.7
  criteria:                      # "" = the built-in generic criterion
    low: ""
    medium: ""
    high: ""
  models:
    low: ""
    medium: ""
    high: { model: big-model, provider: anthropic }   # provider is optional
```

These keys are **profile-scoped**, not global: a multiplexing gateway can serve
two business scopes and two sets of band models from one process.

### Band configuration

The two surfaces express the same thing, so they merge **per field** rather than
one replacing the other. Each band takes its `model` and `provider` from
`jev.models.<band>`, and `HERMES_JEV_MODEL_<BAND>` / `HERMES_JEV_PROVIDER_<BAND>`
override only the field they name:

| `jev.models.low` | `HERMES_JEV_MODEL_LOW` | `HERMES_JEV_PROVIDER_LOW` | result |
|---|---|---|---|
| `{model: cfg-low, provider: anthropic}` | — | — | `cfg-low` @ `anthropic` |
| `{model: cfg-low, provider: anthropic}` | `env-low` | — | `env-low` @ **`anthropic`** |
| `{model: cfg-low, provider: anthropic}` | — | `openai` | `cfg-low` @ `openai` |
| `{model: cfg-low, provider: anthropic}` | `env-low` | `openai` | `env-low` @ `openai` |
| `cfg-medium` (shorthand) | — | `xai` | `cfg-medium` @ `xai` |
| — | — | `openai` | **ignored**, with a warning — a provider without a model cannot route |

The second row is the one that matters: setting only the model env var keeps the
provider from `config.yaml` instead of silently discarding it.

`provider` is optional everywhere. Leave it unset for the common case — a band
that runs on the session's own provider and only swaps the model.

## Feature 1 — channel admission

Every **unmentioned group/channel message from a human** reaches the gate —
including when `require_mention` is off and the message would have been answered
anyway. Everything else is untouched:

- a DM, or a message that *does* `@`-mention the bot → normal flow, no Jev call
- a sender the group policy already rejects → still rejected
- a **bot** sender → still dropped (an unprompted reply invites a bot-to-bot loop)
- a **slash command** → still dropped (an unaddressed `/reset` typed at another
  bot must never reach this agent's dispatch)

### The master switch

`HERMES_JEV_AUTOREPLY` / `jev.autoreply`, **default off**, is the on/off switch
for this whole stage, and it is checked **before** the sub-switches:

| | `HERMES_JEV_AUTOREPLY=false` (default) | `=true` |
|---|---|---|
| `CHANNEL_AUTOREPLY` on, top-level message | **normal path — Jev is never called** | judged |
| in a topic, `THREAD_AUTOREPLY` on | **normal path — Jev is never called** | judged |
| `CHANNEL_AUTOREPLY` off | **normal path** | stay quiet |
| in a topic, `THREAD_AUTOREPLY` off | **normal path** | stay quiet |
| no API key / no `agent_description` | **normal path** | stay quiet |
| gate says **yes** | — | answer |
| gate says **no** | — | stay quiet |
| gate errors / undecided | — | stay quiet |

With the master switch off there is no admission stage at all: every message
takes the pre-Jev path and the mention gate alone decides it. The sub-switches
are inert — not "partially honoured". Asking Jev for a verdict nobody acts on
would cost a second of latency and an API call per group message, so the check
short-circuits before the request is built.

With it on, an unaddressed group message is answered **only** with Jev's
explicit blessing. The sub-switches then select which categories are *eligible*
to be judged; every other outcome is silence, including a sub-switch simply
being off. So the sub-switches never widen the master switch — they only narrow
it.

That asymmetry is the point: under `require_mention: false` the "normal path"
means *answer everything*, so a config mistake — an expired key, an emptied
`agent_description` — would silently turn the bot into an unfiltered responder.
With the master switch on, the same mistake makes it quiet instead.

**DMs and `@`-mentioned messages are never affected either way.** Neither are
bot senders or slash commands, which the gate has always excluded.

The decision is resolved in one place, `jev_policy.channel_admission_mode()`,
which returns `judge` / `silence` / `normal`. Callers do not read the flags
separately — the ordering is easy to re-derive wrongly.

**Gateway only.** The master switch does not apply to the WORKAGENT A2A
service. A2A is request/response — the caller blocks on a reply — and nothing
in the delegate envelope even carries the `chat_type` / `mentioned` fields that
surface's gate keys on, so its gate is inert for every real caller today.
Letting a flag described as governing group chat flip an RPC service from
"answer" to "refuse" would change a contract it was never described as
touching. A2A keeps its own rule: anything short of an explicit "no" is
answered.

### Messages inside a topic

A message already inside a group topic/thread needs a second opt-in,
`HERMES_JEV_THREAD_AUTOREPLY` / `jev.thread_autoreply`, **default off**. A
thread is usually a conversation the agent is already part of, and re-judging
every follow-up would cut one off mid-way.

With `HERMES_JEV_AUTOREPLY` and `HERMES_JEV_CHANNEL_AUTOREPLY` both on:

| message | `THREAD_AUTOREPLY` off (default) | on |
|---|---|---|
| top-level group message | **judged** | **judged** |
| inside a topic (`thread_id` or `root_id` set) | **not answered** | **judged** |

The topic row is the master switch at work: a category the gate cannot vouch
for is silence, not a free pass. With `HERMES_JEV_AUTOREPLY` off the whole
stage is skipped and that message is left to the mention gate instead —
dropped under `require_mention: true`, answered under `require_mention: false`.

The **first** message of a topic carries neither `thread_id` nor `root_id` (the
bot creates the topic from its own reply), so it is a top-level message and is
judged whenever the stage is on. This sub-switch cannot open the gate on its
own — `HERMES_JEV_CHANNEL_AUTOREPLY` has to be on too.

### When the gate runs

The gate is **not** tied to the mention-drop path. It judges an unaddressed
message whether the mention gate was about to drop it or the group admits
unaddressed messages outright. With `HERMES_JEV_AUTOREPLY=true`:

| `require_mention` | `HERMES_JEV_CHANNEL_AUTOREPLY` | @-mentioned | outcome |
|---|---|---|---|
| `true` | off | no | dropped |
| `true` | off | yes | answered |
| `true` | **on** | no | **judged** |
| `true` | on | yes | answered |
| `false` | off | no | not answered |
| `false` | off | yes | answered |
| `false` | **on** | no | **judged** |
| `false` | on | yes | answered |

With `HERMES_JEV_AUTOREPLY=false` this table collapses: nothing is judged and
nothing is silenced, so `require_mention` alone decides every row.

The `require_mention: false` + autoreply `on` row is the one that matters: that
configuration answers *every* message in the group, so it is where a
business-scope filter is worth the most. The channel sub-switch changes only
the two unmentioned rows — a mentioned message behaves identically either way,
which is the whole carve-out: this stage is about UNADDRESSED messages.

The flag is read *before* `_mentions_self()`, which can parse a post payload, so
a disabled feature adds nothing to the hot path.

### The exact request

Built by `_relevance_state()` + `_SCOPE_QUESTION` / `_WANTS_ANSWER_QUESTION` in
`agent/jev_policy.py`. Both propositions ride in **one** call — Jev answers all
questions in a single forward pass, so the second costs no extra round trip.

```json
{
  "model": "jev-latest",
  "state": {
    "agent": "Hermes 安全运营助手，服务于 SOC 团队：做告警研判、查资产与漏洞、答应急响应流程与合规问题。",
    "message": "谁能帮忙扫一下这个网段的漏洞？",
    "remote_agents": "avgc: 漏洞扫描与资产清点\naegis: 安全策略与合规问答",
    "channel": "安全运营大群",
    "sender": "张三"
  },
  "questions": {
    "in_scope": {
      "type": "noul",
      "instructions": "`agent` describes an assistant that is a member of this group chat. `message` was posted in that chat. Is `message` about something that assistant handles?"
    },
    "delegatable": {
      "type": "noul",
      "instructions": "`remote_agents` lists specialist agents that the assistant can hand work to. `message` was posted in a group chat. Is `message` about something one of `remote_agents` handles?"
    },
    "wants_answer": {
      "type": "noul",
      "instructions": "`message` was posted in a group chat. Is it asking for help, information, or action from whoever can provide it? Answer false for statements, acknowledgements, status updates, small talk, and messages clearly directed at a specific named person."
    }
  }
}
```

#### `state` fields

| key | required | source | why it is there |
|---|---|---|---|
| `agent` | **yes** | `HERMES_JEV_AGENT_DESCRIPTION` / `jev.agent_description`, verbatim | What this assistant is. Relevance is judged against it, so its wording is the real tuning surface. The same key feeds the complexity question. |
| `message` | **yes** | the inbound text | What is being judged. |
| `remote_agents` | no — omitted when empty | `HERMES_JEV_REMOTE_AGENTS` / `jev.remote_agents` | Specialists this agent can delegate to. A request one of them handles is this agent's business too — it can hand the work off. Adds the `delegatable` question. |
| `channel` | no — omitted when empty | Feishu: chat display name, falling back to `chat_id`. A2A: `<source>.channel` | Lets the model read the room. "有人看一下吗" in `安全运营大群` reads differently than in a random group. |
| `sender` | no — omitted when empty | Feishu: resolved display name. A2A: `<source>.uname` | Supports the "directed at a specific named person" clause. |

`jev.business_scope` was the earlier name for the `agent` source. It is still
read when `agent_description` is empty, so an existing deployment keeps its gate
on upgrade; prefer the new key, which both decisions share.

Note what is **not** here: anything about how the request will be *executed*.
Admission asks only whether the request is this agent's business; how much work
it is belongs to the complexity question.

Empty optional keys are **dropped, not sent as `""`** — an empty string is a
value the model would try to interpret.

#### What `message` actually contains (Feishu)

Not the raw payload. By the time the gate runs, `text` has been through:

1. `_extract_message_content()` — text/post/media normalization
2. `_strip_edge_self_mentions()` — leading `@Bot` removed (irrelevant here; a
   message that mentioned us never reaches this gate)
3. `_build_mention_hint()` prepended — so `@李四 你看下这个` arrives with the
   mention of **another** person visible, which is what lets question 2 answer
   `false` for messages aimed at someone specific

On the WORKAGENT A2A service `message` is `user_input` **including the
`<source>{…}</source>` prefix**, because the executor deliberately keeps that
prefix so the downstream LLM sees the original text. The classifier therefore
also sees it. Harmless in practice — it reads as metadata — but it is why an
A2A probability may differ slightly from the same text sent through Feishu.

### The decision

```python
ours  = in_scope.probability >= relevance_threshold \
    or (delegatable is not None and delegatable.probability >= relevance_threshold)
admit = ours and wants_answer.probability >= relevance_threshold
```

A `noul` answer is a bare probability — Jev returns no separate `confidence`
field for it, because for a proposition the probability *is* the confidence:

```json
{"answers": {"in_scope":     {"type": "noul", "noul": 0.98},
             "wants_answer": {"type": "noul", "noul": 0.97}}}
```

**Both** must clear the bar, and one threshold governs both. If either answer is
missing from the response the verdict is `None` (undecided) — not a yes.

### Delegation is its own question

Adding `remote_agents` by widening the scope question — "something the assistant
handles itself **or** can delegate to one of `remote_agents`" — was measured and
rejected. The disjunction makes the model hedge and the threshold stops
discriminating:

| message | scope question alone | folded "handles or delegates" | split into two questions |
|---|---|---|---|
| 好的收到，谢谢 | 0.14 | 0.50 | 0.17 / deleg 0.10 |
| 帮我把这份周报排版一下 | 0.13 | **0.62** | 0.15 / deleg 0.08 |
| 生产环境 WAF 报了一批 SQL 注入告警 | 0.97 | **0.77** | 0.95 / deleg 0.85 |
| 有谁知道 P1 事件要多久上报？ | 0.96 | **0.81** | 0.96 / deleg 0.82 |

Folded, the gap collapses from ~0.85 to ~0.15 and an off-topic request reaches
0.62 — close enough to a 0.7 threshold to be one wording change away from firing.
Split, both propositions stay crisp and the verdict ORs them. Jev answers all
three in one forward pass, so the extra question costs no extra round trip.

This is the same lesson as the scope / wants-answer split below, found twice.

#### What delegation buys

With a deliberately narrow agent (`只负责告警研判`) and two remote specialists
(`avgc: 漏洞扫描与资产清点`, `aegis: 安全策略与合规问答`):

| message | without `remote_agents` | with `remote_agents` |
|---|---|---|
| 这条告警是不是误报？ | scope 0.95 → **answer** | scope 0.95, deleg 0.41 → **answer** |
| 谁能帮忙扫一下这个网段的漏洞？ | scope 0.10 → quiet | scope 0.07, **deleg 0.97 → answer** |
| 等保三级对日志留存有什么要求？ | scope 0.18 → quiet | scope 0.12, **deleg 0.90 → answer** |
| 今晚吃什么？ | scope 0.02 → quiet | scope 0.02, deleg 0.02 → quiet |

The agent's own work is unaffected (row 1 — delegation correctly reads low), the
two requests it would hand off are now admitted, and off-topic stays out.

### Why two questions and not one

Folded into a single "is this in scope AND should you answer it" question, both
signals collapse toward the middle and the threshold stops discriminating.
Measured on the same security-ops scope, same messages:

(Numbers from the tuning run that produced the current wording; the split
columns there predate the measured run in the next section, which is why a
figure or two differs by a few points.)

| message | one combined question | split: in_scope / wants_answer |
|---|---|---|
| 今晚吃什么？ | 0.02 | 0.02 / 0.55 |
| 生产环境 WAF 报了一批 SQL 注入告警，有人看一下吗 | **0.64** | **0.98** / **0.97** |
| 这批告警是不是误报，怎么判？ | **0.69** | **0.98** / **0.97** |
| 帮我把这份周报排版一下 | 0.11 | 0.17 / 0.96 |

With one question a genuine in-scope alert scored 0.64 — below a 0.7 threshold,
so the bot would have stayed silent. Split, it is 0.98/0.97.

### Separation on the split questions

Scope = `网络安全运营：告警研判、漏洞与资产管理、应急响应流程、安全策略与合规咨询。`,
`channel` = `安全运营大群`, threshold 0.7. One measured run:

| message | in_scope | wants_answer | verdict |
|---|---|---|---|
| 今晚吃什么？ | 0.02 | 0.40 | quiet — off topic |
| 今天天气不错啊 | 0.02 | 0.04 | quiet |
| 好的收到，谢谢 | 0.11 | 0.03 | quiet — acknowledgement |
| 帮我把这份周报排版一下 | 0.18 | 0.96 | quiet — a real request, wrong domain |
| @李四 你昨天那个 PPT 发我一下 | 0.30 | 0.17 | quiet — aimed at a person |
| 刚才那批告警我已经处理完了，是误报 | **0.97** | 0.05 | quiet — in domain, but a statement |
| 生产环境 WAF 报了一批 SQL 注入告警，有人看一下吗 | **0.98** | **0.97** | **answer** |
| 有谁知道应急响应流程里 P1 事件要多久上报？ | **0.99** | **0.98** | **answer** |

Two rows carry the whole argument for the split. `刚才那批告警我已经处理完了` is
squarely our topic (0.97) but asks for nothing (0.05). `帮我把这份周报排版一下`
is a real request (0.96) in someone else's domain (0.18). A single combined
question would have to average those, and either question alone would admit one
of them.

### Tuning

`business_scope` is the highest-leverage knob — it is pasted verbatim into the
state and both questions resolve against it. Write it as an inventory of
responsibilities, not a mission statement:

> ✅ `Security operations: alert triage, vulnerability and asset management, incident response procedure, security policy and compliance questions.`
>
> ❌ `Helps the team stay secure.`

`relevance_threshold` (default `0.7`) moves both bars together. Raise it toward
0.9 to speak only when unmistakably addressed; the cost of a false negative here
is **silent**, so prefer raising it and letting people @-mention over lowering it
and having the bot interrupt.

`HERMES_JEV_BUSINESS_SCOPE` is required. Without it there is nothing to judge
relevance against, so the gate stays closed and unmentioned messages keep being
dropped — with a warning in the log.

**Feishu/Lark only** today: an admitted message is answered in a **topic under
the triggering message**, keyed on its `om_*` root. That topic reply is forced
even when `FEISHU_REPLY_THREAD=false`, because that switch expresses where an
*invited* answer goes, and an uninvited one belongs under the message that
prompted it. The event carries `metadata["jev_channel_autoreply"] = True`.

On the WORKAGENT A2A service the gate applies only when the caller's `<source>`
envelope says the request came from a channel (`chat_type` of
`group`/`channel`/`supergroup`) without a mention. A declined request completes
with a `SKIPPED: …` marker and `{"hermes": {"jev": {"in_scope": false}}}` so the
calling agent can tell "nothing to say" from an empty reply. A plain RPC call
carries no `chat_type` and is **never** gated — the caller is blocking on an
answer, and silently refusing it would strand them.

## Feature 2 — complexity routing

Jev picks one of `low` / `medium` / `high` / `other` for the request and the turn
runs on that band's model. Each band's meaning is configurable
(`HERMES_JEV_CRITERIA_<BAND>` / `jev.criteria.<band>`); `other` means the request
matched none of them. `other`, a band with no configured model, or an
unreadable answer all keep the agent's default model.

### `/model` wins

A session that pinned its own model with `/model` is **not** classified at all — no
Jev call is made, and the turn runs on the model the user chose. An automatic
classifier does not overrule an explicit human choice, and silently doing so
would make `/model` look broken.

```
no /model set        ->  [Jev] complexity: high in 1067ms (api) — confidence=1.00
                         Jev complexity routing: session-default -> claude-opus-5

/model claude-sonnet-5 ->  (no Jev call)
                         model stays claude-sonnet-5
```

The pin travels as a private `_session_model_pinned` marker that
`_resolve_session_agent_runtime` sets on both `/model` resolution paths (the
fast path where the override carries its own key, and the fall-through where it
does not). It is a marker, not a runtime field: `_resolve_turn_agent_config`
builds the agent's runtime from an explicit key list, so it never reaches
`AIAgent(**runtime)`.

This covers the session `/model` command only. A `channel_overrides.model` in
`config.yaml` is still subject to routing — see *Limits worth knowing*.

### How often the classification happens

`HERMES_JEV_COMPLEXITY_SCOPE` / `jev.complexity_scope`:

| value | behavior | cost |
|---|---|---|
| **`session`** (default) | Classify the first turn of a session that yields a decision, reuse that band for every later turn of the same session. A confident `other` counts as a decision and is reused too. | One decision per conversation. One model throughout — the prompt cache survives. |
| `turn` | Classify every turn. | One decision per message. The model can change mid-conversation, which rebuilds the agent each time it does. |

Measured on the same three-turn conversation, bands configured
`low=gpt-5-mini`, `medium=gpt-5`, `high=claude-opus-5`:

| turn | message | `scope=session` | `scope=turn` |
|---|---|---|---|
| 1 | 重新设计整个零信任接入架构，并给出分阶段迁移方案 | `high` in 878ms (api) → **claude-opus-5** | `high` in 817ms (api) → **claude-opus-5** |
| 2 | 谢谢 | `high` reused → **claude-opus-5** | `low` in 937ms (api) → **gpt-5-mini** |
| 3 | 这个方案什么时候能开始？ | `high` reused → **claude-opus-5** | `low` in 859ms (api) → **gpt-5-mini** |
| | | **1 API call, 1 model** | **3 API calls, 2 model switches** |

**The trade-off is real in both directions.** Under `session`, a conversation
that opens with "谢谢" is pinned to the LOW model even when the real task arrives
on turn 2 — the band is decided by the *first* message, which is usually but not
always the task statement. Under `turn`, follow-ups like "谢谢" and
"什么时候能开始？" correctly rate `low`, but the conversation bounces between
models and pays a decision every message.

`session` is the default because the first message of a session is normally the
task, and because not switching models mid-conversation is what keeps the prompt
cache intact. Choose `turn` when one session genuinely carries unrelated tasks of
different sizes.

#### What counts as a session

The **session id**, not the routing key — so `/new` or `/reset` mints a new id
and the next turn is rated fresh. On the WORKAGENT A2A service it is the A2A
context id, so a delegate conversation is rated once and its follow-ups inherit
the band. The gateway's one-shot background-task path has no session of its own
and is therefore always rated per turn, which is what a single-turn task means.

An **undecided** turn is deliberately not remembered: a transport error or a
transport error must not pin a whole session to the default model, so the next
turn tries again. `other` *is* remembered — it routes to the same default model,
but it is a decision rather than a failure, and re-asking it every turn would
buy nothing.

The **band** is remembered, not the model — re-pointing `HERMES_JEV_MODEL_HIGH`
at a different model takes effect on the next turn of an existing session. Bands
are held in a bounded in-process memo (512 sessions, 24h TTL), so a gateway
restart re-rates; `jev_policy.forget_session_band(session_id)` drops one
explicitly.

### The exact request

Built by `judge_complexity()` + `_COMPLEXITY_QUESTION` + `_complexity_criteria()`
in `agent/jev_policy.py`. A **`choice`** over four options, three of which carry
a criterion the deployment can replace.

It used to be a `score` over `["low","medium","high"]`, on the reasoning that
complexity is ordinal. Two things broke that:

- **A `score` has no way to say "none of these."** Every request had to land
  somewhere on the low→high line, so a request the bands did not describe was
  still routed by whichever band it was forced onto.
- **A `score`'s rungs are defined by the instructions, which are a code
  constant.** Once the rungs are configurable they belong in the options, and a
  `choice` is what carries a per-option description.

The ordinal signal is not actually lost: the answer still returns a full
distribution over the four options, so "probably medium, maybe high" is still
expressible and still logged.

```json
{
  "model": "jev-latest",
  "state": {
    "request": "查一下 8.8.8.8 的威胁情报",
    "agent": "Hermes security-operations assistant for a SOC team."
  },
  "questions": {
    "complexity": {
      "type": "choice",
      "instructions": "How much reasoning depth and how many steps does it take to fully answer `request`? Each option's criterion says when it applies. Pick `other` when the request fits none of them — do not stretch a criterion to cover it. Judge the work as it would be done by the assistant described in `agent`: a request that assistant answers directly is cheaper than one it has to reason out or compose from several steps.",
      "criteria": {
        "low": "A greeting, an acknowledgement, or a single short factual answer or lookup that needs no reasoning.",
        "medium": "A focused task needing a few steps, wiki maintenance, a brief analysis and comparison or information retrieval and summarization",
        "high": "Multi-step work needing planning, deep analysis, cross-referencing several sources, incident response or a substantial code change.",
        "other": "The request fits none of the other options — it is outside what they describe, not merely between two of them."
      }
    }
  }
}
```

The instructions **frame** the decision; they do not define the bands. That
separation is deliberate: the definitions are configurable, and instructions
that also defined them would contradict an override instead of being replaced
by it. A test asserts no default criterion string appears in the instructions.

#### `state` fields

| key | required | source | why it is there |
|---|---|---|---|
| `request` | **yes** | the turn's user message | What is being rated. |
| `agent` | no — omitted when empty | `HERMES_JEV_AGENT_DESCRIPTION` / `jev.agent_description` | Complexity is the cost of the task *as this agent would do it*. |

`request` and `agent` are the whole state; a test asserts that as a closed set.
No business scope, no channel, no sender. `agent` describes the *executor* and
is stable per agent, so it cannot make the same request band differently from
one room to the next — which is exactly why the channel and the sender stay out.

There used to be a third field, `tools`: a hand-maintained inventory of what the
agent could do. It is **gone**, along with `HERMES_JEV_TOOLS` and `jev.tools`.
It was a second copy of what `agent` already says, it never tracked the live
toolset (`platform_toolsets`, enabled plugins), and a band criterion — which is
configuration now — says the same thing better. See *What the context buys*.

The question is built to name **only the fields actually sent**: referencing a
state key the request does not carry is worse than sending no context at all.

##### What the context buys

Measured on the same endpoint, same requests, with and without a SOC agent
description:

| request | `request` only | `+agent` |
|---|---|---|
| 谢谢 | `low` (0.99) | `low` (0.96) |
| 查一下 8.8.8.8 的威胁情报 | `medium` (0.56) | `medium` (0.56) |
| 这台主机 web-prod-07 是谁负责的？ | `low` (0.87) | `low` (0.70) |
| 生产环境 WAF 报了一批 SQL 注入告警，有人看一下吗 | `high` (**0.27**) | **`medium` (0.51)** |
| 重新设计整个零信任接入架构，并给出分阶段迁移方案 | `high` (0.98) | `high` (0.98) |
| 今晚吃什么？ | `low` (0.52) | **`other` (0.80)** |

Row 4 is what `agent` buys: an under-specified alert goes from an unusable
`high` (0.27, a near-coin-flip) to a settled `medium`, because a SOC
assistant triaging an alert batch is a known shape of work. Row 6 is the cost —
see *`other` absorbs off-domain work* below.

##### The criterion replaces the tool inventory

The state used to carry a `tools` inventory too, and it did buy something: a
threat-intel lookup moved from `medium` (0.54) to `low` (0.77) when a
`threat_intel` tool was listed. That is gone with the field.

A **band criterion** buys the same thing and more, because it says directly what
the inventory only implied. Same agent description, default `low` criterion vs.
one that names the lookups this deployment actually does:

```bash
HERMES_JEV_CRITERIA_LOW="A greeting or acknowledgement, or a single lookup answered from one fact or one query: an IP/domain/hash reputation, an asset's owner, one alert's details."
```

| request | default `low` criterion | tuned `low` criterion |
|---|---|---|
| 查一下 8.8.8.8 的威胁情报 | `medium` (0.58) | **`low` (0.95)** |
| 这台主机 web-prod-07 是谁负责的？ | `low` (0.68) | **`low` (0.99)** |
| 谢谢 | `low` (0.95) | `low` (0.93) |
| 把 users 表加个 email 唯一索引 | `medium` (0.66) | `medium` (0.50) |
| 重新设计整个零信任接入架构，并给出分阶段迁移方案 | `high` (0.98) | `high` (0.92) |

The tuned criterion beats what the four-tool inventory achieved on both lookup
rows (0.95 and 0.99 against 0.77 and 0.64), and it does it without a second
hand-maintained list of the agent's capabilities to keep in sync. That is why
the inventory was removed rather than kept alongside: it was a worse version of
a lever that now exists, and one that silently mis-bands when it drifts from the
live toolset.

The option names come from `COMPLEXITY_TIERS` plus `COMPLEXITY_OTHER`, and they
are **also** the suffixes of `HERMES_JEV_MODEL_*`, `HERMES_JEV_PROVIDER_*` and
`HERMES_JEV_CRITERIA_*`. Renaming one rewrites the question and breaks the
config surface at the same time, which is why the constant carries a "keep them
stable" note.

#### What `request` actually contains

The turn message **as the gateway assembled it**, which for Feishu/Slack turns
includes the `<source>{…}</source>` identity envelope and any channel-context or
sender prefixes prepended before the agent runs — `_apply_jev_complexity_route`
is called with `ctx.message`, after that assembly.

Measured: prefixing the same three requests with a realistic
`<source>{"platform":"feishu","channel":"oc_ops","uid":"ou_abc","uname":"张三"}</source>`
changed no band and moved confidence by ≤0.08 (`low` 0.99→0.96, the ~0.5 index
case 0.51→0.43 with its band unchanged, `high` 0.99→0.99). The envelope is short
and reads as metadata, so it does not distort the rating — but it is the honest
answer to "what text is being rated".

### How a band is picked

Response:

```json
{"answers": {"complexity": {
  "type": "choice",
  "choice": "high",
  "probabilities": {"low": 0, "medium": 0, "high": 0.98, "other": 0.02},
  "confidence": 0.98
}}}
```

Two steps, in `agent/jev_client.py::_parse_answer` then `judge_complexity`:

1. **the returned `choice`**, or the argmax of `probabilities` when the service
   sends no explicit pick. An answer that is not one of the four options is
   treated as undecided rather than guessed at.
2. **band → model**: `HERMES_JEV_MODEL_{LOW,MEDIUM,HIGH}` plus the optional
   `HERMES_JEV_PROVIDER_{LOW,MEDIUM,HIGH}` (or `jev.models.*`) — see
   *Band configuration* above. An unconfigured band is also `None` → default
   model. So you can configure `HIGH` alone and leave everything else on the
   session model.

`other` returns the string `"other"`, not `None`. The routing result is the same
— the default model — but the distinction matters for the session memo: `other`
is **remembered** for the session, while an undecided turn is not. Otherwise a
session whose first turn falls outside every band would pay for a classification
on every subsequent turn to be told the same thing.

#### There is no confidence floor

There used to be one: `HERMES_JEV_MIN_CONFIDENCE` / `jev.min_confidence`,
default 0.5, below which the band was discarded and the turn kept the session's
model. It is **gone**, and it went out with `other`.

Its job was to catch "the model cannot tell". Under a `score` over three bare
rungs that was the only signal available — a thin, flat distribution was the
model's only way to express doubt. `other` is that signal now, and it is an
explicit one.

What was left of the floor was worse than nothing: it discarded **correct**
bands whose probability mass had merely split with the residual. Measured, a
report-layout request bands `medium` at 0.30 — the band is right, `other` is
just a plausible runner-up. Throwing that away routed the turn to the default
model for a reason that had nothing to do with the band being wrong. Having both
mechanisms also meant every criterion you wrote moved confidence, so the
threshold needed re-tuning each time.

The confidence is still on every log line. It is diagnostic now, not a gate:
a band that keeps coming back thin is a band whose criterion needs rewriting.

### What each band means

`HERMES_JEV_CRITERIA_{LOW,MEDIUM,HIGH}` / `jev.criteria.<band>`, merging the same
way the band models do: env overrides config per band, and a band left empty
falls back to the built-in criterion in `DEFAULT_COMPLEXITY_CRITERIA`. A blank
string is a fallback, not an empty description — sending an option with nothing
written against it strips exactly the calibration the answer is then gated on.

The defaults describe a **general assistant** and are phrased in terms of effort.
Override them when this agent's idea of "hard" differs: a fleet-wide scan is
routine for a SOC agent and a research project for a support bot.

`other` is **not** configurable. It is defined by the other three — "fits none of
them" — so a deployment describing it separately could only create overlap with
bands it also defined.

### Measured bandings

Live, against the configured endpoint:

One measured run, **default criteria and no `agent` context** — so nothing tells
Jev what this assistant is for:

| request | band | confidence | effect |
|---|---|---|---|
| 谢谢 | `low` | 0.99 | → `HERMES_JEV_MODEL_LOW` |
| 今晚吃什么？ | `low` | 0.52 | → `HERMES_JEV_MODEL_LOW` |
| 把 users 表加个 email 唯一索引 | `low` / `medium` | ~0.5 | **unstable — see below** |
| 帮我把这份周报排版一下 | `medium` | 0.51 | → `HERMES_JEV_MODEL_MEDIUM` |
| 重新设计整个零信任接入架构，并给出分阶段迁移方案 | `high` | 0.98 | → `HERMES_JEV_MODEL_HIGH` |
| 生产环境 WAF 报了一批 SQL 注入告警，有人看一下吗 | `high` | **0.27** | → `HERMES_JEV_MODEL_HIGH` |

Every row routes — there is no floor. The last one is the case worth watching:
an under-specified alert could be a one-line "false positive" or a full
investigation, and 0.27 says Jev knows that. It still runs on the `high` model.
If that is wrong for your deployment, the fix is a `medium` criterion that names
alert triage, not a threshold — see *Tuning*.

### `other` absorbs off-domain work once `agent` is configured

Same requests, same default criteria, with a SOC `agent` description added to
the state:

| request | no context | `+agent` |
|---|---|---|
| 谢谢 | `low` (0.99) | `low` (0.96) |
| 今晚吃什么？ | `low` (0.52) | **`other` (0.80)** |
| 把 users 表加个 email 唯一索引 | `medium` (0.83) | `medium` (0.66) |
| 帮我把这份周报排版一下 | `medium` (0.50) | `medium` (**0.30**) |
| 重新设计整个零信任接入架构，并给出分阶段迁移方案 | `high` (0.98) | `high` (0.98) |
| 生产环境 WAF 报了一批 SQL 注入告警，有人看一下吗 | `high` (**0.27**) | **`medium` (0.51)** |

**Read this before turning the feature on.** "今晚吃什么？" is a real request
that any generic reading bands as `low`, but it is not what a SOC assistant's
bands describe, so it becomes `other` and runs on the default model. This is the
feature behaving as specified — `other` means "matched none of the three
criteria", and the criteria are read in the context of `agent`. The consequence
is that **a narrow `agent_description` narrows what gets re-routed**, and a
deployment that wants off-domain work banded by effort anyway should widen the
criteria rather than fight the question.

Removing the `tools` inventory made this much less aggressive: with the
inventory in the state, rows 3 and 4 were `other` at 0.88 and 0.96 — a unique
index and a report layout, both perfectly ordinary `medium` work, pushed off the
routing table. With `agent` alone they stay `medium`.

Row 4 shows the residual still taking mass: `medium` survives but at 0.30, under
the gate, so that turn reaches the default model anyway — through an undecided
answer rather than an `other`.

Two wordings that push back against this were measured and **rejected**: telling
the model that "outside the subject area is not a reason to pick `other`" moved
those rows back into bands, but at 0.37–0.50 confidence, against a clean 0.80+
for `other`. That was measured while a confidence floor still existed, and under
it those rows reached the default model anyway; the wordings are still rejected
now, because splitting the mass three ways to avoid a legible `other` makes the
answer less readable for no change in routing.

A narrow criterion set has the mirror-image cost: with SOC-specific criteria
that never mention greetings, `谢谢` fell to `low` (0.44) with `other` at 0.42 —
under the gate. If you write your own criteria, make sure the cheap ambient
traffic your agent actually receives is described by one of them.

### Numbers here are one run, not constants

Jev's probabilities move a few points between runs on identical input, and at
~0.5 **the band moves too**. Measured on "把 users 表加个 email 唯一索引" with no
`agent` context: `medium` 0.83 in one session, then `low` 0.47–0.53 across five
consecutive calls in another. Five calls in a row agree with each other; runs
separated in time do not.

Settled cases do not do this — "谢谢" was `low` 0.98–0.99 and the zero-trust
redesign `high` 0.99 across every repeat. **The confidence figure is what tells
the two apart**, which is the main reason it is still logged now that it gates
nothing: a band that keeps printing ~0.5 is one whose criterion does not
separate your traffic.

Three consequences:

- Treat every figure in this document as illustrative of the **separation**
  between cases, not as a reproducible constant.
- A case sitting within a few points of `relevance_threshold` **will flap**
  between runs. Set it with margin; if a particular message type matters,
  measure it rather than reasoning from these tables.
- A complexity case sitting near ~0.5 will change **band** between runs, and
  with no confidence floor that now means it changes **model**. Under
  `complexity_scope: session` (the default) it is decided once per conversation,
  so the flap is between conversations rather than within one; under `turn` it
  is per message. Either way the fix is a criterion that claims the case
  outright.

### Tuning

**The criteria are the only knob**, and they are the right one: they say what
you actually mean, where a threshold could only say "act on fewer answers".

- a request type landing in the wrong band → name it in the right band's
  criterion
- a request type you would rather not re-route at all → leave it out of all
  three and let `other` take it
- a band that keeps coming back thin in the log → its criterion does not
  describe your traffic; rewrite it

Two knobs that no longer exist: `HERMES_JEV_MIN_CONFIDENCE` (see *There is no
confidence floor*) and `HERMES_JEV_TOOLS` (see *The criterion replaces the tool
inventory*). Both were removed in favour of criteria, which do the same jobs
directly.

The **band definitions are configuration**; the question's framing is still a
code constant, and the band names must stay `low`/`medium`/`high` because
`HERMES_JEV_MODEL_*`, `HERMES_JEV_CRITERIA_*` and `jev.models.*` /
`jev.criteria.*` all key off them.

Note that adding the fourth option costs a few points of confidence on
genuinely ambiguous requests, because `other` is a plausible competitor for
probability mass. That no longer changes any routing decision — it only makes
the log line look less settled.

### Prompt-caching note

In the gateway, the route's signature feeds the agent-cache key, so switching
band rebuilds the `AIAgent` — the same cache boundary a `/model` switch crosses.
That is intentional: a cached agent must not be reused against a different model.

Under the default `complexity_scope: session` a conversation crosses that
boundary **at most once** — the band is decided on the first turn and reused, so
there is no mid-conversation switch to invalidate the cache. This is the main
reason `session` is the default rather than `turn`.

Under `complexity_scope: turn` every message can re-route and therefore rebuild,
and with the confidence floor gone there is no longer a brake on that. If you
see churn, the fix is `complexity_scope: session` (the default) rather than a
threshold — `session` crosses the cache boundary at most once by construction,
where a threshold only made churn less frequent.

On the WORKAGENT A2A service the agent is long-lived, so the band is applied to
it in place. Its whole runtime — model, provider, credentials, `base_url`,
`api_mode` — is captured on first use, so a later unbanded turn (or turning the
feature off) restores the profile's runtime instead of leaving the agent
stranded on the last band.

Both kinds of band work there:

| band | what happens |
|---|---|
| no provider pinned | only `agent.model` moves; the session's credentials stay |
| pinned to the provider the agent is already on | same — only the model moves |
| pinned to a **different** provider | credentials are resolved for that provider and the agent is swapped onto them with `AIAgent.switch_model`, the supported in-place swap: it rebuilds the provider clients, refreshes the credential pool and caching flags, and **restores the previous runtime atomically if the rebuild raises** |

Undoing a same-provider band is a plain model assignment, not a client rebuild —
symmetric with how it was applied.

"Different provider" is decided against the agent's **`requested_provider`** (the
full id the profile asked for, e.g. `custom:glm`), not its `provider` (which a
runtime canonicalizes to the bare `custom` namespace and therefore cannot tell
two custom entries apart). A bare name and its `custom:` form are the same
provider; a lone `custom` identifies none.

Every failure keeps the agent exactly where it is: credentials that will not
resolve, a `switch_model` that raises, or an agent that has no `switch_model` at
all all leave the current runtime untouched and log a warning. A switch logs
both sides:

```
[Jev] switching this turn to gpt-5.6-sol on custom:chatai (was glm-5.3-flash on custom:glm)
```

## Logging

Every decision logs its outcome and latency on one line, prefix `[Jev]`, so
`grep '\[Jev\]' ~/.hermes/logs/gateway.log` is a complete audit of what the
layer decided and what it cost. Real output:

```
INFO  [Jev] channel admission: ANSWER in 1260ms (api) — in_scope=0.98 wants_answer=0.97 threshold=0.70 chat=安全运营大群
INFO  [Jev] channel admission: ANSWER in 0ms (cache) — in_scope=0.98 wants_answer=0.97 threshold=0.70 chat=安全运营大群
INFO  [Jev] channel admission: STAY QUIET in 1132ms (api) — in_scope=0.02 wants_answer=0.47 threshold=0.70 chat=安全运营大群
INFO  [Jev] complexity: high in 1077ms (api) — confidence=1.00
INFO  [Jev] complexity: high reused for this session (scope=session) -> claude-opus-5
INFO  [Jev] complexity: high in 871ms (api) — confidence=0.27
INFO  [Jev] complexity: OTHER in 964ms (api) — confidence=0.88, no band's criteria matched; keeping the default model
WARN  [Jev] channel admission: UNDECIDED in 15ms (api) — ConnectError; caller keeps its default
WARN  [Jev] complexity: UNDECIDED in 6ms (api) — ConnectError; keeping the default model
```

| level | when | why that level |
|---|---|---|
| `INFO` | a decision was reached — `ANSWER` / `STAY QUIET`, a band, `OTHER`, or a band `reused` from the session | This is the record of what the layer did. `STAY QUIET` is logged as loudly as `ANSWER`, because a silent bot is exactly what an operator comes to the log to explain. A `reused` line has no latency figure: nothing was asked. The `confidence=` figure on a band line gates nothing — it is there so a band that keeps coming back thin is visible as a criterion that needs rewriting. |
| `WARNING` | `UNDECIDED` — transport error, or an answer missing from the response | Something is wrong with the classifier, and the feature silently degraded. |
| `DEBUG` | skipped — empty text, or no client because `TYPESAFE_API_KEY` is unset | Ordinary and expected; would otherwise flood the log on every turn with the feature off. |

The `(api)` / `(cache)` / `(api+cache)` marker says whether the call went over
the wire. Without it a `0ms` line looks like a bug — it is a memo hit
(`JevClient`'s 120s TTL cache), which is also why a retried delivery does not
pay for the decision twice.

`WARNING` lines carry the exception class only. Full tracebacks are on the
client's `DEBUG` line (`[Jev] decision request error detail`), along with the
endpoint actually called:

```
DEBUG [Jev] 2/2 answers in 1260ms (api) via https://…/v1/systemone
WARN  [Jev] decision request failed (ConnectError) in 8ms via https://…/v1/systemone; caller falls back
```

### Measured latency

Through a proxied endpoint, **0.9–1.3s** per decision — not the 100–300ms
quoted for direct TypeSafe access. One `ReadTimeout` was observed at 8.5s
against the default 8s `HERMES_JEV_TIMEOUT`.

That matters in two places: it is added to **every** inbound turn while the
feature is on, and the 8s default has less headroom than it looks. Watch the
`in …ms` figures on your own endpoint before enabling this on a busy gateway,
and remember the degradation directions differ — a timed-out admission means
silence, a timed-out complexity call means the default model.

## Code map

| File | Role |
|---|---|
| `agent/jev_client.py` | transport + the `noul` / `choice` / `score` primitives (`choice` carries a per-option criterion), `_parse_answer` (legend/argmax normalization), TTL+LRU decision memo |
| `agent/jev_policy.py` | `_SCOPE_QUESTION`, `_WANTS_ANSWER_QUESTION`, `_COMPLEXITY_QUESTION`, `DEFAULT_COMPLEXITY_CRITERIA`, `_complexity_criteria`, `_relevance_state`, settings resolution, feature gates, band → model |
| `gateway/run.py` | `_apply_jev_complexity_route` on the turn route |
| `plugins/platforms/feishu/adapter.py` | `_admit` → `group_mention_missing`, the gate, the forced topic reply |
| `workagent/backend/a2a_service/executor.py` | both gates on the A2A turn |

## Limits worth knowing

- Jev is a **decision** surface, not a generator. It does not replace the
  conversational model; it chooses which one runs and whether one runs at all.
- `other` and a rejected band both end on the default model, so a deployment
  with narrow criteria routes less, not wrongly. The failure mode to watch for
  is the opposite one: criteria so broad that everything lands in `high`.
- Complexity routing yields to the session `/model` command, but **not** to a
  `channel_overrides.model` in `config.yaml` — a channel pinned to a model will
  still be re-routed by band. If that is wrong for your deployment, the same
  `_session_model_pinned` marker is where the exemption would go.
- Calibration is a statistical property, not per-answer correctness. A 0.9
  in-scope probability is right about 90% of the time — set thresholds for the
  cost of the mistake you care about, and remember that a false *negative* here
  is silent.
- Every decision is one extra HTTP round trip. Measured on the configured
  endpoint: admission ≈ 471 input / 39 output tokens, $0.0000198; complexity
  ≈ 534 / 47 tokens, $0.0000224; **0.9–1.3s** each through a proxy (see
  *Measured latency* above). Complexity got **more** expensive with the four
  described options, not less: it was ≈ 395 / 19 when it was a `score` over
  three bare rungs plus a tool inventory. The four criteria are ~140 input
  tokens, and a 4-way distribution is a longer answer than a 3-rung score. That
  is the price of the options being configurable and of `other` existing;
  writing *shorter* criteria is the lever if it matters. A channel message with both features on costs two
  calls — the two states differ (`agent` + `message` vs. `agent` + `request`), so
  they cannot share a request, and keeping them separate is what lets each
  feature be toggled on its own.
- The decision memo in `JevClient` is keyed on the exact `(model, endpoint,
  state, question)` tuple with a 120s TTL. It coalesces retried deliveries and
  repeated identical text; it is **not** shared between the two features,
  because their states differ.
