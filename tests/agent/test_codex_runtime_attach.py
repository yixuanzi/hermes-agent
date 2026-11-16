"""A codex session its owner attaches (``attach_codex_app_server_session``) runs on the owner's settings.

Plugin backends (``/codex``, ``/claude``) bind their own session to the AIAgent: Hermes must not retire it,
override its turns with Hermes' model / effort / tier, or publish its thread as the session's resumable codex
thread. Hermes' own session keeps all of that (tests/agent/test_codex_runtime_prompt_handoff.py).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent import codex_runtime
from agent.codex_runtime import attach_codex_app_server_session, run_codex_app_server_turn
from agent.transports import codex_app_server_session as sess_mod
from agent.transports.codex_app_server_session import TurnResult
from agent.transports.codex_event_projector import ProjectionLimits
from hermes_state import SessionDB
from run_agent import AIAgent

SESSION_ID = "sess-owner"


class _OwnerSession:
    """A backend the owner drives: records what Hermes hands each turn."""

    def __init__(self, agent_probe=None) -> None:
        self.turn_kwargs: list[dict] = []
        self.rebinds: list[dict] = []
        self.closed_calls = 0
        self._agent_probe = agent_probe

    def rebind(self, **hooks) -> None:
        self.rebinds.append(hooks)

    def ensure_started(self) -> str:
        return "owner-thread"

    def run_turn(self, **kwargs) -> TurnResult:
        self.turn_kwargs.append(kwargs)
        if self._agent_probe is not None:
            self._agent_probe()
        return TurnResult(final_text="done", projected_messages=[{"role": "assistant", "content": "done"}],
                          thread_id="owner-thread", turn_id="owner-turn")

    def close(self) -> None:
        self.closed_calls += 1


@pytest.fixture
def agent(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    db.create_session(session_id=SESSION_ID, source="cli", model="deepseek-v4-flash")
    built = AIAgent(api_key="test-key", base_url="https://openrouter.ai/api/v1", model="deepseek-v4-flash",
                    quiet_mode=True, skip_context_files=True, skip_memory=True, session_db=db, session_id=SESSION_ID)
    built._session_db_created = True
    built.tool_progress_callback = None
    yield built
    db.close()


def _run_turn(agent) -> dict:
    messages = [{"role": "user", "content": "hi"}]
    agent._flush_messages_to_session_db(messages)
    return run_codex_app_server_turn(agent, user_message="hi", original_user_message="hi", messages=messages,
                                     effective_task_id="task-1")


def test_owner_attached_session_runs_on_its_own_settings(agent):
    session = _OwnerSession()
    attach_codex_app_server_session(agent, session)

    result = _run_turn(agent)

    assert result["final_response"] == "done"
    # Only the user's input: no Hermes model / effort / tier override for the owner's backend.
    assert session.turn_kwargs == [{"user_input": "hi"}]
    assert agent._codex_session is session
    assert session.closed_calls == 0
    # The owner's thread (or a non-codex backend's session id) is not Hermes' resumable codex thread.
    assert agent._session_db.get_session_model_config_value(SESSION_ID, "codex_thread_id") is None


def test_attaching_an_existing_session_rebinds_its_hooks_to_the_new_agent(agent):
    session = _OwnerSession()

    assert attach_codex_app_server_session(agent, session) is session

    [hooks] = session.rebinds
    assert set(hooks) == {"approval_callback", "on_event", "request_routing"}
    assert callable(hooks["on_event"])


def test_hermes_never_retires_an_owner_attached_session(agent):
    session = _OwnerSession()
    # A composition recorded for an earlier Hermes-built session must not make the owner's one look stale.
    agent._codex_session_prompt = "a prompt recorded for a previous Hermes session"
    attach_codex_app_server_session(agent, session)

    codex_runtime._ensure_codex_session(agent)

    assert agent._codex_session is session
    assert session.closed_calls == 0


def test_each_codex_turn_starts_from_a_clean_stream_segment(agent):
    seen = {}

    def probe():
        seen["streamed"] = agent._current_streamed_assistant_text
        seen["needs_break"] = agent._stream_needs_break

    attach_codex_app_server_session(agent, _OwnerSession(agent_probe=probe))
    agent._current_streamed_assistant_text = "the previous turn's interrupted stream"
    agent._stream_needs_break = True

    _run_turn(agent)

    assert seen == {"streamed": "", "needs_break": False}


def test_attach_creates_a_session_with_the_owner_thread_params_and_projection(monkeypatch):
    clients = []

    class _Client:
        def __init__(self, **_kw):
            self.requests = []
            clients.append(self)

        def initialize(self, **_kw):
            return {}

        def request(self, method, params=None, timeout=None):
            self.requests.append((method, params))
            return {"thread": {"id": "t1"}}

        def close(self):
            pass

    monkeypatch.setattr(sess_mod, "CodexAppServerClient", _Client)
    owner = SimpleNamespace(_codex_session=None, session_cwd="/tmp", tool_progress_callback=None)
    limits = ProjectionLimits(command_head_chars=10)

    session = attach_codex_app_server_session(owner, cwd="/work", thread_start_params={"sandbox": "read-only"},
                                              projection_limits=limits)
    session.ensure_started()

    assert owner._codex_session is session
    assert session.cwd == "/work"
    assert session._projection_limits is limits
    [(_, params)] = [r for r in clients[0].requests if r[0] == "thread/start"]
    assert params["sandbox"] == "read-only"
    assert "model" not in params
    assert "developerInstructions" not in params
