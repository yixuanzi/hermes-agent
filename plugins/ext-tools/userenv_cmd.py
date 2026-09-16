"""userenv_cmd — /userenv slash 命令：用户自行托管/查询自己的 key（不经 LLM）。

设计要点：
  - 身份来源是 tools.user_env_runtime.get_current_user_env_identity()。
    gateway 侧在分发插件命令前已绑定调用者身份（gateway/run.py 插件命令
    dispatch 处），身份取自 adapter 的 SessionSource，消息文本无法伪造。
  - 响应文本直接作为命令回复返回给发起者，不进入 LLM 对话上下文，
    因此 value 可以安全回显（get 按要求打码：保留前后 4 字符）。
  - 复用 tools/user_env_store.py 现成的 load/list/set/delete，无新存储。
  - fail-closed：取不到身份时拒绝执行。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tools.user_env_store import (  # noqa: E402
    CURRENT_USER_NAME_KEY,
    delete_user_env_var,
    list_user_env,
    set_user_env_var,
)

_KEEP = 4  # get 回显时保留的前/后明文字符数


def _current_identity():
    try:
        from tools.user_env_runtime import get_current_user_env_identity

        return get_current_user_env_identity()
    except Exception:
        return None


def _mask(value: str) -> str:
    """保留前后各 _KEEP 个字符，中间打码；短值全码。"""
    text = str(value or "")
    if len(text) <= _KEEP * 2:
        return "*" * len(text)
    return f"{text[:_KEEP]}{'*' * (len(text) - _KEEP * 2)}{text[-_KEEP:]}"


def _help_text() -> str:
    return (
        "**/userenv** — 管理你自己的托管环境变量（不经过大模型）\n"
        "用法：\n"
        "  /userenv list — 列出你名下的所有 key\n"
        "  /userenv get KEY — 查询 key（值打码显示，保留前后 4 字符）\n"
        "  /userenv set KEY VALUE — 设置 key（VALUE 为 KEY 之后的所有文本）\n"
        "  /userenv delete KEY — 删除 key\n"
        "说明：数据按 平台:用户 身份分区存储，仅你本人可读写。"
    )


def _visible_keys(env: dict) -> list:
    return sorted(k for k in env if k != CURRENT_USER_NAME_KEY)


def _parse(raw_args: str):
    """返回 (subcommand, key, value_text)。value 取 KEY 之后的全部原文。"""
    text = str(raw_args or "").strip()
    if not text:
        return "", "", ""
    parts = text.split(None, 2)
    sub = parts[0].lower()
    key = parts[1] if len(parts) > 1 else ""
    value = parts[2] if len(parts) > 2 else ""
    return sub, key, value


def userenv_command(raw_args: str = "") -> str:
    sub, key, value = _parse(raw_args)

    # 帮助在任何情况下可用（纯静态文本，无数据访问）
    if sub in ("", "help", "-h", "--help"):
        return _help_text()

    identity = _current_identity()
    if identity is None:
        return (
            "❌ 无法识别当前用户身份，/userenv 仅对已认证的运行时用户可用。"
        )

    if sub == "list":
        loaded = list_user_env(identity.platform, identity.user_id, identity.user_name)
        keys = _visible_keys(loaded.env)
        if not keys:
            return f"📭 你（{identity.platform}:{identity.user_id}）名下暂无托管变量。"
        lines = [f"📋 你（{identity.platform}:{identity.user_id}）名下共 {len(keys)} 个 key："]
        lines += [f"  • `{k}`" for k in keys]
        return "\n".join(lines)

    if sub == "get":
        if not key:
            return "用法：/userenv get KEY"
        loaded = list_user_env(identity.platform, identity.user_id, identity.user_name)
        if key not in loaded.env:
            return f"❌ key 不存在：`{key}`（用 /userenv list 查看你名下的 key）"
        return f"🔑 `{key}` = `{_mask(loaded.env[key])}`（打码显示，保留前后 {_KEEP} 字符）"

    if sub == "set":
        if not key:
            return "用法：/userenv set KEY VALUE"
        try:
            set_user_env_var(
                identity.platform, identity.user_id, identity.user_name,
                key, value,
            )
        except ValueError as exc:
            return f"❌ 设置失败：{exc}"
        return f"✅ 已设置 `{key}`（值不回显）"

    if sub == "delete":
        if not key:
            return "用法：/userenv delete KEY"
        try:
            loaded, deleted = delete_user_env_var(
                identity.platform, identity.user_id, identity.user_name, key
            )
        except ValueError as exc:
            return f"❌ 删除失败：{exc}"
        if not deleted:
            return f"❌ key 不存在：`{key}`"
        return f"🗑️ 已删除 `{key}`，剩余 {_len_visible(loaded.env)} 个"

    return f"未知子命令：`{sub}`\n\n{_help_text()}"


def _len_visible(env: dict) -> int:
    return len(_visible_keys(env))


def register(ctx):
    ctx.register_command(
        name="userenv",
        handler=userenv_command,
        description=(
            "管理当前用户自己的托管环境变量（list/get/set/delete），"
            "不经过大模型，key 值不进入对话上下文"
        ),
        args_hint="list | get KEY | set KEY VALUE | delete KEY",
    )
