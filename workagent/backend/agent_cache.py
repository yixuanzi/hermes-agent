"""Bounded LRU + idle-TTL cache for the registries that hold live agents.

Both fork surfaces keep one long-lived object per conversation so a follow-up
turn reuses its warm prompt prefix, its session approvals and its tool state:

* ``workagent.backend.a2a_service.executor`` — one ``AIAgent`` per A2A
  ``context_id``;
* ``aegis.backend.chat.service.ChatSessionManager`` — one
  ``ChatSessionActor`` (which owns an ``AIAgent``) per ``(owner, session_id)``.

Left unbounded those registries only ever grow, and each entry is expensive: an
LLM/httpx client pool, tool schemas, an MCP client and the live
``_session_messages`` transcript — tens of MB on a tool-heavy session.  The A2A
surface grows faster than "one entry per conversation", because a caller that
does not pass an explicit ``session_id`` gets a freshly generated context id
per delegation (``tools/a2a_delegate_tool._default_session_id``).

Eviction is safe on both surfaces because each reloads conversation history
from ``SessionDB`` at the start of every turn
(``agent_runtime.load_conversation_history``), so a dropped entry is rebuilt
for the same session id with the same conversation.  Only the warm prompt
prefix is lost.

Bounds are read from the same ``agent.agent_cache`` config block the gateway
uses (``gateway.agent_cache_pressure.resolve_agent_cache_bounds``), so one
setting governs every surface.  The memory-pressure valve that block also
configures stays gateway-only — this cache enforces the entry cap and the idle
TTL.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Hashable, Iterable
from dataclasses import dataclass
import logging
import time
from typing import Any

from hermes_cli import config as hermes_config


logger = logging.getLogger(__name__)


# Mirror gateway/run.py's module-level defaults so an operator who never wrote
# an ``agent.agent_cache`` block gets the same behaviour on every surface.
_DEFAULT_MAX_SIZE = 128
_DEFAULT_IDLE_TTL_SECS = 3600.0


def resolve_cache_bounds(config_module=hermes_config) -> tuple[int, float]:
    """Return ``(max_size, idle_ttl_secs)`` from ``agent.agent_cache``.

    Fails open to the module defaults: a broken config read must not leave a
    cache unbounded, which is the very failure this module exists to prevent.
    """
    try:
        from gateway.agent_cache_pressure import resolve_agent_cache_bounds

        bounds = resolve_agent_cache_bounds(config_module.load_config_readonly())
    except Exception as exc:
        logger.debug("Agent cache bounds config read failed: %s", exc)
        return _DEFAULT_MAX_SIZE, _DEFAULT_IDLE_TTL_SECS
    return (
        bounds.max_size or _DEFAULT_MAX_SIZE,
        bounds.idle_ttl_secs or _DEFAULT_IDLE_TTL_SECS,
    )


def release_agent_soft(agent: object) -> None:
    """Release an evicted agent's client pool and transcript.

    Deliberately soft, for the same reason the gateway's eviction path is
    (``GatewayRunner._release_evicted_agent_soft``): the conversation may come
    back at any time, and its terminal sandbox, browser daemon and tracked
    background processes are keyed by task id, so they must outlive this Python
    object.  What we do drop is what eviction exists to reclaim — the socket
    pool and the message list, both rebuilt on the next turn.

    Never raises: eviction is a best-effort memory reclaim, never a reason to
    fail the request that triggered it.
    """
    if agent is None:
        return
    try:
        release_clients = getattr(agent, "release_clients", None)
        if callable(release_clients):
            release_clients()
    except Exception as exc:
        logger.debug("Cached agent release_clients failed: %s", exc)
    for attribute, empty in (("_session_messages", []), ("_db_flush_scan_prefix", None)):
        if hasattr(agent, attribute):
            try:
                setattr(agent, attribute, empty)
            except Exception as exc:
                logger.debug("Cached agent %s reset failed: %s", attribute, exc)


@dataclass(slots=True)
class _Entry:
    value: Any
    last_used: float


class AgentCache:
    """LRU + idle-TTL cache of per-conversation entries.

    Not internally locked: every caller already serializes access under its own
    lock (the A2A executor's ``asyncio.Lock``, the Aegis manager's
    ``threading.Lock``), and a second lock here would only invite the two to
    disagree.  Evicted entries are *returned* rather than released in place so
    the caller can run the (socket-closing) teardown off its lock, and so each
    surface decides what "release" means for the object it stores.

    ``is_protected`` marks entries that must never be evicted regardless of age
    — a conversation with a turn in flight or a client still attached.  It
    fails closed: an entry whose protection cannot be evaluated is kept.
    """

    def __init__(
        self,
        *,
        max_size: int | None = None,
        idle_ttl_secs: float | None = None,
        is_protected: Callable[[Hashable, Any], bool] | None = None,
        config_module=hermes_config,
        time_fn: Callable[[], float] = time.monotonic,
    ):
        self._entries: OrderedDict[Hashable, _Entry] = OrderedDict()
        self._max_size = max_size
        self._idle_ttl_secs = idle_ttl_secs
        self._is_protected = is_protected
        self._config_module = config_module
        self._time_fn = time_fn

    # ── bounds ──────────────────────────────────────────────────────────
    def _resolve_bounds(self) -> None:
        """Resolve configured bounds once, lazily.

        Lazy rather than in ``__init__`` so constructing a cache never depends
        on a readable config, and so the bounds are read after the profile's
        ``HERMES_HOME`` is bound.
        """
        if self._max_size is not None and self._idle_ttl_secs is not None:
            return
        max_size, idle_ttl_secs = resolve_cache_bounds(self._config_module)
        if self._max_size is None:
            self._max_size = max_size
        if self._idle_ttl_secs is None:
            self._idle_ttl_secs = idle_ttl_secs

    @property
    def max_size(self) -> int:
        self._resolve_bounds()
        return int(self._max_size or _DEFAULT_MAX_SIZE)

    @property
    def idle_ttl_secs(self) -> float:
        self._resolve_bounds()
        return float(self._idle_ttl_secs or _DEFAULT_IDLE_TTL_SECS)

    # ── accessors ───────────────────────────────────────────────────────
    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, key: object) -> bool:
        return key in self._entries

    def keys(self) -> list[Hashable]:
        return list(self._entries)

    def items(self) -> list[tuple[Hashable, Any]]:
        return [(key, entry.value) for key, entry in self._entries.items()]

    def peek(self, key: Hashable) -> Any | None:
        """Return a cached entry without counting as a use.

        For read-only paths (cancellation, history export, lookups by id) that
        must not promote an otherwise-idle conversation out of the eviction
        queue.
        """
        entry = self._entries.get(key)
        return entry.value if entry is not None else None

    def touch(self, key: Hashable) -> None:
        """Mark a conversation as used *now* — called when its turn ends.

        Without this the idle TTL would count from the start of a turn, so a
        long turn could make its own entry evictable the moment it finishes.
        """
        entry = self._entries.get(key)
        if entry is None:
            return
        entry.last_used = self._time_fn()
        self._entries.move_to_end(key)

    # ── mutation ────────────────────────────────────────────────────────
    def get_or_create(
        self,
        key: Hashable,
        factory: Callable[[Hashable], Any],
        *,
        protected: Iterable[Hashable] = (),
    ) -> tuple[Any, list[tuple[Hashable, Any]]]:
        """Return ``(value, evicted)`` for ``key``, building the value if needed.

        ``protected`` names conversations with a turn in flight; together with
        the ``is_protected`` predicate they are never evicted, even over the
        cap — a running turn holds its agent's client pool open.  The cap can
        therefore be exceeded transiently, which is the correct trade: the
        alternative is tearing down sockets under a live request.
        """
        now = self._time_fn()
        entry = self._entries.get(key)
        if entry is None:
            entry = _Entry(value=factory(key), last_used=now)
            self._entries[key] = entry
        else:
            entry.last_used = now
        self._entries.move_to_end(key)

        protected_keys = set(protected)
        protected_keys.add(key)
        return entry.value, self._evict(now, protected_keys)

    def discard(self, key: Hashable) -> Any | None:
        """Drop one entry, returning its value so the caller can release it."""
        entry = self._entries.pop(key, None)
        return entry.value if entry is not None else None

    def clear(self) -> list[tuple[Hashable, Any]]:
        """Drop every entry, returning the values for the caller to release."""
        evicted = [(key, entry.value) for key, entry in self._entries.items()]
        self._entries.clear()
        return evicted

    def _protected(self, key: Hashable, entry: _Entry, protected: set[Hashable]) -> bool:
        if key in protected:
            return True
        if self._is_protected is None:
            return False
        try:
            return bool(self._is_protected(key, entry.value))
        except Exception as exc:
            # Fail closed: an entry we cannot classify is one we must not drop.
            logger.debug("Agent cache protection check failed for %s: %s", key, exc)
            return True

    def _evict(self, now: float, protected: set[Hashable]) -> list[tuple[Hashable, Any]]:
        evicted: list[tuple[Hashable, Any]] = []

        idle_ttl = self.idle_ttl_secs
        for candidate_key, entry in list(self._entries.items()):
            if self._protected(candidate_key, entry, protected):
                continue
            if now - entry.last_used > idle_ttl:
                self._entries.pop(candidate_key, None)
                evicted.append((candidate_key, entry.value))

        max_size = self.max_size
        while len(self._entries) > max_size:
            # OrderedDict order is LRU order, so the first unprotected entry is
            # the least recently used one we are allowed to shed.
            victim_key = next(
                (
                    candidate_key
                    for candidate_key, entry in self._entries.items()
                    if not self._protected(candidate_key, entry, protected)
                ),
                None,
            )
            if victim_key is None:
                # Everything left is in flight; stay over the cap rather than
                # evict a live conversation.
                break
            entry = self._entries.pop(victim_key)
            evicted.append((victim_key, entry.value))

        if evicted:
            logger.info(
                "Agent cache evicted %d conversation(s) (size=%d, max=%d, idle_ttl=%ss): %s",
                len(evicted),
                len(self._entries),
                max_size,
                idle_ttl,
                ", ".join(str(candidate_key) for candidate_key, _value in evicted),
            )
        return evicted
