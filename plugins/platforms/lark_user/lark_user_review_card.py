"""Review card JSON: one card per draft, patched as the user acts on it.

A pending card carries a form: a revision-notes input and three submit buttons whose callback
value is ``{"lark_user_review": approve|revise|reject, "draft": <n>}``. Every settled state drops
the form, so a decided draft cannot be clicked again.
"""

from __future__ import annotations

import json
from typing import Any

from .lark_user_render import neutralize_mentions

ACTION_KEY = "lark_user_review"
NOTES_FIELD = "revision"
_DRAFT_LIMIT = 8000
_SNIPPET = 300
# state -> (header colour, title, whether the form is shown)
_STATES = {
    "pending": ("orange", "📝 待审核回复", True),
    "need_note": ("orange", "📝 待审核回复", True),
    "failed": ("red", "❌ 发送失败，可重试", True),
    "sent": ("green", "✅ 已发送", False),
    "rejected": ("grey", "🚫 已拒绝，不回复", False),
    "revising": ("wathet", "✏️ 修订中", False),
    "expired": ("grey", "⌛ 已过期，未回复", False),
}


def _snippet(text: str, limit: int = _SNIPPET) -> str:
    flat = " ".join((text or "").split())
    return flat if len(flat) <= limit else flat[:limit] + "…"


def _button(label: str, verb: str, number: str, kind: str) -> dict[str, Any]:
    return {"tag": "column", "width": "auto", "elements": [{
        "tag": "button", "name": verb, "type": kind, "form_action_type": "submit",
        "text": {"tag": "plain_text", "content": label},
        "behaviors": [{"type": "callback", "value": {ACTION_KEY: verb, "draft": number}}],
    }]}


def _form(number: str) -> dict[str, Any]:
    return {"tag": "form", "name": f"review_{number}", "elements": [
        {"tag": "input", "name": NOTES_FIELD, "max_length": 1000,
         "placeholder": {"tag": "plain_text", "content": "修改意见（点“修订”时必填）"}},
        {"tag": "column_set", "columns": [
            _button("✅ 通过", "approve", number, "primary"),
            _button("✏️ 修订", "revise", number, "default"),
            _button("🚫 拒绝", "reject", number, "danger"),
        ]},
    ]}


def review_card(number: str, draft: dict[str, Any], *, state: str = "pending", note: str = "") -> str:
    colour, title, with_form = _STATES[state]
    origin = draft.get("origin") or {}
    where = f"群：{origin['chat_name']}" if origin.get("chat_name") else "私聊"
    head = [f"**来自**：{origin.get('sender_name') or origin.get('sender_id') or '?'}（{where}）",
            f"**原消息**：{_snippet(origin.get('text', ''))}"]
    if draft.get("revision"):
        head.append(f"**第 {draft['revision']} 次修订**，意见：{_snippet(draft.get('revision_note', ''), 200)}")
    content = str(draft.get("content") or "")
    if len(content) > _DRAFT_LIMIT:
        content = content[:_DRAFT_LIMIT] + "\n\n…（卡片中已截断，发送的是完整内容）"
    elements: list[dict[str, Any]] = [
        {"tag": "markdown", "content": neutralize_mentions("\n".join(head))},
        {"tag": "hr"},
        {"tag": "markdown", "content": neutralize_mentions(content) or " "},
    ]
    status = {"need_note": "⚠️ 请先在输入框填写修改意见，再点“修订”。"}.get(state, note)
    if status:
        elements += [{"tag": "hr"}, {"tag": "markdown", "content": neutralize_mentions(status), "text_size": "notation"}]
    if with_form:
        elements.append(_form(number))
    return json.dumps({
        "schema": "2.0", "config": {"update_multi": True},
        "header": {"title": {"tag": "plain_text", "content": f"{title} #{number}"}, "template": colour},
        "body": {"elements": elements},
    }, ensure_ascii=False)
