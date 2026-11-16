"""Owner-facing surface of CodexAppServerSession: per-thread start params, the policy codex reports,
read-only state, re-binding hooks to a new owner, and the owner's projection limits."""

from __future__ import annotations

from agent.transports.codex_app_server_session import (
    CodexAppServerSession,
    _effective_policy_from_thread_start,
    _ServerRequestRouting,
)
from agent.transports.codex_event_projector import ProjectionLimits
from tests.agent.transports.test_codex_app_server_session import FakeClient, make_session

_WORKSPACE_POLICY_RESPONSE = {
    "thread": {"id": "thread-policy"},
    "approvalPolicy": "on-request",
    "sandbox": {
        "type": "workspaceWrite", "writableRoots": ["/data"], "networkAccess": False,
        "excludeTmpdirEnvVar": True, "excludeSlashTmp": False,
    },
    "activePermissionProfile": {"id": ":workspace", "extends": None},
}


def test_session_exposes_cwd_closed_and_turn_state_and_rebinds_hooks(tmp_path):
    session = CodexAppServerSession(cwd=str(tmp_path), on_event=lambda note: None)
    assert session.cwd == str(tmp_path)
    assert session.closed is False
    assert session.turn_active is False

    seen: list = []
    routing = _ServerRequestRouting(auto_approve_exec=True)
    session.rebind(approval_callback=None, on_event=seen.append, request_routing=routing)
    session._on_event({"method": "x"})
    assert seen == [{"method": "x"}]
    assert session._routing is routing

    session.close()
    assert session.closed is True


def test_thread_start_records_the_policy_codex_reports():
    client = FakeClient()
    client._request_handler = lambda method, params: _WORKSPACE_POLICY_RESPONSE if method == "thread/start" else {}
    session = make_session(client)
    assert session.effective_policy is None

    session.ensure_started()

    assert session.effective_policy == {
        "permission_profile": ":workspace", "approval_policy": "on-request", "sandbox": "workspaceWrite",
        "network_access": False, "writable_roots": ["/data"], "slash_tmp_writable": True, "tmpdir_writable": False,
    }


def test_resumed_thread_records_the_policy_codex_reports():
    client = FakeClient()

    def handler(method, params):
        if method == "thread/resume":
            return {**_WORKSPACE_POLICY_RESPONSE, "thread": {"id": params["threadId"]}}
        return {}

    client._request_handler = handler
    session = make_session(client, resume_thread_id="thread-stored")

    assert session.ensure_started() == "thread-stored"
    assert session.effective_policy["sandbox"] == "workspaceWrite"


def test_read_only_policy_has_no_writable_tmp():
    policy = _effective_policy_from_thread_start({
        "approvalPolicy": "on-request",
        "sandbox": {"type": "readOnly", "networkAccess": False},
        "activePermissionProfile": {"id": ":read-only"},
    })
    assert policy["sandbox"] == "readOnly"
    assert policy["slash_tmp_writable"] is False
    assert policy["tmpdir_writable"] is False


def test_thread_start_params_ride_along_but_never_replace_hermes_fields():
    client = FakeClient()
    session = CodexAppServerSession(
        cwd="/work", client_factory=lambda **kw: client,
        thread_start_params={"sandbox": "danger-full-access", "approvalPolicy": "never", "cwd": "/ignored",
                             "personality": "pirate"},
    )

    session.ensure_started()

    [(_, params)] = [request for request in client.requests if request[0] == "thread/start"]
    assert params["sandbox"] == "danger-full-access"
    assert params["approvalPolicy"] == "never"
    assert params["cwd"] == "/work"
    assert params["personality"] == "none"
    assert session.thread_start_params["sandbox"] == "danger-full-access"


def _queue_tool_turn(client: FakeClient, output: str) -> None:
    client.queue_notification("turn/started", threadId="t", turn={"id": "tu1"})
    client.queue_notification("item/completed", threadId="t", turnId="tu1",
                              item={"type": "reasoning", "id": "r1", "summary": ["thinking"], "content": []})
    client.queue_notification("item/completed", threadId="t", turnId="tu1", item={
        "type": "commandExecution", "id": "c1", "command": "x" * 50, "cwd": "/w",
        "aggregatedOutput": output, "exitCode": 0,
    })
    client.queue_notification("turn/completed", threadId="t", turn={"id": "tu1", "status": "completed", "error": None})


def test_owner_projection_limits_clamp_what_reaches_hermes_history():
    client = FakeClient()
    _queue_tool_turn(client, "H" * 30 + "M" * 100 + "T" * 20)
    limits = ProjectionLimits(command_head_chars=30, command_tail_chars=20, args_field_chars=10)
    session = make_session(client, projection_limits=limits)

    result = session.run_turn("go", turn_timeout=2.0)

    call, tool = result.projected_messages[-2:]
    assert "reasoning" not in call
    assert '"command": "xxxxxxxxxx…[clipped 40 chars]"' in call["tool_calls"][0]["function"]["arguments"]
    assert "H" * 30 in tool["content"] and "T" * 20 in tool["content"]
    assert "M" not in tool["content"]
    assert "[elided 100 chars]" in tool["content"]


def test_session_without_limits_projects_the_full_turn():
    client = FakeClient()
    _queue_tool_turn(client, "H" * 5000)
    session = make_session(client)

    result = session.run_turn("go", turn_timeout=2.0)

    call, tool = result.projected_messages[-2:]
    assert call["reasoning"] == "thinking"
    assert "H" * 5000 in tool["content"]
