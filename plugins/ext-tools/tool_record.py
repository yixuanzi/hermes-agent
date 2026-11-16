"""tool_record — /codex、/claude 回合的中间过程是否记入 Hermes 会话。

环境变量 THIRDAGENT_TOOL_RECORD（默认 false）：
  - false：codex / Claude Code 回合里的中间消息、工具调用与结果、思考内容都不进 Hermes 会话上下文，
    也不写会话库；只留回合中用户追加的输入（steer）与最终回复。本轮的用户消息在交给后端之前就已由
    Hermes 记录，不经过这里。
  - true：整轮投影原样记录（工具调用、结果、中间消息全部入库）。
界面上的实时进度（工具卡片、中间消息）不受影响；每轮都重新读取，改了环境变量重启后生效。

做法：包一层会话后端的 run_turn，在 core 把 TurnResult.projected_messages 拼进会话、写库之前过滤。
"""

from __future__ import annotations

import os
from typing import Any

ENV_VAR = "THIRDAGENT_TOOL_RECORD"
_TRUE = frozenset({"1", "true", "yes", "on"})


def record_enabled() -> bool:
    return os.environ.get(ENV_VAR, "").strip().lower() in _TRUE


def install(session: Any) -> None:
    """codex / Claude Code 会话的 run_turn 结果按 THIRDAGENT_TOOL_RECORD 过滤（幂等）。"""
    if session is None or getattr(session, "_ext_tool_record_wrapped", False):
        return
    original = session.run_turn

    def run_turn(user_input, **kwargs):
        result = original(user_input=user_input, **kwargs)
        if not record_enabled() and hasattr(result, "projected_messages"):
            result.projected_messages = final_only(result.projected_messages, result.final_text)
        return result

    session.run_turn = run_turn
    session._ext_tool_record_wrapped = True


def final_only(messages: Any, final_text: Any) -> list[dict]:
    """只留用户消息（回合中的 steer 输入）与一条最终回复（就是返回给用户的 final_text）。"""
    kept = [m for m in messages or [] if isinstance(m, dict) and m.get("role") == "user"]
    text = str(final_text or "")
    if text.strip():
        kept.append({"role": "assistant", "content": text})
    return kept
