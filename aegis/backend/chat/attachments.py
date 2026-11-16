"""Validated, short-lived attachments for Aegis chat turns."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import mimetypes
from pathlib import Path
import re
import threading
import time
from typing import Any, Iterable
from uuid import uuid4

from fastapi import HTTPException, UploadFile, status

from gateway.platforms.base import (
    SUPPORTED_IMAGE_DOCUMENT_TYPES,
    cache_document_from_bytes,
    cache_image_from_bytes,
    cleanup_document_cache,
    cleanup_image_cache,
)
from tools.credential_files import to_agent_visible_cache_path


MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
TEXT_INLINE_MAX_BYTES = 100 * 1024
ATTACHMENT_TTL_SECONDS = 24 * 60 * 60
_READ_CHUNK_BYTES = 64 * 1024
_TEXT_EXTENSIONS = frozenset({
    ".txt", ".md", ".markdown", ".csv", ".tsv", ".log", ".json", ".jsonl",
    ".ndjson", ".xml", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
    ".html", ".htm", ".css", ".py", ".js", ".ts", ".tsx", ".jsx", ".sh",
    ".bash", ".zsh", ".sql", ".graphql", ".proto", ".diff", ".patch",
})


@dataclass(frozen=True, slots=True)
class ChatAttachment:
    """A server-owned cached file that may be included in one Aegis turn."""

    id: str
    owner_id: str
    kind: str
    media_type: str
    cache_path: str
    host_path: str
    display_name: str
    size: int
    created_at: float

    def public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "media_type": self.media_type,
            "cache_path": self.cache_path,
            "display_name": self.display_name,
            "size": self.size,
        }


class ChatAttachmentStore:
    """Owns upload records so websocket clients cannot nominate host paths."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._attachments: dict[str, ChatAttachment] = {}
        self._last_cache_cleanup = 0.0

    async def upload(self, file: UploadFile, *, owner_id: str) -> ChatAttachment:
        self._cleanup_expired()
        self._cleanup_cache_if_due()
        payload = await self._read_upload(file)
        filename = self._safe_filename(file.filename)
        declared_type = (file.content_type or "").split(";", 1)[0].strip().lower()
        attachment = self._cache_bytes(
            payload,
            filename=filename,
            declared_type=declared_type,
            owner_id=owner_id,
        )
        with self._lock:
            self._attachments[attachment.id] = attachment
        return attachment

    def resolve(self, raw_attachments: object, *, owner_id: str) -> list[ChatAttachment]:
        if raw_attachments is None:
            return []
        if not isinstance(raw_attachments, list):
            raise ValueError("attachments must be a list")

        self._cleanup_expired()
        resolved: list[ChatAttachment] = []
        seen: set[str] = set()
        with self._lock:
            for raw in raw_attachments:
                if not isinstance(raw, dict):
                    raise ValueError("each attachment must be an object")
                attachment_id = str(raw.get("id") or "").strip()
                if not attachment_id or attachment_id in seen:
                    raise ValueError("attachments must contain unique upload ids")
                attachment = self._attachments.get(attachment_id)
                if attachment is None or attachment.owner_id != owner_id:
                    raise ValueError("attachment is unavailable")
                if not Path(attachment.host_path).is_file():
                    raise ValueError("attachment cache file is unavailable")
                seen.add(attachment_id)
                resolved.append(attachment)
        return resolved

    async def _read_upload(self, file: UploadFile) -> bytes:
        chunks: list[bytes] = []
        total = 0
        try:
            while True:
                chunk = await file.read(_READ_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_ATTACHMENT_BYTES:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail="Attachment exceeds the 20 MiB limit.",
                    )
                chunks.append(chunk)
        finally:
            await file.close()
        if total == 0:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Attachment is empty.")
        return b"".join(chunks)

    def _cache_bytes(
        self,
        payload: bytes,
        *,
        filename: str,
        declared_type: str,
        owner_id: str,
    ) -> ChatAttachment:
        suffix = Path(filename).suffix.lower()
        is_image = declared_type.startswith("image/") or suffix in SUPPORTED_IMAGE_DOCUMENT_TYPES
        if is_image:
            ext = suffix if suffix in SUPPORTED_IMAGE_DOCUMENT_TYPES else ".jpg"
            try:
                host_path = cache_image_from_bytes(payload, ext=ext)
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="Uploaded image content is invalid.",
                ) from exc
            media_type = declared_type if declared_type.startswith("image/") else SUPPORTED_IMAGE_DOCUMENT_TYPES[ext]
            kind = "image"
        else:
            host_path = cache_document_from_bytes(payload, filename)
            guessed_type = mimetypes.guess_type(filename)[0] or ""
            media_type = declared_type or guessed_type or "application/octet-stream"
            kind = "document"

        return ChatAttachment(
            id=f"att_{uuid4().hex}",
            owner_id=owner_id,
            kind=kind,
            media_type=media_type,
            cache_path=to_agent_visible_cache_path(host_path),
            host_path=host_path,
            display_name=filename,
            size=len(payload),
            created_at=time.time(),
        )

    @staticmethod
    def _safe_filename(value: str | None) -> str:
        filename = Path(value or "attachment").name.replace("\x00", "").strip()
        filename = re.sub(r"[^\w.\- ]", "_", filename)
        return filename or "attachment"

    def _cleanup_expired(self) -> None:
        cutoff = time.time() - ATTACHMENT_TTL_SECONDS
        with self._lock:
            expired = [key for key, value in self._attachments.items() if value.created_at < cutoff]
            for key in expired:
                self._attachments.pop(key, None)

    def _cleanup_cache_if_due(self) -> None:
        now = time.monotonic()
        if now - self._last_cache_cleanup < 60:
            return
        self._last_cache_cleanup = now
        cleanup_image_cache(max_age_hours=24)
        cleanup_document_cache(max_age_hours=24)


def _is_inline_text(attachment: ChatAttachment) -> bool:
    return (
        attachment.media_type.startswith("text/")
        or Path(attachment.display_name).suffix.lower() in _TEXT_EXTENSIONS
    )


def _document_context(user_text: str, attachments: Iterable[ChatAttachment]) -> str:
    result = user_text
    for attachment in attachments:
        if attachment.kind == "image":
            continue
        prefix: str
        if _is_inline_text(attachment) and attachment.size <= TEXT_INLINE_MAX_BYTES:
            try:
                contents = Path(attachment.host_path).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                contents = ""
            if contents:
                prefix = f"[Content of {attachment.display_name}]:\n{contents}"
            else:
                prefix = (
                    f"[The user sent a document: '{attachment.display_name}'. It is saved at: "
                    f"{attachment.cache_path}. Its text could not be inlined; read it with a file tool.]"
                )
        else:
            prefix = (
                f"[The user sent a document: '{attachment.display_name}' ({attachment.media_type}). "
                f"It is saved at: {attachment.cache_path}. Its content is not inlined; use a file "
                "or terminal tool to inspect it before answering.]"
            )
        result = f"{prefix}\n\n{result}" if result else prefix
    return result


async def _describe_images(user_text: str, attachments: list[ChatAttachment]) -> str:
    from agent.memory_manager import sanitize_context
    from tools.vision_tools import vision_analyze_tool

    descriptions: list[str] = []
    prompt = (
        "Describe everything visible in this image in thorough detail. Include text, code, data, "
        "objects, people, layout, colors, and notable visual information."
    )
    for attachment in attachments:
        try:
            raw = await vision_analyze_tool(image_url=attachment.host_path, user_prompt=prompt)
            result = json.loads(raw)
            if result.get("success"):
                descriptions.append(
                    f"[The user sent an image. What it shows:\n{sanitize_context(str(result.get('analysis') or ''))}]\n"
                    f"[For closer inspection, use vision_analyze with image_url: {attachment.cache_path}]"
                )
            else:
                descriptions.append(
                    f"[The user sent an image at {attachment.cache_path}, but automatic analysis was unavailable.]"
                )
        except Exception:
            descriptions.append(
                f"[The user sent an image at {attachment.cache_path}, but automatic analysis failed.]"
            )
    return "\n\n".join(descriptions + ([user_text] if user_text else []))


def prepare_turn_message(
    user_text: str,
    attachments: list[ChatAttachment],
    *,
    agent: object,
) -> object:
    """Build the current user turn using the same image/document semantics as Slack."""
    message = _document_context(user_text, attachments)
    images = [attachment for attachment in attachments if attachment.kind == "image"]
    if not images:
        return message

    try:
        from agent.image_routing import build_native_content_parts, decide_image_input_mode
        from hermes_cli.config import load_config

        mode = decide_image_input_mode(
            str(getattr(agent, "provider", "") or ""),
            str(getattr(agent, "model", "") or ""),
            load_config(),
        )
        if mode == "native":
            parts, _skipped = build_native_content_parts(message, [item.host_path for item in images])
            for part in parts:
                if part.get("type") == "text":
                    text = str(part.get("text") or "")
                    for item in images:
                        text = text.replace(item.host_path, item.cache_path)
                    part["text"] = text
            if any(part.get("type") == "image_url" for part in parts):
                return parts
    except Exception:
        pass

    return asyncio.run(_describe_images(message, images))
