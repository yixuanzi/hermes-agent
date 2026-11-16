"""ext-tools tool_record：/codex、/claude 回合只把用户输入与最终回复记入 Hermes 会话。"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "plugins" / "ext-tools"))

import tool_record
from agent.transports.codex_app_server_session import TurnResult

# codex / Claude Code 一轮的典型投影：中间说明、工具调用与结果、回合中 steer、带思考的最终回复
PROJECTED = [
    {"role": "assistant", "content": "Checking.", "reasoning": "need to look"},
    {"role": "assistant", "content": None, "tool_calls": [{
        "id": "call_1", "type": "function",
        "function": {"name": "exec_command", "arguments": '{"cmd": "ls"}'},
    }]},
    {"role": "tool", "tool_call_id": "call_1", "content": "a.py\nb.py"},
    {"role": "user", "content": "only python files"},
    {"role": "assistant", "content": "Found a.py and b.py.", "reasoning": "listed"},
]


class FakeSession:
    def __init__(self, final_text="Found a.py and b.py."):
        self.final_text = final_text
        self.inputs = []

    def run_turn(self, user_input, *, turn_timeout=600.0):
        self.inputs.append(user_input)
        result = TurnResult()
        result.projected_messages = [dict(m) for m in PROJECTED]
        result.final_text = self.final_text
        return result


@pytest.fixture(autouse=True)
def _default_env(monkeypatch):
    monkeypatch.delenv(tool_record.ENV_VAR, raising=False)


def _session(**kwargs):
    session = FakeSession(**kwargs)
    tool_record.install(session)
    return session


def test_default_keeps_only_user_input_and_final_reply():
    session = _session()
    turn = session.run_turn("list files", turn_timeout=5.0)

    assert session.inputs == ["list files"]
    assert turn.projected_messages == [
        {"role": "user", "content": "only python files"},
        {"role": "assistant", "content": "Found a.py and b.py."},
    ]
    assert turn.final_text == "Found a.py and b.py." and turn.tool_iterations == 0


@pytest.mark.parametrize("value", ["true", "TRUE", "1", "yes", "on"])
def test_enabled_records_the_whole_turn(monkeypatch, value):
    monkeypatch.setenv(tool_record.ENV_VAR, value)
    assert _session().run_turn(user_input="list files").projected_messages == PROJECTED


@pytest.mark.parametrize("value", ["false", "0", "", "no"])
def test_other_values_keep_the_default(monkeypatch, value):
    monkeypatch.setenv(tool_record.ENV_VAR, value)
    assert len(_session().run_turn("list files").projected_messages) == 2


def test_no_final_reply_stores_no_assistant_message():
    # 中断或出错、没有最终回复：不把中间说明当成回复记进去
    turn = _session(final_text="").run_turn("list files")
    assert turn.projected_messages == [{"role": "user", "content": "only python files"}]


def test_install_is_idempotent():
    session = _session()
    run_turn = session.run_turn
    tool_record.install(session)
    assert session.run_turn is run_turn
