# Jev decision layer

Two judgements that happen **before** the agent loop runs, both backed by
[TypeSafe Jev](https://typesafe.ai/) ("System One"): a typed-decision model that
answers propositions and ordinal ratings in a single forward pass and returns
*calibrated* probabilities instead of prose.

| # | Feature | Flag | Surfaces |
|---|---------|------|----------|
| 1 | **Channel admission** — answer a group message that did not `@`-mention the bot, when it falls inside the agent's business scope | `HERMES_JEV_CHANNEL_AUTOREPLY` | Feishu/Lark gateway, WORKAGENT A2A service |
| 2 | **Complexity routing** — rate each turn low/medium/high and run it on that band's model | `HERMES_JEV_COMPLEXITY_ROUTING` | Gateway (all platforms), WORKAGENT A2A service |

Both are **off by default** and independent — enabling one does not enable the
other. Both **fail safe**: with no API key, no business scope, no band model, a
low-confidence answer, or any transport error, every surface keeps exactly the
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
HERMES_JEV_CHANNEL_AUTOREPLY=true
HERMES_JEV_BUSINESS_SCOPE="Security operations: alert triage, vulnerability and asset management, incident response, policy and compliance questions."
HERMES_JEV_RELEVANCE_THRESHOLD=0.7

# Feature 2
HERMES_JEV_COMPLEXITY_ROUTING=true
HERMES_JEV_COMPLEXITY_SCOPE=session      # session (default) | turn
HERMES_JEV_MODEL_LOW=gpt-5-mini
HERMES_JEV_MODEL_MEDIUM=gpt-5
HERMES_JEV_MODEL_HIGH=claude-opus-5
HERMES_JEV_PROVIDER_HIGH=anthropic        # optional, per band
HERMES_JEV_MIN_CONFIDENCE=0.5
```

```yaml
# ~/.hermes/config.yaml — same knobs, lower precedence
jev:
  base_url: https://api.typesafe.ai
  model: jev-latest
  timeout: 8.0
  channel_autoreply: false
  complexity_routing: false
  complexity_scope: session      # session | turn
  business_scope: ""
  relevance_threshold: 0.7
  min_confidence: 0.5
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

### When the gate runs

The gate is **not** tied to the mention-drop path. It judges an unaddressed
message whether the mention gate was about to drop it or the group admits
unaddressed messages outright:

| `require_mention` | `HERMES_JEV_CHANNEL_AUTOREPLY` | @-mentioned | outcome |
|---|---|---|---|
| `true` | off | no | dropped |
| `true` | off | yes | answered |
| `true` | **on** | no | **judged** |
| `true` | on | yes | answered |
| `false` | off | no | answered |
| `false` | off | yes | answered |
| `false` | **on** | no | **judged** |
| `false` | on | yes | answered |

The `require_mention: false` + autoreply `on` row is the one that matters: that
configuration answers *every* message in the group, so it is where a
business-scope filter is worth the most. Turning the feature on changes only the
two unmentioned rows — a mentioned message behaves identically either way, and
every `off` row keeps its pre-Jev behavior exactly.

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
    "business_scope": "网络安全运营：告警研判、漏洞与资产管理、应急响应流程、安全策略与合规咨询。",
    "message": "生产环境 WAF 刚刚报了一批 SQL 注入告警，有人看一下吗",
    "channel": "安全运营大群",
    "sender": "张三"
  },
  "questions": {
    "in_scope": {
      "type": "noul",
      "instructions": "`business_scope` lists what the assistant is responsible for. `message` was posted in a group chat the assistant is a member of. Is `message` about a topic covered by `business_scope`?"
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
| `business_scope` | **yes** | `HERMES_JEV_BUSINESS_SCOPE` / `jev.business_scope`, verbatim | The *only* definition of "our business". Both questions reference it by name, so its wording is the real tuning surface — see below. |
| `message` | **yes** | the inbound text | What is being judged. |
| `channel` | no — omitted when empty | Feishu: chat display name, falling back to `chat_id`. A2A: `<source>.channel` | Lets the model read the room. "有人看一下吗" in `安全运营大群` reads differently than in a random group. |
| `sender` | no — omitted when empty | Feishu: resolved display name. A2A: `<source>.uname` | Supports the "directed at a specific named person" clause in question 2. |

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
admit = in_scope.probability >= relevance_threshold \
    and wants_answer.probability >= relevance_threshold
```

A `noul` answer is a bare probability — Jev returns no separate `confidence`
field for it, because for a proposition the probability *is* the confidence:

```json
{"answers": {"in_scope":     {"type": "noul", "noul": 0.98},
             "wants_answer": {"type": "noul", "noul": 0.97}}}
```

**Both** must clear the bar, and one threshold governs both. If either answer is
missing from the response the verdict is `None` (undecided) — not a yes.

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

Jev rates the request on an ordinal `low / medium / high` scale and the turn runs
on that band's model. A band with no configured model, or an answer below
`min_confidence`, keeps the agent's default model.

### `/model` wins

A session that pinned its own model with `/model` is **not** rated at all — no
Jev call is made, and the turn runs on the model the user chose. An automatic
classifier does not overrule an explicit human choice, and silently doing so
would make `/model` look broken.

```
no /model set        ->  [Jev] complexity: high in 1067ms (api) — confidence=1.00 >= 0.50
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

### How often the rating happens

`HERMES_JEV_COMPLEXITY_SCOPE` / `jev.complexity_scope`:

| value | behavior | cost |
|---|---|---|
| **`session`** (default) | Rate the first turn of a session that yields a decision, reuse that band for every later turn of the same session. | One decision per conversation. One model throughout — the prompt cache survives. |
| `turn` | Rate every turn. | One decision per message. The model can change mid-conversation, which rebuilds the agent each time it does. |

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
single low-confidence rating must not pin a whole session to the default model,
so the next turn tries again. Only a decided band is stored.

The **band** is remembered, not the model — re-pointing `HERMES_JEV_MODEL_HIGH`
at a different model takes effect on the next turn of an existing session. Bands
are held in a bounded in-process memo (512 sessions, 24h TTL), so a gateway
restart re-rates; `jev_policy.forget_session_band(session_id)` drops one
explicitly.

### The exact request

Built by `judge_complexity()` + `_COMPLEXITY_QUESTION` in `agent/jev_policy.py`.
Deliberately a **`score`**, not a `choice`: complexity is ordinal, and `score`
returns an ordered distribution plus a legend, so "probably medium, maybe high"
is expressible. A `choice` would flatten that into unrelated categories.

```json
{
  "model": "jev-latest",
  "state": {
    "request": "重新设计整个零信任接入架构，并给出分阶段迁移方案"
  },
  "questions": {
    "complexity": {
      "type": "score",
      "instructions": "How much reasoning depth and how many steps does it take to fully answer `request`? low = a greeting, lookup, or single short factual answer. medium = a focused task needing a few steps, a tool call, or a short piece of code. high = multi-step work needing planning, deep analysis, cross-referencing, or a substantial code change.",
      "criteria": ["low", "medium", "high"]
    }
  }
}
```

#### `state` fields

| key | source | why it is there |
|---|---|---|
| `request` | the turn's user message | The **only** field. No business scope, no channel, no sender. |

That minimalism is deliberate. Complexity is a property of the *task*, not of
who asked or where — feeding it scope or channel would make identical requests
band differently across rooms, and the band decides which model runs.

`criteria` is `COMPLEXITY_TIERS`, and the band names are **also the rung labels
the question defines**. Renaming a tier silently rewrites the question, which is
why the constant carries a "keep them stable" note.

#### What `request` actually contains

The turn message **as the gateway assembled it**, which for Feishu/Slack turns
includes the `<source>{…}</source>` identity envelope and any channel-context or
sender prefixes prepended before the agent runs — `_apply_jev_complexity_route`
is called with `ctx.message`, after that assembly.

Measured: prefixing the same three requests with a realistic
`<source>{"platform":"feishu","channel":"oc_ops","uid":"ou_abc","uname":"张三"}</source>`
changed no band and moved confidence by ≤0.02 (`low` 1.00→0.98, `medium`
0.81→0.80, `high` 1.00→1.00). The envelope is short and reads as metadata, so it
does not distort the rating — but it is the honest answer to "what text is
being rated".

### How a band is picked

Response:

```json
{"answers": {"complexity": {
  "type": "score",
  "score": 2,
  "legend": {"0": "low", "1": "medium", "2": "high"},
  "probabilities": {"0": 0, "1": 0, "2": 1},
  "confidence": 1
}}}
```

Three steps, in `agent/jev_client.py::_parse_answer` then `judge_complexity`:

1. **argmax of `probabilities`**, mapped back through `legend` — *not* the
   rounded `score`. A 0.5/0.5 split between `low` and `high` has an expected
   value of 1.0, which would round to `medium` — a band with **zero**
   probability mass. `score` is used only as a fallback when the service returns
   no distribution at all.
2. **confidence gate**: `confidence < min_confidence` (default `0.5`) → return
   `None` → the turn keeps the session's model. This is what stops a marginal
   rating from moving a conversation onto another model.
3. **band → model**: `HERMES_JEV_MODEL_{LOW,MEDIUM,HIGH}` plus the optional
   `HERMES_JEV_PROVIDER_{LOW,MEDIUM,HIGH}` (or `jev.models.*`) — see
   *Band configuration* above. An unconfigured band is also `None` → default
   model. So you can configure `HIGH` alone and leave everything else on the
   session model.

### Measured bandings

Live, against the configured endpoint:

One measured run, `min_confidence` 0.5:

| request | band | confidence | effect |
|---|---|---|---|
| 谢谢 | `low` | 1.00 | → `HERMES_JEV_MODEL_LOW` |
| 今晚吃什么？ | `low` | 0.75 | → `HERMES_JEV_MODEL_LOW` |
| 把 users 表加个 email 唯一索引 | `medium` | 0.81 | → `HERMES_JEV_MODEL_MEDIUM` |
| 帮我把这份周报排版一下 | `medium` | 0.68 | → `HERMES_JEV_MODEL_MEDIUM` |
| 重新设计整个零信任接入架构，并给出分阶段迁移方案 | `high` | 1.00 | → `HERMES_JEV_MODEL_HIGH` |
| 生产环境 WAF 报了一批 SQL 注入告警，有人看一下吗 | `high` | **0.22** | **default model** — below `min_confidence` |

The last row is the gate doing its job: an under-specified alert could be a
one-line "yes that's a false positive" or a full investigation, Jev says so with
a low confidence, and the turn declines to move off the session model rather
than guessing.

### Numbers here are one run, not constants

Jev's probabilities move a few points between runs on identical input — the
complexity rows above drifted 0.75↔0.79, 0.78↔0.81, 0.22↔0.26 across two
measurements; the chosen *band* did not change. Two consequences:

- Treat every figure in this document as illustrative of the **separation**
  between cases, not as a reproducible constant.
- A case sitting within a few points of `relevance_threshold` or
  `min_confidence` **will flap** between runs. Set thresholds with margin; if a
  particular message type matters, measure it rather than reasoning from these
  tables.

### Tuning

`min_confidence` (default `0.5`) is the only knob, and it trades routing
coverage against stability:

- **raise it** (0.7+) — fewer turns get banded, more run on the session model;
  use this if you see model churn mid-conversation
- **lower it** (0.2–0.3) — nearly every turn gets banded; only sensible when all
  three bands are configured and the models are close in behavior

The question text itself is a code constant, not configuration. The rung
definitions ("low = a greeting, lookup, or single short factual answer…") are
generic on purpose — if your workload needs domain-specific rungs, that is a
change to `_COMPLEXITY_QUESTION`, and the band names must stay `low`/`medium`/
`high` because `HERMES_JEV_MODEL_*` and `jev.models.*` key off them.

### Prompt-caching note

In the gateway, the route's signature feeds the agent-cache key, so switching
band rebuilds the `AIAgent` — the same cache boundary a `/model` switch crosses.
That is intentional: a cached agent must not be reused against a different model.

Under the default `complexity_scope: session` a conversation crosses that
boundary **at most once** — the band is decided on the first turn and reused, so
there is no mid-conversation switch to invalidate the cache. This is the main
reason `session` is the default rather than `turn`.

Under `complexity_scope: turn` every message can re-route and therefore rebuild.
`min_confidence` is the brake there: it keeps a marginal rating from moving the
turn at all. Raise it if you see churn; the fallback is always the session's own
model.

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
INFO  [Jev] complexity: high in 1077ms (api) — confidence=1.00 >= 0.50
INFO  [Jev] complexity: high reused for this session (scope=session) -> claude-opus-5
INFO  [Jev] complexity: high REJECTED in 871ms (api) — confidence=0.27 < 0.50, keeping the default model
WARN  [Jev] channel admission: UNDECIDED in 15ms (api) — ConnectError; caller keeps its default
WARN  [Jev] complexity: UNDECIDED in 6ms (api) — ConnectError; keeping the default model
```

| level | when | why that level |
|---|---|---|
| `INFO` | a decision was reached — `ANSWER` / `STAY QUIET`, a band, a band `REJECTED` for low confidence, or a band `reused` from the session | This is the record of what the layer did. `STAY QUIET` is logged as loudly as `ANSWER`, because a silent bot is exactly what an operator comes to the log to explain. A `reused` line has no latency figure: nothing was asked. |
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
| `agent/jev_client.py` | transport + the `noul` / `choice` / `score` primitives, `_parse_answer` (legend/argmax normalization), TTL+LRU decision memo |
| `agent/jev_policy.py` | `_SCOPE_QUESTION`, `_WANTS_ANSWER_QUESTION`, `_COMPLEXITY_QUESTION`, `_relevance_state`, settings resolution, feature gates, band → model |
| `gateway/run.py` | `_apply_jev_complexity_route` on the turn route |
| `plugins/platforms/feishu/adapter.py` | `_admit` → `group_mention_missing`, the gate, the forced topic reply |
| `workagent/backend/a2a_service/executor.py` | both gates on the A2A turn |

## Limits worth knowing

- Jev is a **decision** surface, not a generator. It does not replace the
  conversational model; it chooses which one runs and whether one runs at all.
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
  ≈ 395 / 19 tokens, $0.0000166; **0.9–1.3s** each through a proxy (see
  *Measured latency* above). A channel message with both features on costs two
  calls — the two states differ (`business_scope` + `message` vs. `request`), so
  they cannot share a request, and keeping them separate is what lets each
  feature be toggled on its own.
- The decision memo in `JevClient` is keyed on the exact `(model, endpoint,
  state, question)` tuple with a 120s TTL. It coalesces retried deliveries and
  repeated identical text; it is **not** shared between the two features,
  because their states differ.
