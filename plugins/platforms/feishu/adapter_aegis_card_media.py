"""Feishu card-mode and in-topic attachment delivery (Aegis).

A file is never card content: it leaves as its own message, the card keeps a receipt, and a
per-turn ledger prevents double delivery; in a topic, attachments re-anchor through the reply API."""

from __future__ import annotations

import logging
import os
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

from gateway.platforms.base import (
    SendResult,
    should_send_media_as_audio,
)
from plugins.platforms.feishu.adapter_aegis_cards import _CARD_PROGRESS_METADATA_KEY
from plugins.platforms.feishu.adapter_aegis_topics import _FEISHU_ATTACHMENT_THREAD_RECEIVE_CODE

logger = logging.getLogger("plugins.platforms.feishu.adapter")


def _fa():
    """The adapter module, read at call time (no import cycle; SDK names bind at connect)."""
    import sys

    return sys.modules["plugins.platforms.feishu.adapter"]

# Card-mode attachment routing.  Deliberately the same sets the gateway's own
# dispatch loop uses, so a file leaves the turn as the same message type
# whether card output is on or off.
_CARD_ATTACHMENT_VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".3gp"}


_CARD_ATTACHMENT_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


class FeishuCardMediaMixin:
    """Mixed into ``FeishuAdapter`` ahead of ``BasePlatformAdapter``."""

    _CARD_MEDIA_LEDGER_MAX = 256  # files remembered per route per card turn

    def _card_media_route(
        self, chat_id: str, metadata: Optional[dict[str, Any]]
    ) -> Optional[str]:
        """The card route for this send, or ``None`` when cards are not in play.

        ``None`` is the backward-compatibility answer: with card output off, or
        outside a turn, every attachment path below becomes a no-op and the
        legacy behaviour is untouched.
        """
        manager = getattr(self, "_card_manager", None)
        if manager is None or not self._card_output_active():
            return None
        try:
            route = manager.resolve_route(chat_id, metadata)
        except Exception:
            return None
        return route if manager.turn_active(route) else None

    @staticmethod
    def _card_media_key(path: str) -> str:
        return os.path.abspath(os.path.expanduser(str(path or "")))

    def _claim_card_media(self, chat_id: str, path: str, metadata) -> bool:
        """Claim ``path`` for this turn; ``False`` when it was already sent.

        One file can reach the uploader from two directions inside a single
        turn: the gateway extracts attachments from the final reply and calls
        the senders itself, while the card-text safety net extracts them from
        text that never passed through that extraction.  Without a shared
        ledger the two together deliver the same file twice.

        The claim happens *before* the upload, not after it: ``_send_with_retry``
        re-sends byte-identical content after a transient failure, so a claim
        that waited for success would upload twice on the first hiccup.
        """
        route = self._card_media_route(chat_id, metadata)
        if route is None:
            return True
        ledger = self._card_media_dispatched.setdefault(route, OrderedDict())
        key = self._card_media_key(path)
        if key in ledger:
            logger.debug("[Feishu] attachment already sent this turn: %s", key)
            return False
        ledger[key] = None
        while len(ledger) > self._CARD_MEDIA_LEDGER_MAX:
            ledger.popitem(last=False)
        return True

    def _release_card_media(self, chat_id: str, path: str, metadata) -> None:
        """Undo a claim whose upload failed, so a later attempt can retry."""
        route = self._card_media_route(chat_id, metadata)
        if route is None:
            return
        ledger = self._card_media_dispatched.get(route)
        if ledger is not None:
            ledger.pop(self._card_media_key(path), None)

    def _split_card_mode_attachments(
        self,
        *,
        chat_id: str,
        content: str,
        metadata: Optional[dict[str, Any]],
    ) -> tuple[str, list[tuple[str, bool]]]:
        """Take file references out of text that is about to become card body.

        The gateway normally strips attachments out of a reply long before it
        reaches ``send``: ``_process_message_background`` extracts them, sends
        the remaining prose, then uploads each file.  Card mode adds text entry
        points that skip that pass — mid-turn commentary and delegate output go
        straight to ``send`` — and a file reference arriving that way would be
        rendered as a literal path inside the card instead of arriving as a
        file.

        Extraction reuses the gateway's own helpers rather than a local regex,
        so the rule set here is *identical* to the non-card path: the same
        extensions, the same code-fence and inline-code masking, the same
        delivery allowlist.  A reply that merely explains ``MEDIA:`` syntax or
        pastes a path inside a code block still uploads nothing.

        Returns the text to put in the card and the files still owed.
        """
        text = str(content or "")
        if "MEDIA:" not in text and "/" not in text and "\\" not in text:
            return text, []
        if self._card_media_route(chat_id, metadata) is None:
            return text, []
        if self._card_bypass_requested(metadata) or (metadata or {}).get(
            _CARD_PROGRESS_METADATA_KEY
        ):
            # Lifecycle notices and tool chrome are not attachment requests.
            return text, []

        try:
            media, cleaned = self.extract_media(text)
            media = self.filter_media_delivery_paths(media)
            cleaned = self.strip_media_directives_for_display(cleaned)
            bare, cleaned = self.extract_local_files(cleaned)
            bare = self.filter_local_delivery_paths(bare)
        except Exception:
            # Extraction must never cost the user their reply.
            logger.debug("[Feishu] card attachment extraction failed", exc_info=True)
            return text, []

        owed = list(media) + [(path, False) for path in bare]
        if not owed:
            return text, []

        names = "、".join(dict.fromkeys(os.path.basename(p) for p, _ in owed))
        note = self._card_copy.attachment_note.format(names=names)
        cleaned = cleaned.strip()
        body = f"{cleaned}\n\n{note}" if cleaned else note
        return body, owed

    async def _dispatch_card_attachments(
        self,
        *,
        chat_id: str,
        attachments: list[tuple[str, bool]],
        metadata: Optional[dict[str, Any]],
    ) -> None:
        """Upload each owed file as its own message beside the card.

        Routing mirrors the gateway's own dispatch — audio to a voice bubble,
        video to a media message, images inline, everything else a file — so a
        deliverable arrives the same way whether card output is on or off.
        """
        for path, is_voice in attachments:
            ext = os.path.splitext(path)[1].lower()
            try:
                if should_send_media_as_audio(self.platform, ext, is_voice=is_voice):
                    result = await self.send_voice(
                        chat_id=chat_id, audio_path=path, metadata=metadata,
                    )
                elif ext in _CARD_ATTACHMENT_VIDEO_EXTS:
                    result = await self.send_video(
                        chat_id=chat_id, video_path=path, metadata=metadata,
                    )
                elif ext in _CARD_ATTACHMENT_IMAGE_EXTS:
                    result = await self.send_image_file(
                        chat_id=chat_id, image_path=path, metadata=metadata,
                    )
                else:
                    result = await self.send_document(
                        chat_id=chat_id, file_path=path, metadata=metadata,
                    )
            except Exception:
                logger.warning(
                    "[Feishu] card attachment upload raised for %s", path, exc_info=True,
                )
                continue
            if not getattr(result, "success", False):
                logger.warning(
                    "[Feishu] card attachment upload failed for %s: %s",
                    path,
                    getattr(result, "error", None),
                )
                await self._notify_media_delivery_failure(
                    chat_id, path, is_voice=is_voice, metadata=metadata,
                )

    async def _send_media_once(self, *, chat_id: str, path: str, metadata, send):
        """Run ``send`` unless this file already left during this card turn.

        A skip is reported as success: the user has the file, so the caller
        must not render a delivery-failure notice for it.  Outside a card turn
        the ledger is inert and every send goes through untouched.
        """
        if not self._claim_card_media(chat_id, path, metadata):
            return SendResult(success=True)
        result = await send()
        if not getattr(result, "success", False):
            self._release_card_media(chat_id, path, metadata)
        return result

    async def _send_attachment_message(
        self,
        *,
        chat_id: str,
        msg_type: str,
        payload: str,
        reply_to: Optional[str],
        metadata: Optional[dict[str, Any]],
    ) -> Any:
        """Deliver an already-uploaded attachment, surviving topic routing.

        Inside a topic the normal send path keys the message on
        ``receive_id_type=thread_id``.  Feishu accepts text and cards that
        way but rejects every attachment kind — audio, file, media, image,
        and a post carrying one — with a bare field-validation error, so a
        file requested in a topic used to be lost while the same request in
        the main chat worked.  The reply API places the same upload in the
        topic without complaint, so re-anchor on a message inside the topic
        first; only if that also fails do we drop the topic and post flat in
        the chat, on the grounds that a file in the wrong place still beats
        no file.
        """
        response = await self._feishu_send_with_retry(
            chat_id=chat_id,
            msg_type=msg_type,
            payload=payload,
            reply_to=reply_to,
            metadata=metadata,
        )
        thread_id = (metadata or {}).get("thread_id")
        if (
            self._response_succeeded(response)
            or getattr(response, "code", None) != _FEISHU_ATTACHMENT_THREAD_RECEIVE_CODE
            or not thread_id
        ):
            return response

        anchor_id = (metadata or {}).get("reply_to_message_id") or await self._fetch_last_message_in_thread(
            str(thread_id)
        )
        if anchor_id and anchor_id != reply_to:
            logger.info(
                "[Feishu] %s rejected in topic %s; retrying as a reply to %s",
                msg_type, thread_id, anchor_id,
            )
            response = await self._feishu_send_with_retry(
                chat_id=chat_id,
                msg_type=msg_type,
                payload=payload,
                reply_to=anchor_id,
                metadata=metadata,
            )
            if self._response_succeeded(response):
                return response
        logger.warning(
            "[Feishu] topic %s would not take a %s (no usable reply anchor, or the "
            "reply was rejected too); delivering it flat in chat %s",
            thread_id, msg_type, chat_id,
        )
        return await self._feishu_send_with_retry(
            chat_id=chat_id,
            msg_type=msg_type,
            payload=payload,
            reply_to=None,
            metadata=None,
        )
