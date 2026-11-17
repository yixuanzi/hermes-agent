"""``/model`` in Aegis chat parses its flags with the shared /model parser.

Passing the whole argument string as the model name made ``/model m --provider p`` switch the
live session to a model literally named ``m --provider p``; every later turn then failed.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.aegis.backend.test_chat_ws import AUTH_TOKEN, _recv_until, _SwitchableAgent


def _send(ws, session_id: str, text: str) -> str:
    ws.send_json({"type": "message.send", "session_id": session_id, "text": text})
    _recv_until(ws, "message.accepted")
    return _recv_until(ws, "message.completed")["content"]


def test_model_slash_passes_provider_flag_separately(load_backend, monkeypatch, hermes_home) -> None:
    agent = _SwitchableAgent("switchable")
    server = load_backend("aegis.backend.server")
    app = server.create_app()
    app.state.chat_manager.set_agent_factory(lambda session_id: agent)

    from hermes_cli import model_switch as model_switch_module

    seen: dict[str, str] = {}

    def _fake_switch_model(**kwargs):
        seen.update(raw_input=kwargs["raw_input"], explicit_provider=kwargs["explicit_provider"])
        return model_switch_module.ModelSwitchResult(
            success=True,
            new_model=kwargs["raw_input"],
            target_provider=kwargs["explicit_provider"],
            api_key="k",
            base_url="https://example.invalid/v1",
            api_mode="chat_completions",
            warning_message="",
        )

    monkeypatch.setattr(model_switch_module, "switch_model", _fake_switch_model)

    with TestClient(app) as client:
        with client.websocket_connect(f"/api/chat/ws?token={AUTH_TOKEN}") as ws:
            ws.send_json({"type": "session.bind", "title": "Model flags"})
            session_id = _recv_until(ws, "session.bound")["session_id"]

            reply = _send(ws, session_id, "/model glm-5.3-flash --provider custom:glm")
            refused = _send(ws, session_id, "/model glm-5.3 --global")

    assert seen == {"raw_input": "glm-5.3-flash", "explicit_provider": "custom:glm"}
    assert reply == "model -> glm-5.3-flash"
    assert agent.switch_calls[-1]["new_model"] == "glm-5.3-flash"
    assert "--global" in refused
    assert len(agent.switch_calls) == 1
