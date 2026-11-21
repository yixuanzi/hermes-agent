"""``/lark-user start|stop|status``: switch auto-reply on and off while the gateway runs.

The switch lives in memory only. Every gateway start (and every reconnect of the adapter) begins
with auto-reply OFF: nothing is polled and nothing is answered until the user runs
``/lark-user start``. Messages that arrive while it is off are history, never answered later.

Only the user the plugin speaks for may flip it: a local session (CLI / TUI / desktop on this
machine), or a Feishu/Lark sender whose union_id or open_id is that user's. A colleague who can
talk to a shared bot cannot turn on replies under someone else's name.
"""

from __future__ import annotations

import weakref
from typing import Any, Optional, Protocol

USAGE = "用法：/lark-user start（开启自动回复）| stop（关闭）| status（查看状态）"
_LIVE: weakref.WeakSet[Controllable] = weakref.WeakSet()


class Controllable(Protocol):
    def serves_profile(self, profile: Optional[str]) -> bool: ...
    def is_owner(self, source: Any) -> bool: ...
    async def start_auto_reply(self) -> str: ...
    async def stop_auto_reply(self) -> str: ...
    def auto_reply_status(self) -> str: ...


def register_live(adapter: Controllable) -> None:
    _LIVE.add(adapter)


def unregister_live(adapter: Controllable) -> None:
    _LIVE.discard(adapter)


def _profile_of(source: Any) -> Optional[str]:
    return getattr(source, "profile", None) if source is not None else None


async def lark_user_command(raw_args: str, source: Any = None) -> str:
    verb = (raw_args or "").strip().lower() or "status"
    if verb not in {"start", "stop", "status"}:
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
    return adapter.auto_reply_status()
