"""``/lark-user reply.<key> [value]``: change how replies go out, in memory only.

An override is a ``platforms.lark_user`` path (``reply.format``, ``chats.<chat_id>.reply.prefix``)
and a value, laid over config.yaml and validated by the same ``load_settings`` that reads it, so a
value the config would refuse (an empty prefix, an unknown format) is refused here too. Nothing is
written anywhere: an override lasts for this gateway process (an adapter that a reconnect retry
rebuilds takes it over, keyed by profile) and a restart starts from config.yaml again.
"""

from __future__ import annotations

import copy
import json
import re
from typing import Any, Mapping, Optional

from .lark_user_config import DELIVERIES, FORMATS, TARGETS, LarkUserSettings, ReplySettings

# key -> ReplySettings field
KEYS = {"delivery": "delivery", "format": "format", "target": "target", "at_sender": "at_sender",
        "prefix": "prefix", "suffix": "suffix", "card.title": "card_title"}
_CHOICES = {"delivery": DELIVERIES, "format": FORMATS, "target": TARGETS,
            "at_sender": ("true", "false", "yes", "no", "on", "off", "1", "0")}
_PATH_RE = re.compile(r"^(?:chats\.(?P<chat>[^.\s]+)\.)?reply\.(?P<key>[A-Za-z_.]+)$")
RESET = "--reset"

Path = tuple[str, str]  # (chat_id, or "" for every chat; key)

_BY_PROFILE: dict[str, dict[Path, Any]] = {}


class OverrideError(ValueError):
    """A ``/lark-user reply…`` argument that names no reply setting or cannot be a value."""


def for_profile(profile: Optional[str]) -> dict[Path, Any]:
    """The live overrides of one profile (``None`` = the default profile)."""
    return _BY_PROFILE.setdefault((profile or "").strip() or "default", {})


def parse_path(text: str) -> Path:
    match = _PATH_RE.match(text.strip())
    key = match.group("key").lower() if match else ""
    if key not in KEYS:
        raise OverrideError(f"没有这个回复设置：{text}。可用：{', '.join('reply.' + k for k in KEYS)}，"
                            "或在前面加 chats.<会话 ID>. 只改一个会话。")
    return match.group("chat") or "", key


def parse_value(key: str, text: str) -> Any:
    """Command text -> value. Quote a value to keep its edge spaces or write escapes: ``"…"`` is
    decoded like a JSON string (``\\n``), ``'…'`` is taken literally (``''`` is one quote)."""
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise OverrideError(f"引号里的内容无法解析：{exc.msg}") from None
    elif len(text) >= 2 and text[0] == text[-1] == "'":
        value = text[1:-1].replace("''", "'")
    else:
        value = text
    choices = _CHOICES.get(key)
    if choices and value.lower() not in choices:
        raise OverrideError(f"reply.{key} 只能是 {' | '.join(choices[:2] if key == 'at_sender' else choices)}"
                            f"（收到 {value!r}）")
    return value.lower() in {"true", "yes", "on", "1"} if key == "at_sender" else value


def label(path: Path) -> str:
    chat, key = path
    return f"{f'chats.{chat}.' if chat else ''}reply.{key}"


def layered(extra: Mapping[str, Any], overrides: Mapping[Path, Any]) -> dict[str, Any]:
    """A copy of the ``platforms.lark_user`` block with every override set at its path."""
    raw = copy.deepcopy(dict(extra))
    for (chat, key), value in overrides.items():
        node = raw
        for part in (["chats", chat] if chat else []) + ["reply", *key.split(".")[:-1]]:
            if not isinstance(node.get(part), dict):
                node[part] = {}
            node = node[part]
        node[key.split(".")[-1]] = value
    return raw


def value_of(reply: ReplySettings, key: str) -> Any:
    return getattr(reply, KEYS[key])


def show(value: Any) -> str:
    return str(value).lower() if isinstance(value, bool) else json.dumps(value, ensure_ascii=False)


def describe(settings: LarkUserSettings, configured: LarkUserSettings, overrides: Mapping[Path, Any],
             path: Optional[Path] = None) -> str:
    """The effective reply settings (one path, or all of them), marking what was changed in memory."""
    def line(chat: str, key: str) -> str:
        now = value_of(settings.for_chat(chat).reply, key)
        text = f"{label((chat, key))} = {show(now)}"
        if (chat, key) in overrides:
            text += f"   ← 运行时修改（配置文件：{show(value_of(configured.for_chat(chat).reply, key))}）"
        return text

    if path is not None:
        chat, key = path
        shadowed = not chat and chat_overrides(settings, key)
        return line(chat, key) + (f"\n注意：{', '.join(shadowed)} 有单独设置，不受全局值影响。" if shadowed else "")
    lines = ["回复设置（运行时修改只在本进程有效，重启后按 config.yaml）："]
    lines += [line("", key) for key in KEYS]
    for chat in sorted({c for c, _ in overrides if c} | set(settings.chats)):
        keys = [k for k in KEYS if (chat, k) in overrides
                or value_of(settings.for_chat(chat).reply, k) != value_of(settings.defaults.reply, k)]
        lines += [line(chat, key) for key in keys]
    lines.append("修改：/lark-user reply.<项> <值>；恢复：/lark-user reply.<项> --reset，全部恢复：/lark-user reply --reset")
    return "\n".join(lines)


def chat_overrides(settings: LarkUserSettings, key: str) -> list[str]:
    """Chats whose own ``reply.<key>`` differs from the global one."""
    return [f"chats.{chat}" for chat, cfg in sorted(settings.chats.items())
            if value_of(cfg.reply, key) != value_of(settings.defaults.reply, key)]
