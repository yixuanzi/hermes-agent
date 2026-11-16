"""Jev (TypeSafe System One) decision-layer defaults: the ``jev:`` config section and its env names.

Pure-data leaf for ``config_defaults`` / ``config`` (stdlib + ``config_defaults_env`` only).
``agent/jev_policy.py`` reads these settings env-first (``TYPESAFE_*`` / ``HERMES_JEV_*``), then
config.yaml's ``jev:`` section, then these defaults.
"""

from hermes_cli.config_defaults_env import _tool

# Typed, calibrated judgements made BEFORE the agent loop runs. Both features are off by default
# and both fail safe: with no TYPESAFE_API_KEY, no agent_description, no band model, or any
# transport error, every surface keeps its pre-Jev behavior. Each key can be overridden by the
# matching environment variable, which takes precedence (TYPESAFE_*, HERMES_JEV_*).
JEV_DEFAULTS = {
    # Endpoint + model for the decision calls. The API key is a secret and lives in .env as
    # TYPESAFE_API_KEY, never here.
    "base_url": "https://api.typesafe.ai",
    "model": "jev-latest",
    "timeout": 8.0,
    # Master switch for unaddressed group messages, checked BEFORE the two sub-switches below.
    # Off (default), there is no admission stage: Jev is never called, every message takes the
    # pre-Jev path, and the sub-switches do nothing on their own. On, an unaddressed group message
    # is answered ONLY with Jev's explicit blessing: a denial, an error, a category the
    # sub-switches do not cover, or a missing key all mean silence. The sub-switches narrow this;
    # they never widen it.
    "autoreply": False,
    # Answer group/channel messages that did not @-mention the bot, when Jev judges them this
    # agent's business. Requires agent_description, and does nothing unless autoreply is on.
    "channel_autoreply": False,
    # Extend that to messages inside an existing group topic/thread. Off by default: a thread is
    # usually a conversation the agent is already part of, and re-judging every follow-up would
    # cut one off mid-way. Under the master switch, off means a thread message is silenced; with
    # the master switch off it is simply left to the mention gate. The first message of a topic
    # is not a thread message and is always judged when autoreply + channel_autoreply are on.
    "thread_autoreply": False,
    # Classify the task as low/medium/high/other and route it to models.<band>. "other" means it
    # matched no band's criteria and keeps the agent's default model.
    "complexity_routing": False,
    # How often that classification is made:
    #   "session" (default) — rate the first turn of a session and reuse that band for the rest
    #     of it: one decision, one model, and the conversation's prompt cache survives.
    #   "turn" — rate every turn, at the cost of a decision per message and a possible
    #     mid-conversation model switch.
    "complexity_scope": "session",
    # Plain-language description of what this agent is and does. Used by BOTH decisions: channel
    # admission judges relevance against it (channel autoreply stays off while it is empty), and
    # the complexity question rates the work as this agent would do it.
    "agent_description": "",
    # Specialist agents this one can delegate to: a plain string, a {name: capability} map, or a
    # list of names / {name, description} entries. Admission counts a request one of them handles
    # as this agent's business too. HERMES_JEV_REMOTE_AGENTS overrides it with a plain string.
    # Complexity ignores it.
    "remote_agents": {},
    # A channel message is answered when Jev's in-scope probability reaches this value; raise it
    # to make the bot more reticent.
    "relevance_threshold": 0.7,
    # What each band MEANS. A band left empty uses the built-in default (DEFAULT_COMPLEXITY_CRITERIA
    # in agent/jev_policy.py), which describes a general assistant. A request matching none of the
    # three is answered "other" and keeps the default model, so narrow criteria narrow what gets
    # re-routed rather than mis-routing it. HERMES_JEV_CRITERIA_<BAND> overrides the entry.
    "criteria": {"low": "", "medium": "", "high": ""},
    # Per-band model and reasoning effort. A band left empty keeps the agent's default model and
    # effort. Either "model-name" or {model: ..., provider: ..., reasoning_effort: ...}; every
    # field is optional, and a band with only reasoning_effort keeps the session's model and
    # changes only how hard it thinks (agent.reasoning_effort vocabulary). It outranks
    # agent.reasoning_overrides, a session /reasoning still outranks it, and each provider clamps
    # a level its model does not accept. HERMES_JEV_MODEL_<BAND> / HERMES_JEV_PROVIDER_<BAND> /
    # HERMES_JEV_REASONING_<BAND> override these per FIELD.
    "models": {"low": "", "medium": "", "high": ""},
}

# The decision layer's only secrets; declared so `hermes setup` and the dashboard can offer them.
JEV_OPTIONAL_ENV_VARS = {
    "TYPESAFE_API_KEY": _tool(
        "TypeSafe Jev key — typed, calibrated decisions used for channel admission and "
        "complexity-based model routing",
        "TypeSafe (Jev) API key", "https://typesafe.ai/", advanced=True),
    "TYPESAFE_BASE_URL": _tool(
        "TypeSafe Jev endpoint override (self-hosted or proxied System One gateway)",
        "TypeSafe base URL (leave empty for the default)", None, password=False, advanced=True),
}

_BANDS = ("LOW", "MEDIUM", "HIGH")

# The env face of the ``jev:`` section: known to .env reload and doctor, but kept out of the
# global-env allowlist in agent/secret_scope.py so one multiplexing gateway can serve different
# agent descriptions and band models per profile. HERMES_JEV_BUSINESS_SCOPE (renamed to
# agent_description) stays unknown on purpose, so doctor reports it.
JEV_ENV_KEYS = frozenset({
    "TYPESAFE_MODEL", "HERMES_JEV_TIMEOUT",
    "HERMES_JEV_AUTOREPLY", "HERMES_JEV_CHANNEL_AUTOREPLY", "HERMES_JEV_THREAD_AUTOREPLY",
    "HERMES_JEV_COMPLEXITY_ROUTING", "HERMES_JEV_COMPLEXITY_SCOPE",
    "HERMES_JEV_AGENT_DESCRIPTION", "HERMES_JEV_REMOTE_AGENTS", "HERMES_JEV_RELEVANCE_THRESHOLD",
    *(f"HERMES_JEV_{field}_{band}" for field in ("MODEL", "PROVIDER", "REASONING", "CRITERIA") for band in _BANDS),
})
