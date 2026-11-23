"""``/lark-user start|stop|status``: switch auto-reply on and off while the gateway runs.

The switch lives in memory only. Every gateway start (and every reconnect of the adapter) begins
with auto-reply OFF: nothing is polled and nothing is answered until the user runs
``/lark-user start``. Messages that arrive while it is off are history, never answered later.

``/lark-user reset <chat_id>`` ends every live conversation of one chat (a DM or a group, whatever
the isolation scope cut it into: per user, per topic, shared); without a chat id it lists them.

``/lark-user reply.<key> [value]`` (or ``chats.<chat_id>.reply.<key>``) changes a reply setting in
memory for this gateway process; ``lark_user_overrides.py`` has the rules.

Only the user the plugin speaks for may use it: a local session (CLI / TUI / desktop on this
machine), or a Feishu/Lark sender whose union_id or open_id is that user's. A colleague who can
talk to a shared bot cannot turn on replies under someone else's name.
"""

from __future__ import annotations

import time
import weakref
from typing import Any, Mapping, Optional, Protocol

USAGE = ("用法：/lark-user start（开启自动回复）| stop（关闭）| status（查看状态）"
         "| reset [会话 ID]（重置这个私聊或群的全部会话；不带 ID 列出进行中的会话）"
         "| reply[.<项> <值> | --reset]（仅在内存中修改回复设置，重启后恢复配置文件）")
_LIVE: weakref.WeakSet[Controllable] = weakref.WeakSet()


class Controllable(Protocol):
    def serves_profile(self, profile: Optional[str]) -> bool: ...
    def is_owner(self, source: Any) -> bool: ...
    async def start_auto_reply(self) -> str: ...
    async def stop_auto_reply(self) -> str: ...
    def auto_reply_status(self) -> str: ...
    async def reset_conversations(self, chat_id: str) -> str: ...
    async def reply_option(self, path: str, value: str) -> str: ...


def register_live(adapter: Controllable) -> None:
    _LIVE.add(adapter)


def unregister_live(adapter: Controllable) -> None:
    _LIVE.discard(adapter)


def conversation_list(chats: Mapping[str, list[Any]]) -> str:
    """``/lark-user reset`` without a chat id: the chats with live conversations, latest first."""
    if not chats:
        return "当前没有进行中的会话。"

    def last(entries: list[Any]) -> float:
        return max(e.updated_at.timestamp() for e in entries)

    lines = ["进行中的会话（最近活动在前）："]
    for chat_id, entries in sorted(chats.items(), key=lambda item: -last(item[1])):
        origin = entries[0].origin
        group = origin.chat_type == "group"
        name = (origin.chat_name if group else origin.user_name) or "未知"
        when = time.strftime("%m-%d %H:%M", time.localtime(last(entries)))
        lines.append(f"• {'群' if group else '私聊'}「{name}」 {chat_id}：{len(entries)} 个会话，最近活动 {when}")
    lines.append("重置某个私聊或群的全部会话：/lark-user reset <会话 ID>")
    return "\n".join(lines)


def _profile_of(source: Any) -> Optional[str]:
    return getattr(source, "profile", None) if source is not None else None


async def lark_user_command(raw_args: str, source: Any = None) -> str:
    head, _, arg = (raw_args or "").strip().partition(" ")
    verb = head.lower() or "status"
    setting = verb == "reply" or verb.startswith(("reply.", "chats."))
    if not setting and verb not in {"start", "stop", "status", "reset"}:
        return USAGE
    adapter = next((a for a in list(_LIVE) if a.serves_profile(_profile_of(source))), None)
    if adapter is None:
        return ("lark_user 没有在这个 gateway / profile 里运行。它只在 gateway 进程中运行："
                "请确认 platforms.lark_user.enabled: true，并在连接到 gateway 的会话里使用此命令。")
    if not adapter.is_owner(source):
        return "只有 lark_user 所代表的本人可以使用 /lark-user。"
    if verb == "start":
        return await adapter.start_auto_reply()
    if verb == "stop":
        return await adapter.stop_auto_reply()
    if verb == "reset":
        return await adapter.reset_conversations(arg.strip())
    if setting:  # chat ids are case-sensitive: pass the path as typed
        return await adapter.reply_option(head, arg)
    return adapter.auto_reply_status()
