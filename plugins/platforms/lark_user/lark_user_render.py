"""Outbound shaping: mention neutralization, prefix/suffix, chunking, per-format bodies.

Replies go out under the user's own name, so agent text must never carry a live ``<at ...>`` tag:
a prompt-injected ``<at user_id="all">`` would page a whole group as the user. Every tag in the
agent's text is broken with a zero-width space; the only live mention is the one ``at_sender``
adds after neutralization.
"""

from __future__ import annotations

import json
import re
from typing import Mapping

from .lark_user_config import ReplySettings

CHUNK_LIMIT = 8000  # chars; ~24KB of CJK UTF-8, under the 30KB post/card body cap

_ZWSP = "\u200b"
_AT_TAG_RE = re.compile(r"<(\s*/?\s*)(at)\b", re.IGNORECASE)


class _Vars(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def neutralize_mentions(text: str) -> str:
    return _AT_TAG_RE.sub(lambda m: f"<{m.group(1)}{_ZWSP}{m.group(2)}", text)


def fill(template: str, variables: Mapping[str, str]) -> str:
    try:
        return template.format_map(_Vars(variables))
    except (ValueError, IndexError):
        return template  # a stray brace in user text is not a template


def split_body(text: str, limit: int = CHUNK_LIMIT) -> list[str]:
    """Paragraph-aware split; a single over-long paragraph is cut hard."""
    chunks: list[str] = []
    current = ""
    for para in text.split("\n"):
        while len(para) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(para[:limit])
            para = para[limit:]
        candidate = f"{current}\n{para}" if current else para
        if len(candidate) > limit:
            chunks.append(current)
            current = para
        else:
            current = candidate
    if current or not chunks:
        chunks.append(current)
    return chunks


def _mention(fmt: str, open_id: str, name: str) -> str:
    return f"<at id={open_id}></at> " if fmt == "card" else f'<at user_id="{open_id}">{name}</at> '


def _card(title: str, body: str, note: str) -> str:
    elements = [{"tag": "markdown", "content": body}]
    if note:
        elements += [{"tag": "hr"}, {"tag": "markdown", "content": note, "text_size": "notation"}]
    return json.dumps({
        "schema": "2.0",
        "header": {"title": {"tag": "plain_text", "content": title}, "template": "blue"},
        "body": {"elements": elements},
    }, ensure_ascii=False)


def render(content: str, reply: ReplySettings, variables: Mapping[str, str], *,
           mention: tuple[str, str] | None = None) -> list[str]:
    """Agent ``content`` -> wire bodies for ``reply.format`` (one per message to send)."""
    prefix, suffix = fill(reply.prefix, variables), fill(reply.suffix, variables)
    at = _mention(reply.format, *mention) if mention else ""
    pieces = split_body(neutralize_mentions(content))
    if reply.format == "card":
        title = f"{prefix.strip()} {fill(reply.card_title, variables)}".strip()
        last = len(pieces) - 1
        return [_card(title, (at if i == 0 else "") + piece, suffix.strip() if i == last else "")
                for i, piece in enumerate(pieces)]
    pieces[0] = f"{at}{prefix}{pieces[0]}"
    pieces[-1] = f"{pieces[-1]}{suffix}"
    return pieces
