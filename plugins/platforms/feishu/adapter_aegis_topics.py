"""Feishu automatic topics and root-keyed sessions (Aegis, FEISHU_REPLY_THREAD).

A top-level message's om_* id is the prospective topic root and reply anchor; later omt_* messages
reuse the root-keyed session only when that root has live or persisted routing. Topic-creation
failures fall back to one flat message only when a topic can never exist under the root."""

from __future__ import annotations

import logging
from collections import OrderedDict
from typing import Any, Optional

from plugins.platforms.feishu.adapter_aegis_admission import _scoped_flag

logger = logging.getLogger("plugins.platforms.feishu.adapter")


def _fa():
    """The adapter module, read at call time (no import cycle; SDK names bind at connect)."""
    import sys

    return sys.modules["plugins.platforms.feishu.adapter"]

#: Replies that say a topic can never exist under this root: the root was
#: withdrawn or is missing, the group does not allow topic replies (230071),
#: or the root is an aggregated message (230072).  Only these justify
#: abandoning the prospective topic on the first failure; every other code
#: (rate limits, card validation, internal errors) says nothing about topics.
_FEISHU_AUTO_THREAD_UNSUPPORTED_CODES = frozenset({230011, 231003, 230071, 230072})  # reply target gone + no topic possible


#: Feishu's generic internal error.  It has been observed on a topic-creating
#: card reply that Feishu had in fact bound (a follow-up with the same card
#: returned "binding over limit"), so the outcome is unknown, not a failure.
_FEISHU_OUTCOME_UNKNOWN_CODES = frozenset({2200})


#: In-topic attempts that may fail, with no topic appearing under the root,
#: before one flat message is sent instead.  Two, so a single transient error
#: is retried in place rather than costing the whole turn its topic.
_FEISHU_AUTO_THREAD_SOFT_FAILURE_LIMIT = 2


#: Feishu's bare field-validation error.  ``message.create`` keyed by
#: ``receive_id_type=thread_id`` gets it for every message type (text, cards,
#: attachments), which is why ``_topic_reply_anchor`` replies into a real topic
#: instead.  The send path only falls back to that create when no anchor could be
#: found; this code then means "re-anchor", not "give up" — see
#: ``_send_attachment_message``.
_FEISHU_ATTACHMENT_THREAD_RECEIVE_CODE = 99992402


class FeishuAutoTopicMixin:
    """Mixed into ``FeishuAdapter`` ahead of ``BasePlatformAdapter``."""

    def _reply_thread_enabled(self) -> bool:
        configured = getattr(self, "_reply_thread_enabled_setting", None)
        if configured is not None:
            return bool(configured)
        return _scoped_flag("FEISHU_REPLY_THREAD", default=True)

    @staticmethod
    def _is_message_thread_anchor(thread_id: Any) -> bool:
        """Return True when thread metadata carries a root message id.

        Feishu message ids use ``om_*`` while real thread ids use ``omt_*``.
        Auto-created lanes deliberately expose the former as ``source.thread_id``
        so the initiating turn and later replies can share one session key.
        """
        return str(thread_id or "").startswith("om_")

    def _note_topic_root(self, thread_id: Any, root_message_id: Any) -> None:
        """Remember the root message of a real ``omt_*`` topic (seen on an inbound message)."""
        if str(thread_id or "").startswith("omt_") and root_message_id:
            roots = self._auto_thread_state("_topic_root_messages")
            roots[str(thread_id)] = str(root_message_id)
            roots.move_to_end(str(thread_id))
            self._trim_oldest_dict_entries(roots, self.CHAT_LOCK_MAX_SIZE)

    async def _topic_reply_anchor(self, metadata: Optional[dict[str, Any]]) -> Optional[str]:
        """A message to reply to (``reply_in_thread``) so a send lands inside ``metadata``'s topic.

        Feishu rejects ``message.create`` with ``receive_id_type=thread_id`` (99992402) for
        every message type, so a send into a real ``omt_*`` topic must go through the reply API.
        Senders that carry no reply target of their own — delegate approval/clarify cards, card
        deliveries outside a turn, notices — would otherwise never reach a topic the user opened
        themselves (2026-11-17). An ``om_*`` thread id is its own anchor; an ``omt_*`` topic is
        anchored on its root, learned from inbound messages or looked up once and cached.
        """
        thread_id = str((metadata or {}).get("thread_id") or "")
        explicit = (metadata or {}).get("reply_to_message_id")
        if explicit or not thread_id or self._is_message_thread_anchor(thread_id):
            return explicit or thread_id or None
        roots = self._auto_thread_state("_topic_root_messages")
        if thread_id not in roots:
            root = await self._fetch_last_message_in_thread(thread_id)  # oldest first: the topic root
            if not root:
                logger.warning("[Feishu] no reply anchor found in topic %s", thread_id)
                return None
            self._note_topic_root(thread_id, root)
        return roots.get(thread_id)

    def _auto_thread_state(self, name: str) -> OrderedDict[str, None]:
        state = getattr(self, name, None)
        if state is None:
            state = OrderedDict()
            setattr(self, name, state)
        return state

    def _auto_thread_is_pending(self, root_message_id: Any) -> bool:
        root = str(root_message_id or "")
        return bool(
            self._is_message_thread_anchor(root)
            and root in self._auto_thread_state("_pending_auto_thread_roots")
        )

    def _auto_thread_is_established(self, root_message_id: Any) -> bool:
        """True for an ``om_*`` root that is neither pending nor failed.

        Only meaningful for a root the caller saw pending moments ago: a root
        that was never tracked reads as established too.
        """
        root = str(root_message_id or "")
        return bool(
            self._is_message_thread_anchor(root)
            and root not in self._auto_thread_state("_pending_auto_thread_roots")
            and root not in self._auto_thread_state("_failed_auto_thread_roots")
        )

    def _mark_auto_thread_pending(self, root_message_id: str) -> None:
        pending = self._auto_thread_state("_pending_auto_thread_roots")
        failed = self._auto_thread_state("_failed_auto_thread_roots")
        failed.pop(root_message_id, None)
        self._auto_thread_state("_auto_thread_soft_failures").pop(root_message_id, None)
        pending[root_message_id] = None
        pending.move_to_end(root_message_id)
        self._trim_oldest_dict_entries(pending, self.CHAT_LOCK_MAX_SIZE)

    def _mark_auto_thread_established(self, root_message_id: str) -> None:
        self._auto_thread_state("_pending_auto_thread_roots").pop(root_message_id, None)
        self._auto_thread_state("_failed_auto_thread_roots").pop(root_message_id, None)
        self._auto_thread_state("_auto_thread_soft_failures").pop(root_message_id, None)

    def _mark_auto_thread_failed(self, root_message_id: str) -> None:
        self._auto_thread_state("_pending_auto_thread_roots").pop(root_message_id, None)
        self._auto_thread_state("_auto_thread_soft_failures").pop(root_message_id, None)
        failed = self._auto_thread_state("_failed_auto_thread_roots")
        failed[root_message_id] = None
        failed.move_to_end(root_message_id)
        self._trim_oldest_dict_entries(failed, self.CHAT_LOCK_MAX_SIZE)

    def _note_auto_thread_soft_failure(self, root_message_id: str) -> int:
        """Count one in-topic failure that left no topic behind; return the total."""
        strikes = self._auto_thread_state("_auto_thread_soft_failures")
        count = int(strikes.get(root_message_id) or 0) + 1
        strikes[root_message_id] = count
        strikes.move_to_end(root_message_id)
        self._trim_oldest_dict_entries(strikes, self.CHAT_LOCK_MAX_SIZE)
        return count

    async def _auto_thread_topic_exists(self, root_message_id: str) -> bool:
        """Does Feishu now report a topic under ``root_message_id``?

        Asked after a topic-creating reply failed with a code that does not
        rule topics out: an internal error may still have created the reply,
        and with it the topic.  A lookup failure answers ``False``, which only
        costs the caller one more in-topic attempt.
        """
        if not getattr(self, "_client", None) or not root_message_id:
            return False
        try:
            request = self._build_get_message_request(str(root_message_id))
            response = await self._run_blocking(self._client.im.v1.message.get, request)
        except Exception:
            logger.debug("[Feishu] topic probe for %s failed", root_message_id, exc_info=True)
            return False
        if not self._response_succeeded(response):
            return False
        items = getattr(getattr(response, "data", None), "items", None) or []
        root = items[0] if items else None
        return bool(str(getattr(root, "thread_id", "") or "").strip())

    async def _auto_thread_failure_action(
        self,
        *,
        root: str,
        chat_id: str,
        msg_type: str,
        payload: str,
        reply_to: Optional[str],
        diag: str,
        code: Any,
    ) -> str:
        """Settle a failed topic-creating send: ``"flat"``, ``"retry"`` or ``"give_up"``.

        Only a code that rules topics out abandons the topic at once.  Anything
        else is first checked against Feishu — an internal error has been seen
        on a reply that did create the topic — and then retried in the topic,
        so a single transient failure no longer drops the rest of the turn into
        the group; one flat message is sent only once a second in-topic attempt
        has also failed without a topic appearing.  A card entity is never
        re-sent from here (``"give_up"``): it may already be bound to the
        message that failed, and the card layer owns its retry.
        """
        card_entity = self._is_card_entity_payload(msg_type, payload)
        if code in _FEISHU_AUTO_THREAD_UNSUPPORTED_CODES:
            self._mark_auto_thread_failed(root)
            logger.warning(
                "[Feishu] Auto-thread reply to %s cannot create a topic (%s, reply_to=%s); "
                "%s in chat %s",
                root,
                diag,
                reply_to,
                "leaving the card to its own fallback" if card_entity
                else "falling back to one flat message",
                chat_id,
            )
            return "give_up" if card_entity else "flat"
        if await self._auto_thread_topic_exists(root):
            self._mark_auto_thread_established(root)
            logger.warning(
                "[Feishu] Auto-thread reply to %s failed (%s, reply_to=%s) but the topic "
                "exists; keeping topic routing in chat %s",
                root,
                diag,
                reply_to,
                chat_id,
            )
            return "give_up" if card_entity else "retry"
        strikes = self._note_auto_thread_soft_failure(root)
        if card_entity or strikes < _FEISHU_AUTO_THREAD_SOFT_FAILURE_LIMIT:
            logger.warning(
                "[Feishu] Auto-thread reply to %s failed (%s, reply_to=%s); no topic yet, "
                "keeping topic routing (%d/%d) in chat %s",
                root,
                diag,
                reply_to,
                strikes,
                _FEISHU_AUTO_THREAD_SOFT_FAILURE_LIMIT,
                chat_id,
            )
            return "give_up" if card_entity else "retry"
        self._mark_auto_thread_failed(root)
        logger.warning(
            "[Feishu] Auto-thread reply to %s failed again (%s, reply_to=%s); "
            "falling back to one flat message in chat %s",
            root,
            diag,
            reply_to,
            chat_id,
        )
        return "flat"

    def _auto_thread_root_session(self, source: Any, root_message_id: str) -> Optional[str]:
        """The root-keyed thread id for this topic, or ``None`` if it does not win.

        A message in an auto-created topic reuses the root-keyed (``om_*``)
        session when that session already exists; a human-created topic keeps
        its historical real-thread (``omt_*``) identity.

        ``None`` rather than "the id that won" so the caller never has to infer
        the outcome from a string comparison — that would read as a win in the
        case where the two ids happen to be equal, and mark a topic established
        whose session does not exist. Read-only: ``source`` is restored before
        returning, so a caller that only wants to *know* the answer does not
        have to undo anything.
        """
        real_thread_id = source.thread_id
        source.thread_id = str(root_message_id)
        try:
            exists = self._session_exists_for_source(source)
        finally:
            source.thread_id = real_thread_id
        return str(root_message_id) if exists else None

    def _session_key_for_source(self, source: Any) -> str:
        """The session key ``source`` routes to, or "" when it cannot be built.

        One spelling of the flags for every caller: the auto-thread probe, the
        Jev busy check, and anything added later. Two spellings would drift,
        and a key that disagrees with the one ``handle_message`` computes is a
        check that silently measures the wrong session.
        """
        try:
            from gateway.session import build_session_key

            extra = getattr(getattr(self, "config", None), "extra", None) or {}
            return build_session_key(
                source,
                group_sessions_per_user=extra.get("group_sessions_per_user", True),
                thread_sessions_per_user=extra.get("thread_sessions_per_user", False),
                profile=getattr(source, "profile", None),
            )
        except Exception:
            logger.debug("[Feishu] Failed to build session key for source", exc_info=True)
            return ""

    def _session_exists_for_source(self, source: Any) -> bool:
        """Check active and persisted routing state for a candidate source."""
        session_key = self._session_key_for_source(source)
        if not session_key:
            return False

        if session_key in (getattr(self, "_active_sessions", None) or {}):
            return True
        store = getattr(self, "_session_store", None)
        peek = getattr(store, "peek_session_id", None)
        if not callable(peek):
            return False
        try:
            return bool(peek(session_key))
        except Exception:
            logger.debug("[Feishu] Failed to inspect candidate auto-thread session", exc_info=True)
            return False

    def _thread_session_is_busy(
        self, source: Any, *, root_message_id: Optional[str] = None
    ) -> bool:
        """Is a turn already running in the topic this message belongs to?

        Asked only of messages already inside a topic, and only on the
        unprompted path. It resolves the SAME session identity the dispatch
        path will settle on — via ``_auto_thread_root_session``, so the root-
        vs-real-thread rule has one spelling — because a check against the
        other candidate would measure a session this message never enters.

        A session that does not exist yet has no running task, so a brand-new
        topic reads as idle and goes on to be judged.
        """
        real_thread_id = source.thread_id
        if root_message_id:
            source.thread_id = (
                self._auto_thread_root_session(source, root_message_id)
                or real_thread_id
            )
        try:
            session_key = self._session_key_for_source(source)
        finally:
            source.thread_id = real_thread_id
        if not session_key:
            return False
        try:
            return self.session_has_running_task(session_key)
        except Exception:
            # Never let a bookkeeping error decide whether a message is
            # answered; fall through to the Jev verdict, which is fail-closed
            # on its own.
            logger.debug("[Feishu] thread busy-check failed", exc_info=True)
            return False

    @staticmethod
    def _feishu_response_diag(response: Any) -> str:
        """``code``/``msg``/``log_id`` of a response, for failure logs.

        The log id is what Feishu support needs to trace a request, and the
        only way to tell two identical-looking internal errors apart.
        """
        if response is None:
            return "no response"
        log_id = ""
        get_log_id = getattr(response, "get_log_id", None)
        if callable(get_log_id):
            try:
                log_id = str(get_log_id() or "")
            except Exception:
                log_id = ""
        return "code={} msg={!r} log_id={}".format(
            getattr(response, "code", None),
            str(getattr(response, "msg", "") or ""),
            log_id or "-",
        )

    @staticmethod
    def _send_outcome_unknown(response: Any) -> bool:
        """True when a failed send may nonetheless have been delivered."""
        if response is None:
            return True
        return getattr(response, "code", None) in _FEISHU_OUTCOME_UNKNOWN_CODES
