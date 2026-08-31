import asyncio
import json
import re
import threading
import xml.etree.ElementTree as ET
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from run_agent import AIAgent
from toolsets import TOOLSETS, _HERMES_CORE_TOOLS
from tools.registry import registry


def _make_parent():
    parent = MagicMock()
    parent.base_url = "https://openrouter.ai/api/v1"
    parent.api_key = "***"
    parent.provider = "openrouter"
    parent.api_mode = "chat_completions"
    parent.model = "anthropic/claude-sonnet-4"
    parent.platform = "cli"
    parent._user_env_platform = None
    parent.reasoning_config = None
    parent.prefill_messages = None
    parent.max_tokens = None
    parent._fallback_chain = None
    parent.providers_allowed = None
    parent.providers_ignored = None
    parent.providers_order = None
    parent.provider_sort = None
    parent.openrouter_min_coding_score = None
    parent._session_db = None
    parent.session_id = "parent-session"
    parent._print_fn = None
    parent._credential_pool = None
    parent._active_children = []
    parent._active_children_lock = threading.Lock()
    parent._current_task_id = "parent-task"
    return parent


class _OutputSink:
    def __init__(self):
        self.events = []

    def emit(self, source, event_type, content, session_id=None):
        self.events.append((source, event_type, content, session_id))


class _Input:
    def __init__(self, values):
        self._values = iter(values)
        self.entered = False
        self.exited = False
        self.timeouts = []

    def enter_foreground(self):
        self.entered = True
        return True

    def exit_foreground(self):
        self.exited = True

    def read_line(self, timeout=None):
        self.timeouts.append(timeout)
        return next(self._values)


def test_a2a_schemas_are_registered_and_toolset_is_opt_in():
    import tools.a2a_delegate_tool as a2a_delegate_tool

    list_schema = registry.get_schema("a2a_list")
    assert list_schema is not None
    assert list_schema["parameters"]["properties"] == {}
    schema = registry.get_schema("a2a_delegate")
    assert schema is not None
    props = schema["parameters"]["properties"]
    assert {"goal", "context", "agent_name", "session_id", "is_delegate_output", "is_loop"} <= set(props)
    assert "type" not in props
    assert "toolsets" not in props
    assert "max_iterations" not in props
    assert "a2a" in TOOLSETS
    assert TOOLSETS["a2a"]["tools"] == ["a2a_list", "a2a_delegate"]
    assert "a2a_list" not in _HERMES_CORE_TOOLS
    assert "a2a_delegate" not in _HERMES_CORE_TOOLS
    assert a2a_delegate_tool.A2A_DELEGATE_SCHEMA["name"] == "a2a_delegate"


def test_a2a_list_supports_compact_json_and_bare_xml_outputs(monkeypatch, tmp_path):
    import tools.a2a_delegate_tool as a2a_delegate_tool

    registry_path = tmp_path / "a2a.json"
    registry_path.write_text(
        json.dumps(
            {
                "a2a": {
                    "responder": {
                        "url": "http://agent.local/a2a",
                        "description": "Investigates incidents",
                        "headers": {"Authorization": "Bearer secret"},
                        "status": "active",
                        "extcapabilities": ["configured-capability"],
                    },
                    "missing-url": {
                        "description": "URL is not configured",
                        "status": "active",
                    },
                    "unreachable": {
                        "url": "http://unreachable.local/a2a",
                        "status": "active",
                    },
                    "malformed": 42,
                    "inactive": {
                        "url": "http://inactive.local/a2a",
                        "status": "inactive",
                    },
                },
                "global": [
                    {
                        "id": "rule&001",
                        "name": "Route <urgent> incidents",
                        "policy": "Escalate to SOC & notify on-call.",
                        "status": "active",
                    },
                    {
                        "id": "rule0002",
                        "name": "Disabled rule",
                        "policy": "Must not be returned.",
                        "status": "inactive",
                    },
                    {"id": "invalid", "status": "active"},
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(a2a_delegate_tool, "_a2a_registry_path", lambda: registry_path)
    monkeypatch.setattr(
        a2a_delegate_tool,
        "_fetch_agent_card",
        lambda url, **kwargs: (
            (None, "connection refused")
            if "unreachable" in url
            else (
                {
                    "name": "Responder Card",
                    "description": "Remote responder",
                    "skills": [{"id": "investigate", "name": "Investigate"}],
                },
                None,
            )
        ),
    )
    monkeypatch.setattr(a2a_delegate_tool, "A2A_CONTEXT", "")

    compact = json.loads(a2a_delegate_tool.a2a_list(otype="json"))
    assert compact["success"] is True
    assert "context" not in compact
    agents = {agent["name"]: agent for agent in compact["agents"]}
    assert set(agents) == {"responder", "missing-url", "unreachable", "malformed"}
    assert agents["responder"]["capabilities"] == [
        "id=investigate | name=Investigate",
        "configured-capability",
    ]
    assert "agent_card_name" not in agents["responder"]
    assert "headers" not in agents["responder"]
    assert "extcapabilities" not in agents["responder"]
    assert "agent_card" not in agents["responder"]
    assert agents["missing-url"] == {
        "name": "missing-url",
        "url": "",
        "description": "URL is not configured",
        "status": "active",
        "available": False,
        "error": "missing url",
    }
    assert agents["unreachable"]["available"] is False
    assert agents["unreachable"]["error"] == "connection refused"
    assert agents["malformed"]["available"] is False
    assert "must be a URL string or object" in agents["malformed"]["error"]
    assert compact["global_routing"] == [
        {
            "id": "rule&001",
            "name": "Route <urgent> incidents",
            "policy": "Escalate to SOC & notify on-call.",
            "status": "active",
        }
    ]

    # XML is the default output so agents receive the compact Aegis context
    # unless a caller explicitly asks for the JSON inspection format.
    xml = a2a_delegate_tool.a2a_list()
    assert xml.startswith("<aegis_context>\n  <agents>\n")
    assert xml.endswith("</aegis_context>")
    assert "<agent name=\"responder\"" in xml
    assert "<global_routing>" in xml
    assert '<rule id="rule&amp;001" status="active">' in xml
    assert "<name>Route &lt;urgent&gt; incidents</name>" in xml
    assert "<policy>Escalate to SOC &amp; notify on-call.</policy>" in xml
    assert "Disabled rule" not in xml
    assert "Remote responder" not in xml
    assert "Bearer secret" not in xml
    assert "configured-capability" in xml

    root = ET.fromstring(xml)
    xml_agents = {agent.attrib["name"]: agent for agent in root.find("agents") or []}
    assert set(xml_agents) == set(agents)
    assert xml_agents["responder"].attrib == {
        "name": "responder",
        "url": "http://agent.local/a2a",
        "status": "active",
        "available": "true",
    }
    assert xml_agents["responder"].find("agent_card_name") is None
    assert [item.text for item in xml_agents["responder"].find("capabilities") or []] == agents[
        "responder"
    ]["capabilities"]
    assert xml_agents["missing-url"].attrib == {
        "name": "missing-url",
        "url": "",
        "status": "active",
        "available": "false",
    }
    assert xml_agents["missing-url"].findtext("description") == agents["missing-url"]["description"]
    assert xml_agents["missing-url"].findtext("error") == agents["missing-url"]["error"]
    assert xml_agents["unreachable"].attrib == {
        "name": "unreachable",
        "url": "http://unreachable.local/a2a",
        "status": "active",
        "available": "false",
    }
    assert xml_agents["unreachable"].findtext("error") == agents["unreachable"]["error"]
    assert xml_agents["malformed"].attrib == {"name": "malformed", "available": "false"}
    assert xml_agents["malformed"].findtext("error") == agents["malformed"]["error"]
    assert a2a_delegate_tool.A2A_CONTEXT == xml


def test_a2a_list_keeps_an_empty_global_routing_container(monkeypatch, tmp_path):
    import tools.a2a_delegate_tool as a2a_delegate_tool

    registry_path = tmp_path / "a2a.json"
    registry_path.write_text(json.dumps({"a2a": {}, "global": []}), encoding="utf-8")
    monkeypatch.setattr(a2a_delegate_tool, "_a2a_registry_path", lambda: registry_path)

    xml = a2a_delegate_tool.a2a_list(otype="xml")

    assert "  <agents>\n  </agents>" in xml
    assert "  <global_routing>\n  </global_routing>" in xml


def test_a2a_list_rejects_unknown_output_type():
    from tools.a2a_delegate_tool import a2a_list

    assert json.loads(a2a_list(otype="yaml")) == {
        "error": "otype must be 'json' or 'xml'"
    }


def test_remote_delegate_session_uses_default_poll_settings(monkeypatch):
    from tools.a2a_delegate_tool import _A2ADelegateSession

    monkeypatch.delenv("A2A_POLL_TIMEOUT", raising=False)
    monkeypatch.delenv("A2A_POLL_INTERVAL", raising=False)
    session = _A2ADelegateSession("http://agent.local/a2a")

    assert session.timeout == 120.0
    assert session.poll_interval == 1.0


def test_remote_delegate_session_reads_poll_settings_from_environment(monkeypatch):
    from tools.a2a_delegate_tool import _A2ADelegateSession

    monkeypatch.setenv("A2A_POLL_TIMEOUT", "180.5")
    monkeypatch.setenv("A2A_POLL_INTERVAL", "0.25")

    session = _A2ADelegateSession("http://agent.local/a2a")

    assert session.timeout == 180.5
    assert session.poll_interval == 0.25


def test_remote_delegate_session_rejects_invalid_poll_settings(monkeypatch):
    from tools.a2a_delegate_tool import _A2ADelegateSession

    monkeypatch.setenv("A2A_POLL_TIMEOUT", "not-a-number")
    monkeypatch.setenv("A2A_POLL_INTERVAL", "-1")

    session = _A2ADelegateSession("http://agent.local/a2a")

    assert session.timeout == 120.0
    assert session.poll_interval == 1.0


def test_remote_task_poll_refreshes_parent_activity():
    from tools.a2a_delegate_tool import _A2ADelegateSession, _run_coro_sync

    parent = _make_parent()
    parent._touch_activity = MagicMock()
    completed_task = SimpleNamespace(
        id="task-1",
        context_id="ctx-1",
        history=[],
        status=SimpleNamespace(state="completed", message=None),
    )

    class FakeClient:
        async def get_task(self, request):
            assert request.id == "task-1"
            return completed_task

    session = _A2ADelegateSession(
        "http://agent.local/a2a",
        parent_agent=parent,
        poll_interval=0,
    )
    session._client = FakeClient()
    initial_task = SimpleNamespace(
        id="task-1",
        context_id="ctx-1",
        history=[],
        status=SimpleNamespace(state="working", message=None),
    )

    result = _run_coro_sync(session._wait_for_final(initial_task))

    assert result is completed_task
    parent._touch_activity.assert_called_once_with(
        "a2a_delegate: received remote task update"
    )


def test_remote_task_deadline_pauses_while_interaction_is_pending():
    from a2a.types import Message, Part, Role
    from tools.a2a_delegate_tool import _A2ADelegateSession, _run_coro_sync

    output = _OutputSink()
    interaction = Message(
        message_id="interaction-message",
        role=Role.ROLE_AGENT,
        context_id="ctx-1",
        task_id="task-1",
        parts=[Part(text="")],
        metadata={
            "hermes": {
                "kind": "approval_request",
                "interaction_id": "approval-1",
                "task_id": "task-1",
                "context_id": "ctx-1",
                "command": "chmod 777 ./artifact",
                "choices": ["once", "deny"],
            }
        },
    )
    completed_task = SimpleNamespace(
        id="task-1",
        context_id="ctx-1",
        history=[],
        status=SimpleNamespace(state="completed", message=None),
    )

    class FakeClient:
        async def get_task(self, request):
            assert request.id == "task-1"
            return completed_task

    session = _A2ADelegateSession(
        "http://agent.local/a2a",
        output=output,
        timeout=0.01,
        poll_interval=0.03,
        session_id="ctx-1",
        interaction_supported=True,
    )
    session._client = FakeClient()
    initial_task = SimpleNamespace(
        id="task-1",
        context_id="ctx-1",
        history=[interaction],
        status=SimpleNamespace(state="working", message=None),
    )

    result = _run_coro_sync(session._wait_for_final(initial_task))

    assert result is completed_task
    assert output.events[0][1] == "approval_request"


def test_remote_task_deadline_resumes_after_interaction_is_resolved():
    from a2a.types import Message, Part, Role
    from tools.a2a_delegate_tool import _A2ADelegateSession, _run_coro_sync

    output = _OutputSink()

    def interaction_message(kind):
        return Message(
            message_id=f"{kind}-message",
            role=Role.ROLE_AGENT,
            context_id="ctx-1",
            task_id="task-1",
            parts=[Part(text="")],
            metadata={
                "hermes": {
                    "kind": kind,
                    "interaction_id": "approval-1",
                    "task_id": "task-1",
                    "context_id": "ctx-1",
                }
            },
        )

    resolved_task = SimpleNamespace(
        id="task-1",
        context_id="ctx-1",
        history=[interaction_message("approval_resolved")],
        status=SimpleNamespace(state="working", message=None),
    )
    completed_task = SimpleNamespace(
        id="task-1",
        context_id="ctx-1",
        history=[],
        status=SimpleNamespace(state="completed", message=None),
    )

    class FakeClient:
        def __init__(self):
            self.tasks = iter((resolved_task, completed_task))
            self.first_poll = True

        async def get_task(self, request):
            assert request.id == "task-1"
            if self.first_poll:
                self.first_poll = False
                await asyncio.sleep(0.02)
            return next(self.tasks)

    session = _A2ADelegateSession(
        "http://agent.local/a2a",
        output=output,
        timeout=0.01,
        poll_interval=0,
        session_id="ctx-1",
        interaction_supported=True,
    )
    session._client = FakeClient()
    initial_task = SimpleNamespace(
        id="task-1",
        context_id="ctx-1",
        history=[interaction_message("approval_request")],
        status=SimpleNamespace(state="working", message=None),
    )

    result = _run_coro_sync(session._wait_for_final(initial_task))

    assert result is completed_task
    assert [event[1] for event in output.events] == [
        "approval_request",
        "approval_resolved",
    ]


def test_aegis_gate_absent_skips_policy_and_audit(monkeypatch):
    import tools.a2a_delegate_tool as a2a_delegate_tool

    monkeypatch.delenv("AEGIS_BOOTSTRAP_ADMIN_PASSWORD", raising=False)
    parent = _make_parent()
    run_delegate = MagicMock(return_value={"success": True, "type": "a2a"})
    check_delegate = MagicMock()
    monkeypatch.setattr(a2a_delegate_tool, "_run_remote_delegate", run_delegate)
    monkeypatch.setattr(
        a2a_delegate_tool.a2a_delegate_aegis,
        "run_aegis_checked_delegate",
        check_delegate,
    )

    payload = json.loads(
        a2a_delegate_tool.a2a_delegate(
            goal="Investigate",
            agent_name="responder",
            parent_agent=parent,
        )
    )

    assert payload["success"] is True
    check_delegate.assert_not_called()
    run_delegate.assert_called_once()


def test_aegis_gate_absent_preserves_agent_name_compatibility(monkeypatch):
    import tools.a2a_delegate_tool as a2a_delegate_tool

    monkeypatch.delenv("AEGIS_BOOTSTRAP_ADMIN_PASSWORD", raising=False)
    run_delegate = MagicMock(return_value={"success": False, "type": "a2a", "error": "unknown"})
    monkeypatch.setattr(a2a_delegate_tool, "_run_remote_delegate", run_delegate)

    a2a_delegate_tool.a2a_delegate(
        goal="Investigate",
        agent_name=None,
        parent_agent=_make_parent(),
    )
    assert run_delegate.call_args.kwargs["agent_name"] is None

    a2a_delegate_tool.a2a_delegate(
        goal="Investigate",
        agent_name=" responder ",
        parent_agent=_make_parent(),
    )
    assert run_delegate.call_args.kwargs["agent_name"] == " responder "


def test_aegis_deny_short_circuits_remote_and_audits(monkeypatch, tmp_path):
    import tools.a2a_delegate_tool as a2a_delegate_tool
    from tools.a2a_delegate_aegis import AegisDelegateStore

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("AEGIS_BOOTSTRAP_ADMIN_PASSWORD", "configured")
    store = AegisDelegateStore(tmp_path / "aegis.db")
    store.create_policy(
        rank_id=1,
        platform="aegis",
        user_id="u-1",
        agent_name="responder",
        status="deny",
    )
    parent = _make_parent()
    parent.platform = "aegis"
    parent._user_id = "u-1"
    parent._user_name = "Alice"
    run_delegate = MagicMock()
    monkeypatch.setattr(a2a_delegate_tool, "_run_remote_delegate", run_delegate)

    payload = json.loads(
        a2a_delegate_tool.a2a_delegate(
            goal="Investigate",
            agent_name="responder",
            session_id=None,
            is_loop=True,
            is_delegate_output=False,
            parent_agent=parent,
        )
    )

    assert payload["success"] is False
    assert payload["authorization"] == "denied"
    assert payload["session_id"] == ""
    run_delegate.assert_not_called()
    audits = store.query_audits().logs
    assert len(audits) == 1
    assert audits[0] == {
        "id": audits[0]["id"],
        "timestamp": audits[0]["timestamp"],
        "platform": "aegis",
        "user_id": "u-1",
        "user_name": "Alice",
        "agent_name": "responder",
        "goal": "Investigate",
        "session_id": "",
        "is_loop": True,
        "is_delegate_output": False,
        "status": "fail",
    }


def test_aegis_nested_subagent_uses_originating_platform_for_policy(monkeypatch, tmp_path):
    import tools.a2a_delegate_tool as a2a_delegate_tool
    from tools.a2a_delegate_aegis import AegisDelegateStore

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("AEGIS_BOOTSTRAP_ADMIN_PASSWORD", "configured")
    store = AegisDelegateStore(tmp_path / "aegis.db")
    store.create_policy(
        rank_id=1,
        platform="aegis",
        user_id="u-1",
        agent_name="responder",
        status="deny",
    )
    child = _make_parent()
    child.platform = "subagent"
    child._user_env_platform = "aegis"
    child._user_id = "u-1"
    child._user_name = "Alice"
    run_delegate = MagicMock()
    monkeypatch.setattr(a2a_delegate_tool, "_run_remote_delegate", run_delegate)

    payload = json.loads(
        a2a_delegate_tool.a2a_delegate(
            goal="Investigate",
            agent_name="responder",
            parent_agent=child,
        )
    )

    assert payload["authorization"] == "denied"
    run_delegate.assert_not_called()
    assert store.query_audits().logs[0]["platform"] == "aegis"


def test_aegis_allowed_delegate_records_successful_authorization(monkeypatch, tmp_path):
    import tools.a2a_delegate_tool as a2a_delegate_tool
    from tools.a2a_delegate_aegis import AegisDelegateStore

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("AEGIS_BOOTSTRAP_ADMIN_PASSWORD", "configured")
    parent = _make_parent()
    parent.platform = "slack"
    parent._user_id = "u-2"
    parent._user_name = "Bob"
    store = AegisDelegateStore(tmp_path / "aegis.db")

    def run_delegate(**_kwargs):
        audits = store.query_audits().logs
        assert len(audits) == 1
        assert audits[0]["status"] == "succ"
        return {"success": False, "type": "a2a", "error": "remote failed"}

    monkeypatch.setattr(
        a2a_delegate_tool,
        "_run_remote_delegate",
        run_delegate,
    )

    payload = json.loads(
        a2a_delegate_tool.a2a_delegate(
            goal="Investigate",
            agent_name="responder",
            session_id="remote-2",
            parent_agent=parent,
        )
    )

    assert payload["success"] is False
    audit = store.query_audits().logs[0]
    assert audit["status"] == "succ"
    assert audit["platform"] == "slack"
    assert audit["session_id"] == "remote-2"


def test_aegis_successful_delegate_records_succ(monkeypatch, tmp_path):
    import tools.a2a_delegate_tool as a2a_delegate_tool
    from tools.a2a_delegate_aegis import AegisDelegateStore

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("AEGIS_BOOTSTRAP_ADMIN_PASSWORD", "configured")
    monkeypatch.setattr(
        a2a_delegate_tool,
        "_run_remote_delegate",
        MagicMock(return_value={"success": True, "type": "a2a", "final_response": "done"}),
    )

    payload = json.loads(
        a2a_delegate_tool.a2a_delegate(
            goal="Investigate",
            agent_name="responder",
            parent_agent=_make_parent(),
        )
    )

    assert payload["success"] is True
    audit = AegisDelegateStore(tmp_path / "aegis.db").query_audits().logs[0]
    assert audit["status"] == "succ"


def test_aegis_policy_check_error_fails_closed(monkeypatch, tmp_path):
    import tools.a2a_delegate_tool as a2a_delegate_tool

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("AEGIS_BOOTSTRAP_ADMIN_PASSWORD", "configured")
    monkeypatch.setattr(
        a2a_delegate_tool.a2a_delegate_aegis.AegisDelegateStore,
        "evaluate_policy",
        MagicMock(side_effect=OSError("database unavailable")),
    )
    run_delegate = MagicMock()
    monkeypatch.setattr(a2a_delegate_tool, "_run_remote_delegate", run_delegate)

    payload = json.loads(
        a2a_delegate_tool.a2a_delegate(
            goal="Investigate",
            agent_name="responder",
            parent_agent=_make_parent(),
        )
    )

    assert payload["success"] is False
    assert payload["authorization"] == "error"
    assert "database unavailable" not in payload["error"]
    run_delegate.assert_not_called()
    audit = a2a_delegate_tool.a2a_delegate_aegis.AegisDelegateStore(
        tmp_path / "aegis.db"
    ).query_audits().logs[0]
    assert audit["status"] == "fail"


def test_aegis_audit_failure_does_not_replace_delegate_result(monkeypatch, tmp_path):
    import tools.a2a_delegate_tool as a2a_delegate_tool

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("AEGIS_BOOTSTRAP_ADMIN_PASSWORD", "configured")
    monkeypatch.setattr(
        a2a_delegate_tool.a2a_delegate_aegis.AegisDelegateStore,
        "record_audit",
        MagicMock(side_effect=OSError("audit unavailable")),
    )
    monkeypatch.setattr(
        a2a_delegate_tool,
        "_run_remote_delegate",
        MagicMock(return_value={"success": True, "type": "a2a", "final_response": "done"}),
    )

    payload = json.loads(
        a2a_delegate_tool.a2a_delegate(
            goal="Investigate",
            agent_name="responder",
            parent_agent=_make_parent(),
        )
    )

    assert payload["success"] is True
    assert payload["final_response"] == "done"


def test_aegis_delegate_exception_does_not_change_authorization_audit(monkeypatch, tmp_path):
    import tools.a2a_delegate_tool as a2a_delegate_tool
    from tools.a2a_delegate_aegis import AegisDelegateStore

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("AEGIS_BOOTSTRAP_ADMIN_PASSWORD", "configured")
    monkeypatch.setattr(
        a2a_delegate_tool,
        "_run_remote_delegate",
        MagicMock(side_effect=RuntimeError("remote exploded")),
    )

    with pytest.raises(RuntimeError, match="remote exploded"):
        a2a_delegate_tool.a2a_delegate(
            goal="Investigate",
            agent_name="responder",
            parent_agent=_make_parent(),
        )

    audit = AegisDelegateStore(tmp_path / "aegis.db").query_audits().logs[0]
    assert audit["status"] == "succ"


def test_default_a2a_session_id_adds_two_digit_random_suffix(monkeypatch):
    import tools.a2a_delegate_tool as a2a_delegate_tool

    monkeypatch.setattr(a2a_delegate_tool, "get_active_profile_name", lambda: "main")
    monkeypatch.setattr(a2a_delegate_tool.time, "strftime", lambda fmt: "20260710_123456")

    session_id = a2a_delegate_tool._resolve_delegate_session_id(None)

    match = re.match(r"^delegate_main_a2a_20260710_123456_([0-9]{2})$", session_id)
    assert match is not None
    assert 1 <= int(match.group(1)) <= 99


def test_dispatch_helper_forwards_agent_runtime_bindings(monkeypatch):
    captured = {}

    def fake_delegate(**kwargs):
        captured.update(kwargs)
        return '{"ok": true}'

    monkeypatch.setattr("tools.a2a_delegate_tool.a2a_delegate", fake_delegate)
    agent = object.__new__(AIAgent)
    agent._delegate_ext_output_adapter = "output"
    agent._delegate_ext_input_factory = lambda: "input"

    result = agent._dispatch_a2a_delegate(
        {
            "goal": "inspect",
            "context": "ctx",
            "agent_name": "remote",
            "type": "local",
            "toolsets": ["terminal"],
            "max_iterations": 3,
            "session_id": "child-session",
            "is_delegate_output": False,
            "is_loop": True,
        }
    )

    assert json.loads(result) == {"ok": True}
    assert captured["parent_agent"] is agent
    assert captured["output"] == "output"
    assert captured["input"] == "input"
    assert "type" not in captured
    assert "toolsets" not in captured
    assert "max_iterations" not in captured
    assert captured["context"] == "ctx"
    assert captured["agent_name"] == "remote"
    assert captured["session_id"] == "child-session"
    assert captured["is_delegate_output"] is False
    assert captured["is_loop"] is True


def test_a2a_remote_without_sdk_returns_clear_error(monkeypatch):
    from tools.a2a_delegate_tool import A2A_REGISTRY, a2a_delegate

    A2A_REGISTRY.clear()
    A2A_REGISTRY["remote"] = {
        "name": "remote",
        "url": "http://agent.local/a2a",
        "available": True,
        "agent_card": {"supported_interfaces": [{"url": "http://agent.local/a2a"}]},
        "error": None,
    }
    monkeypatch.setattr("tools.a2a_delegate_tool._a2a_sdk_available", lambda: False)

    result = json.loads(
        a2a_delegate(goal="remote", type="a2a", agent_name="remote", parent_agent=_make_parent())
    )

    assert result["success"] is False
    assert "a2a sdk" in result["error"].lower()


def test_a2a_loop_requires_input_adapter(monkeypatch):
    from tools.a2a_delegate_tool import A2A_REGISTRY, a2a_delegate

    A2A_REGISTRY.clear()
    A2A_REGISTRY["remote"] = {
        "name": "remote",
        "url": "http://agent.local/a2a",
        "available": True,
        "agent_card": {"supported_interfaces": [{"url": "http://agent.local/a2a"}]},
        "error": None,
    }
    monkeypatch.setattr("tools.a2a_delegate_tool._a2a_sdk_available", lambda: True)

    result = json.loads(a2a_delegate(goal="remote", agent_name="remote", is_loop=True, parent_agent=_make_parent()))

    assert result["success"] is False
    assert "input adapter" in result["error"].lower()


@pytest.mark.parametrize("is_delegate_output", [True, False])
def test_remote_loop_reuses_session_and_routes_foreground_input(monkeypatch, is_delegate_output):
    import tools.a2a_delegate_tool as a2a_delegate_tool
    from tools.a2a_delegate_tool import A2A_REGISTRY, a2a_delegate

    A2A_REGISTRY.clear()
    A2A_REGISTRY["remote"] = {
        "name": "remote",
        "url": "http://agent.local/a2a",
        "available": True,
        "agent_card": {"supported_interfaces": [{"url": "http://agent.local/a2a"}]},
        "error": None,
    }
    monkeypatch.setattr("tools.a2a_delegate_tool._a2a_sdk_available", lambda: True)

    sessions = []

    class FakeSession:
        def __init__(self, base_url, *, output=None, session_id=None, headers=None, **kwargs):
            del kwargs
            self.base_url = base_url
            self.output = output
            self.context_id = session_id
            self.task_id = None
            self.headers = headers or {}
            self.turns = []
            sessions.append(self)

        async def send_turn(self, text, *, is_delegate_output=True):
            self.turns.append((text, is_delegate_output, self.context_id))
            self.context_id = "ctx-remote"
            self.task_id = f"task-{len(self.turns)}"
            if self.output and is_delegate_output:
                self.output.emit("delegate", "ai", f"response-{len(self.turns)}", session_id=self.context_id)
            return {
                "final_response": f"response-{len(self.turns)}",
                "state": "completed",
                "state_name": "completed",
                "context_id": self.context_id,
                "task_id": self.task_id,
            }

        async def close(self):
            self.closed = True

        def latest_assistant_text(self):
            return "latest"

        def should_suppress_stop_exception(self, exc):
            del exc
            return False

    monkeypatch.setattr(a2a_delegate_tool, "_A2ADelegateSession", FakeSession)
    parent = _make_parent()
    parent.platform = "slack"
    parent._user_id = "U123"
    parent._user_name = "Ada"
    parent._touch_activity = MagicMock()
    sink = _OutputSink()
    input_adapter = _Input(["follow up", "/main"])

    result = json.loads(
        a2a_delegate(
            goal="start",
            context="extra context",
            agent_name="remote",
            session_id="seed-session",
            is_loop=True,
            is_delegate_output=is_delegate_output,
            input=input_adapter,
            output=sink,
            parent_agent=parent,
        )
    )

    assert result["success"] is True
    assert result["session_id"] == "ctx-remote"
    assert result["loop_exit_reason"] == "main_command"
    assert input_adapter.entered is True
    assert input_adapter.exited is True
    assert parent._touch_activity.call_count == 1
    assert [turn[1] for turn in sessions[0].turns] == [True, True]
    assert sessions[0].turns[0][2] == "seed-session"
    assert sessions[0].turns[1][2] == "ctx-remote"
    assert "<source>" in sessions[0].turns[0][0]
    assert '"conn":"a2a"' in sessions[0].turns[0][0]
    assert "extra context" in sessions[0].turns[0][0]
    assert sessions[0].turns[1][0].endswith("follow up")
    assert sink.events == [
        ("delegate", "status", "entered foreground loop", "seed-session"),
        ("delegate", "ai", "response-1", "ctx-remote"),
        ("delegate", "ai", "response-2", "ctx-remote"),
        ("delegate", "status", "return to main", "ctx-remote"),
    ]


def test_remote_non_loop_disables_all_output_events(monkeypatch):
    import tools.a2a_delegate_tool as a2a_delegate_tool
    from tools.a2a_delegate_tool import A2A_REGISTRY, a2a_delegate

    A2A_REGISTRY.clear()
    A2A_REGISTRY["remote"] = {
        "name": "remote",
        "url": "http://agent.local/a2a",
        "available": True,
        "agent_card": {"supported_interfaces": [{"url": "http://agent.local/a2a"}]},
        "error": None,
    }
    monkeypatch.setattr("tools.a2a_delegate_tool._a2a_sdk_available", lambda: True)

    sessions = []

    class FakeSession:
        def __init__(self, base_url, *, output=None, session_id=None, headers=None, **kwargs):
            del base_url, headers, kwargs
            self.output = output
            self.context_id = session_id
            sessions.append(self)

        async def send_turn(self, text, *, is_delegate_output=True):
            del text
            assert is_delegate_output is False
            self.context_id = "ctx-remote"
            if self.output:
                for event_type, content in (
                    ("status", "working"),
                    ("ai_delta", "partial"),
                    ("tool_call", "terminal {}"),
                    ("ai", "done"),
                    ("error", "failed"),
                ):
                    self.output.emit("delegate", event_type, content, session_id=self.context_id)
            return {
                "final_response": "done",
                "state": "completed",
                "state_name": "completed",
                "context_id": self.context_id,
                "task_id": "task-1",
            }

        async def close(self):
            return None

    monkeypatch.setattr(a2a_delegate_tool, "_A2ADelegateSession", FakeSession)
    sink = _OutputSink()

    result = json.loads(
        a2a_delegate(
            goal="start",
            agent_name="remote",
            is_loop=False,
            is_delegate_output=False,
            output=sink,
            parent_agent=_make_parent(),
        )
    )

    assert result["success"] is True
    assert result["final_response"] == "done"
    assert sessions[0].output is None
    assert sink.events == []


def test_remote_loop_timeout_returns_clear_payload(monkeypatch):
    import tools.a2a_delegate_tool as a2a_delegate_tool
    from tools.a2a_delegate_tool import A2A_REGISTRY, _DELEGATE_INPUT_TIMEOUT, a2a_delegate

    A2A_REGISTRY.clear()
    A2A_REGISTRY["remote"] = {
        "name": "remote",
        "url": "http://agent.local/a2a",
        "available": True,
        "agent_card": {"supported_interfaces": [{"url": "http://agent.local/a2a"}]},
        "error": None,
    }
    monkeypatch.setattr("tools.a2a_delegate_tool._a2a_sdk_available", lambda: True)

    class FakeInput(_Input):
        def read_line(self, timeout=None):
            self.timeouts.append(timeout)
            return _DELEGATE_INPUT_TIMEOUT

    class FakeSession:
        def __init__(self, *args, session_id=None, **kwargs):
            del args, kwargs
            self.context_id = session_id
            self.task_id = "task-1"

        async def send_turn(self, text, *, is_delegate_output=True):
            del text, is_delegate_output
            self.context_id = "ctx-timeout"
            return {
                "final_response": "waiting",
                "state": "completed",
                "state_name": "completed",
                "context_id": self.context_id,
                "task_id": self.task_id,
            }

        async def close(self):
            return None

        def latest_assistant_text(self):
            return "waiting"

        def should_suppress_stop_exception(self, exc):
            del exc
            return False

    monkeypatch.setattr(a2a_delegate_tool, "_A2ADelegateSession", FakeSession)
    result = json.loads(
        a2a_delegate(
            goal="start",
            agent_name="remote",
            is_loop=True,
            input=FakeInput([]),
            parent_agent=_make_parent(),
        )
    )

    assert result["success"] is True
    assert result["loop_exit_reason"] == "input_timeout"
    assert "timeout" in result["final_response"].lower()
