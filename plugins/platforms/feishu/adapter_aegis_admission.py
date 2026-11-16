"""Feishu Jev channel admission, recent group history and sender names (Aegis).

The Jev gate judges unaddressed human group messages and fails closed; group history is fetched
for the first turn of a new group session and carried on event metadata, never in the text."""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Optional

from gateway.platforms.base import (
    MessageType,
)
from tools import feishu_group_history as _group_history

logger = logging.getLogger("plugins.platforms.feishu.adapter")


def _fa():
    """The adapter module, read at call time (no import cycle; SDK names bind at connect)."""
    import sys

    return sys.modules["plugins.platforms.feishu.adapter"]

# Recent-group-history injection for the first turn of a new group session
# (FEISHU_GROUP_HISTORY).  Fetching and rendering live in
# tools/feishu_group_history.py; these aliases keep the adapter-side names.
_DEFAULT_GROUP_HISTORY_LIMIT = _group_history.DEFAULT_LIMIT


_DEFAULT_GROUP_HISTORY_HOURS = _group_history.DEFAULT_HOURS


_DEFAULT_GROUP_HISTORY_THREAD_LIMIT = _group_history.DEFAULT_THREAD_LIMIT


_FEISHU_GROUP_HISTORY_PAGE_SIZE_MAX = _group_history.PAGE_SIZE_MAX


_FEISHU_GROUP_HISTORY_MSG_MAX_CHARS = _group_history.DEFAULT_MSG_MAX_CHARS




_DEFAULT_GROUP_HISTORY_SOURCE = _group_history.SOURCE_API


_DEFAULT_GROUP_HISTORY_CLI_BIN = _group_history.DEFAULT_CLI_BIN


_DEFAULT_GROUP_HISTORY_CARD_REGIONS = _group_history.DEFAULT_CARD_REGIONS


def _coerce_positive_float(value: Any, default: float) -> float:
    """Coerce value to a strictly positive float, falling back to ``default``."""
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if parsed != parsed or parsed <= 0:  # NaN or non-positive
        return default
    return parsed


def _env_boolean_default_false(name: str) -> bool:
    """Parse a user-facing boolean environment flag that is off by default.

    Mirrors :func:`_env_boolean_default_true` so an operator can spell the
    value any of the usual ways.  Not expressible with ``_to_boolean``, which
    only accepts the literal string ``"true"`` — ``FEISHU_CARD_OUTPUT=1``
    would silently read as off.
    """
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return False
    normalized = raw.strip().lower()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    logger.warning("[Feishu] Invalid %s=%r; defaulting to false", name, raw)
    return False


class FeishuAdmissionMixin:
    """Mixed into ``FeishuAdapter`` ahead of ``BasePlatformAdapter``."""

    def _jev_settings(self):
        """Effective Jev settings, or ``None`` when the module is unavailable."""
        try:
            from agent import jev_policy

            return jev_policy.load_jev_settings()
        except Exception:
            logger.debug("[Feishu] Jev policy unavailable", exc_info=True)
            return None

    def _jev_admission_mode(self, *, in_thread: bool = False) -> str:
        """How to handle an unaddressed group message: judge / silence / normal.

        Resolved in one call so the master switch's inversion of the
        sub-switches lives in the policy layer — see
        ``jev_policy.channel_admission_mode``.
        """
        try:
            from agent import jev_policy
        except Exception:
            logger.debug("[Feishu] Jev policy unavailable", exc_info=True)
            return "normal"

        settings = self._jev_settings()
        if settings is None:
            # The policy module loaded but its settings did not resolve. Under
            # the master switch that is still a gate we cannot vouch for.
            return "normal"
        try:
            return jev_policy.channel_admission_mode(settings, in_thread=in_thread)
        except Exception:
            logger.debug("[Feishu] Jev admission mode check failed", exc_info=True)
            return jev_policy.SILENCE if settings.autoreply_gate else jev_policy.NORMAL

    async def _jev_message_in_scope(
        self, *, text: str, chat_name: str, sender_name: str,
    ) -> bool:
        """Ask Jev whether this unmentioned group message is ours to answer.

        Fails closed: an unconfigured, undecided, or unreachable Jev returns
        False, which reproduces the pre-Jev behavior of dropping the message.
        """
        settings = self._jev_settings()
        if settings is None:
            return False
        try:
            from agent import jev_policy

            verdict = await jev_policy.judge_channel_relevance_async(
                text,
                settings=settings,
                channel_name=chat_name or "",
                sender_name=sender_name or "",
            )
        except Exception:
            logger.warning("[Feishu] Jev relevance check raised; staying quiet", exc_info=True)
            return False
        return verdict is True

    def _group_history_settings(self) -> _group_history.GroupHistorySettings:
        """Snapshot the adapter's group-history knobs (defaults when unset)."""
        return _group_history.GroupHistorySettings(
            enabled=bool(getattr(self, "_group_history_enabled", False)),
            limit=max(1, int(getattr(self, "_group_history_limit", _DEFAULT_GROUP_HISTORY_LIMIT))),
            hours=float(getattr(self, "_group_history_hours", _DEFAULT_GROUP_HISTORY_HOURS)),
            thread_limit=max(
                0, int(getattr(self, "_group_history_thread_limit", _DEFAULT_GROUP_HISTORY_THREAD_LIMIT))
            ),
            msg_max_chars=max(
                0, int(getattr(self, "_group_history_msg_max_chars", _FEISHU_GROUP_HISTORY_MSG_MAX_CHARS))
            ),
            source=_group_history.normalize_history_source(
                getattr(self, "_group_history_source", _DEFAULT_GROUP_HISTORY_SOURCE)
            ),
            cli_bin=str(getattr(self, "_group_history_cli_bin", _DEFAULT_GROUP_HISTORY_CLI_BIN) or "")
            or _DEFAULT_GROUP_HISTORY_CLI_BIN,
            card_regions=_group_history.normalize_card_regions(
                getattr(self, "_group_history_card_regions", _DEFAULT_GROUP_HISTORY_CARD_REGIONS)
            ),
        )

    def _build_group_history_source(self, settings: _group_history.GroupHistorySettings) -> Any:
        """Pick the fetch backend for ``settings.source``."""
        tz = _group_history.default_timezone()
        if settings.source == _group_history.SOURCE_CLI:
            return _group_history.LarkCliHistorySource(
                binary=settings.cli_bin, tz=tz, card_regions=settings.card_regions
            )
        return _group_history.ApiHistorySource(
            client=self._client,
            run_blocking=self._run_blocking,
            extract_text=self._extract_text_from_raw_content,
            tz=tz,
            card_regions=settings.card_regions,
        )

    def _should_inject_group_history(
        self,
        *,
        chat_type: str,
        inbound_type: MessageType,
        source: Any,
    ) -> bool:
        """True when a group message opens a session that has no context yet.

        ``chat_type`` is the raw event value (``p2p`` vs anything else), the
        same group test ``_admit`` uses.  Slash commands are excluded like the
        mention hint is: a ``/reset`` must reach command dispatch untouched.

        The gate is the *session*, not the thread flags: Feishu stamps a
        ``thread_id`` on a plain quote-reply chain as soon as any topic
        exists under it, so "root vs topic" cannot be read from the event.
        A message whose resolved ``source`` already has an active or
        persisted session inherits that session's history and gets no block;
        a message that opens a new session gets one cold-start block.
        """
        if not getattr(self, "_group_history_enabled", False):
            return False
        if getattr(self, "_client", None) is None:
            return False
        if (chat_type or "p2p") == "p2p":
            return False
        if inbound_type == MessageType.COMMAND:
            return False
        return not self._session_exists_for_source(source)

    async def _fetch_group_history_block(
        self,
        *,
        chat_id: str,
        exclude_message_id: str,
        thread_id: Optional[str] = None,
    ) -> str:
        """Render recent history for a cold-start group turn.

        Thin adapter over :mod:`tools.feishu_group_history`: picks the backend
        from ``FEISHU_GROUP_HISTORY_SOURCE`` and supplies the adapter-owned
        pieces (SDK client, text extraction, sender-name cache, app id).  See
        :func:`tools.feishu_group_history.build_group_history_block` for the
        block contract.  Every failure degrades to ``""``.
        """
        if getattr(self, "_client", None) is None or not chat_id:
            return ""
        settings = self._group_history_settings()

        async def _resolve_name(sender_id: str) -> Optional[str]:
            return await self._resolve_sender_name_from_api(sender_id, is_bot=False)

        return await _group_history.build_group_history_block(
            source=self._build_group_history_source(settings),
            settings=settings,
            chat_id=chat_id,
            exclude_message_id=exclude_message_id,
            thread_id=thread_id,
            render=_group_history.HistoryRenderContext(
                app_id=str(getattr(self, "_app_id", "") or ""),
                resolve_sender_name=_resolve_name,
                msg_max_chars=settings.msg_max_chars,
            ),
            is_thread_anchor=self._is_message_thread_anchor,
        )

    def _cache_sender_name(self, name: str, *sender_ids: Optional[str]) -> None:
        """Cache one resolved display name under every event-provided ID alias."""
        normalized_name = str(name or "").strip()
        if not normalized_name:
            return
        expire_at = time.time() + _fa()._FEISHU_SENDER_NAME_TTL_SECONDS
        for sender_id in sender_ids:
            normalized_id = str(sender_id or "").strip()
            if normalized_id:
                self._sender_name_cache[normalized_id] = (normalized_name, expire_at)

    def _build_websocket_client(self, *, domain: str, event_handler: Any) -> Any:
        return build_feishu_ws_client(self, domain=domain, event_handler=event_handler)


def build_feishu_ws_client(adapter: Any, *, domain: str, event_handler: Any) -> Any:
    """Create a WebSocket client across supported lark-oapi versions.

    ``extra_ua_tags`` enables Channel delivery on current SDKs.  Older
    installations reject that keyword before constructing the client, so
    retain a connection-capable fallback and make the missing group-event
    capability explicit in the log rather than failing the whole adapter.
    """
    kwargs = {
        "app_id": adapter._app_id,
        "app_secret": adapter._app_secret,
        "log_level": _fa().lark.LogLevel.INFO,
        "event_handler": event_handler,
        "domain": domain,
    }
    try:
        # Channel SDK signaling tag: without this UA tag the Feishu
        # server does not push group @mention events over the WebSocket
        # transport.  The tag tells the server to use the Channel protocol
        # which enables group-message routing in addition to P2P DM.
        # See https://github.com/NousResearch/hermes-agent/issues/50656
        return _fa().FeishuWSClient(**kwargs, extra_ua_tags=["channel"])
    except TypeError as exc:
        if "extra_ua_tags" not in str(exc) or "unexpected keyword" not in str(exc):
            raise
        logger.warning(
            "[Feishu] lark-oapi does not support extra_ua_tags; connecting without "
            "Channel group-event signaling. Upgrade to lark-oapi==1.6.8 for group @mentions."
        )
        return _fa().FeishuWSClient(**kwargs)
