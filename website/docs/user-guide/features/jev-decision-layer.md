---
title: Jev Decision Layer
description: Use TypeSafe Jev's calibrated decisions to admit unaddressed group messages and to route tasks to a model by complexity.
sidebar_label: Jev Decision Layer
---

# Jev Decision Layer

Jev (TypeSafe System One) answers typed questions about a message in a single short call and returns
calibrated probabilities. Hermes uses it only **in front of** the agent loop, for two independent
features that are both **off by default**:

- **Channel admission** — answer group messages that did not @-mention the bot, but only when Jev
  judges them this agent's business, delegatable to one of its remote agents, or addressed to it by
  name. Feishu/Lark today.
- **Complexity routing** — classify a task as `low`, `medium`, `high` or `other` and run it on that
  band's model (and reasoning effort). `other` keeps the agent's default model.

Both fail safe: with no `TYPESAFE_API_KEY`, no `agent_description`, no band model, or any transport
error, every surface keeps its normal behavior.

## Setup

Put the key in `~/.hermes/.env` (it is the only secret):

```bash
TYPESAFE_API_KEY=...
# optional: self-hosted or proxied System One gateway
TYPESAFE_BASE_URL=
```

Everything else is configured in `config.yaml`:

```yaml
jev:
  base_url: https://api.typesafe.ai
  model: jev-latest
  timeout: 8.0
  # Master switch for unaddressed group messages. Off: no admission stage at all.
  # On: an unaddressed group message is answered only on Jev's explicit allow.
  autoreply: false
  # Judge top-level group messages that did not mention the bot (needs autoreply).
  channel_autoreply: false
  # Also judge messages inside an existing topic/thread (needs both switches above).
  thread_autoreply: false
  # Route each task to the model of its complexity band.
  complexity_routing: false
  # session: rate the first turn and keep that band (prompt cache intact); turn: rate every turn.
  complexity_scope: session
  # What this agent is and does. Required for channel admission; also used to rate complexity.
  agent_description: ""
  # Specialists this agent can delegate to, e.g. {aisoc: "SOC incident response"}.
  remote_agents: {}
  # In-scope probability needed to answer a channel message; raise it to be quieter.
  relevance_threshold: 0.7
  # What each band means; empty uses the built-in general-assistant criteria.
  criteria:
    low: ""
    medium: ""
    high: ""
  # Per-band model: "model-name" or {model, provider, reasoning_effort}. Empty keeps the default.
  models:
    low: ""
    medium: ""
    high: ""
```

So `jev.autoreply`, `jev.channel_autoreply` and `jev.thread_autoreply` gate admission;
`jev.complexity_routing`, `jev.complexity_scope`, `jev.criteria.<band>` and `jev.models.<band>`
drive routing; `jev.agent_description`, `jev.remote_agents` and `jev.relevance_threshold` describe
what counts as this agent's business; `jev.base_url`, `jev.model` and `jev.timeout` pick the
decision endpoint.

## Precedence

Each setting can also be pinned per deployment with an environment variable, which wins over
`config.yaml`: `TYPESAFE_BASE_URL`, `TYPESAFE_MODEL`, `HERMES_JEV_TIMEOUT`,
`HERMES_JEV_AUTOREPLY`, `HERMES_JEV_CHANNEL_AUTOREPLY`, `HERMES_JEV_THREAD_AUTOREPLY`,
`HERMES_JEV_COMPLEXITY_ROUTING`, `HERMES_JEV_COMPLEXITY_SCOPE`, `HERMES_JEV_AGENT_DESCRIPTION`,
`HERMES_JEV_REMOTE_AGENTS`, `HERMES_JEV_RELEVANCE_THRESHOLD`, and per band
`HERMES_JEV_MODEL_<BAND>`, `HERMES_JEV_PROVIDER_<BAND>`, `HERMES_JEV_REASONING_<BAND>`,
`HERMES_JEV_CRITERIA_<BAND>`. The per-band model/provider/effort variables override
`jev.models.<band>` field by field. These settings are profile-scoped: one multiplexing gateway can
serve a different agent description and band models per profile.

For reasoning effort the precedence is: a session's `/reasoning` > the band's effort >
`agent.reasoning_overrides` > `agent.reasoning_effort`. A model chosen with `/model` is never
replaced by a band.
