"""raw_prompt — /codex、/claude 模式下把用户发的内容原样交给 codex / Claude Code。

网关交给 agent 之前会加工入站消息（gateway/run.py ``_prepare_inbound_message_text``：
``<source>`` 头、飞书群聊历史、``[昵称]`` 前缀、引用、频道上下文、附件说明），run_sync
还可能再加模型切换 / 续跑等提示。这些是给 Hermes loop 的，codex / Claude Code 只该看到用户发的内容。

  - 捕获：给 GatewayRunner 实例包一层 ``_prepare_inbound_message_text``，照常加工（Hermes 会话
    记录、STT、图片缓冲都不变），再把 (event, source, 加工结果) 记进 ContextVar。新消息与排队的
    后续消息都经过它；``_run_in_executor_with_context`` 把 context 带进 agent 线程，每轮读到的就是
    本轮那条消息。
  - 替换：给会话后端包一层 ``run_turn``：本轮输入确实由那条加工结果而来时才替换——纯文本消息换成
    ``event.text`` 原文；带附件的消息保留附件说明，剥掉网关加的其他内容。每条消息只替换一次；
    CLI / TUI 没有记录，原样透传。
"""

from __future__ import annotations

import contextvars
import json
import logging
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)

_SOURCE_OPEN = "<source>"
_SOURCE_CLOSE = "</source>\n\n"
_REPLY_SNIPPET_CHARS = 500  # 与网关引用片段截断一致


@dataclass
class _Inbound:
    event: Any
    source: Any
    prepared: str
    # agent 线程跑在 context 副本里：只用一次靠这个共享对象上的标记
    used: bool = False


_inbound: contextvars.ContextVar[Optional[_Inbound]] = contextvars.ContextVar(
    "ext_raw_prompt_inbound", default=None
)
_missing_warned = False


def install_gateway_capture(runner: Any) -> None:
    """记录每条入站消息加工前后的样子（幂等，实例级，不改网关行为）。"""
    if runner is None or getattr(runner, "_ext_raw_prompt_capture", False):
        return
    original = getattr(runner, "_prepare_inbound_message_text", None)
    if not callable(original):
        global _missing_warned
        if not _missing_warned:
            _missing_warned = True
            logger.warning("raw_prompt: gateway has no _prepare_inbound_message_text; prompts pass through unchanged")
        return

    async def _prepare_inbound_message_text(*args, **kwargs):
        _inbound.set(None)
        prepared = await original(*args, **kwargs)
        event = kwargs.get("event")
        if isinstance(prepared, str) and event is not None:
            _inbound.set(_Inbound(event, kwargs.get("source"), prepared))
        return prepared

    runner._prepare_inbound_message_text = _prepare_inbound_message_text
    runner._ext_raw_prompt_capture = True


def install_session_passthrough(session: Any) -> None:
    """codex / Claude Code 会话的 run_turn 改发用户原文（幂等）。"""
    if session is None or getattr(session, "_ext_raw_prompt_wrapped", False):
        return
    original = session.run_turn

    def run_turn(user_input, **kwargs):
        return original(user_input=backend_input(user_input), **kwargs)

    session.run_turn = run_turn
    session._ext_raw_prompt_wrapped = True


def backend_input(user_input: Any) -> Any:
    """本轮输入若由刚记录的那条入站消息加工而来，换成用户原文；否则原样返回。"""
    inbound = _inbound.get()
    if inbound is None or inbound.used:
        return user_input
    # 网关之后加的提示（模型切换、续跑、时间戳、旁观群聊上下文）都在前面，所以按结尾匹配
    if isinstance(user_input, str):
        if not user_input.endswith(inbound.prepared):
            return user_input
        text = user_text(inbound)
        # 空文本（如重启后的自动续跑轮）保留 Hermes 的提示，免得发出空回合
        if not text.strip():
            return user_input
        inbound.used = True
        return text
    if isinstance(user_input, list):
        # 原生图片：只换文本部分，图片部分原样保留
        for index, part in enumerate(user_input):
            if (
                isinstance(part, dict)
                and part.get("type") == "text"
                and str(part.get("text") or "").endswith(inbound.prepared)
            ):
                inbound.used = True
                text = user_text(inbound)
                parts = list(user_input)
                if text.strip():
                    parts[index] = {**part, "text": text}
                else:
                    del parts[index]
                return parts
    return user_input


def user_text(inbound: _Inbound) -> str:
    event = inbound.event
    if not getattr(event, "media_urls", None):
        return getattr(event, "text", None) or ""
    return strip_gateway_context(event, inbound.source, inbound.prepared)


def strip_gateway_context(event: Any, source: Any, text: str) -> str:
    """附件消息：保留附件说明与用户文字，剥掉网关加的 <source>、群聊历史、引用、频道上下文、[昵称]。

    逐层按网关拼接的原样精确匹配；某层对不上就留着，宁可多发也不误删。"""
    text = _strip_source_header(text)
    metadata = getattr(event, "metadata", None)
    history = str(metadata.get("group_history_block") or "").strip() if isinstance(metadata, dict) else ""
    if history:
        text = _strip_prefix(text, f"{history}\n\n")
    reply = getattr(event, "reply_to_text", None)
    if reply and getattr(event, "reply_to_message_id", None):
        snippet = reply[:_REPLY_SNIPPET_CHARS]
        if getattr(event, "reply_to_is_own_message", False):
            quote = f'[Replying to your previous message: "{snippet}"]'
        else:
            quote = f'[Replying to: "{snippet}"]'
        text = _strip_prefix(text, f"{quote}\n\n")
    channel = getattr(event, "channel_context", None)
    if channel:
        text = text.replace(f"{channel}\n\n[New message]\n", "", 1)
    return _strip_sender_prefix(event, source, text).strip()


def _strip_prefix(text: str, prefix: str) -> str:
    return text[len(prefix):] if text.startswith(prefix) else text


def _strip_source_header(text: str) -> str:
    if not text.startswith(_SOURCE_OPEN):
        return text
    try:
        _, end = json.JSONDecoder().raw_decode(text, len(_SOURCE_OPEN))
    except ValueError:
        return text
    rest = text[end:]
    return rest[len(_SOURCE_CLOSE):] if rest.startswith(_SOURCE_CLOSE) else text


def _strip_sender_prefix(event: Any, source: Any, text: str) -> str:
    """共享会话里网关在用户文字前加的 ``[昵称] ``（Slack 另带 ``| Slack user <@U…>``）。"""
    name = getattr(source, "user_name", None)
    if not name:
        return text
    try:
        from gateway.session import neutralize_untrusted_inline_text

        label = neutralize_untrusted_inline_text(name)
    except Exception:
        return text
    user_id = getattr(source, "user_id", None)
    if getattr(getattr(source, "platform", None), "value", None) == "slack" and user_id:
        label = f"{label} | Slack user <@{user_id}>"
    if hasattr(event, "_gateway_pending_stt_text"):
        base = event._gateway_pending_stt_text or ""
    else:
        base = getattr(event, "text", None) or ""
    marker = f"[{label}] {base}"
    return text[: -len(marker)] + base if text.endswith(marker) else text
