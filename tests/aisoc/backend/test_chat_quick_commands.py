from __future__ import annotations

import json
from pathlib import Path

from aisoc.backend.services.quick_command_service import QuickCommandService


def _write_user_commands(hermes_home: Path, commands: list[dict]) -> Path:
    path = hermes_home / "aisoc_quick_commands.json"
    path.write_text(json.dumps({"commands": commands}, ensure_ascii=False), encoding="utf-8")
    return path


def test_chat_quick_commands_require_authentication_and_include_seeds(
    client,
    auth_headers: dict[str, str],
) -> None:
    assert client.get("/api/chat/quick-commands").status_code == 401

    response = client.get("/api/chat/quick-commands", headers=auth_headers)

    assert response.status_code == 200
    commands = response.json()["commands"]
    ontology = [c for c in commands if c["type"] == "instruct" and c["name"] == "ontology"]
    assert len(ontology) == 1
    assert "aisoc-ontology" in ontology[0]["content"]


def test_chat_quick_commands_merge_user_file_and_prefer_it_over_seeds(
    client,
    auth_headers: dict[str, str],
    hermes_home: Path,
) -> None:
    _write_user_commands(
        hermes_home,
        [
            {
                "type": "instruct",
                "name": "ontology",
                "desc": "Operator override.",
                "content": "Overridden ontology instructions.",
            },
            {
                "type": "prompt",
                "name": "Triage",
                "desc": "Assess incident severity.",
                "content": "Classify this alert.",
            },
        ],
    )

    response = client.get("/api/chat/quick-commands", headers=auth_headers)

    assert response.status_code == 200
    commands = {(c["type"], c["name"]): c for c in response.json()["commands"]}
    assert commands[("instruct", "ontology")]["content"] == "Overridden ontology instructions."
    assert commands[("prompt", "Triage")]["content"] == "Classify this alert."


def test_chat_quick_commands_reload_when_the_user_file_changes(
    client,
    auth_headers: dict[str, str],
    hermes_home: Path,
) -> None:
    path = _write_user_commands(
        hermes_home,
        [{"type": "prompt", "name": "First", "desc": "v1", "content": "one"}],
    )
    first = client.get("/api/chat/quick-commands", headers=auth_headers).json()["commands"]
    assert any(c["name"] == "First" for c in first)

    _write_user_commands(
        hermes_home,
        [{"type": "prompt", "name": "Second", "desc": "v2", "content": "two"}],
    )
    import os

    os.utime(path, (path.stat().st_atime + 5, path.stat().st_mtime + 5))

    second = client.get("/api/chat/quick-commands", headers=auth_headers).json()["commands"]
    assert any(c["name"] == "Second" for c in second)
    assert not any(c["name"] == "First" for c in second)


def test_resolve_text_expands_tokens_and_arguments(tmp_path: Path) -> None:
    commands_path = tmp_path / "aisoc_quick_commands.json"
    commands_path.write_text(
        json.dumps(
            {
                "commands": [
                    {
                        "type": "agent",
                        "name": "responder",
                        "desc": "",
                        "content": "Delegate to {name} in {region}.",
                    },
                    {
                        "type": "instruct",
                        "name": "evidence",
                        "desc": "",
                        "content": "Preserve evidence.",
                    },
                    {
                        "type": "prompt",
                        "name": "triage",
                        "desc": "",
                        "content": "Classify {indicator}.",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    service = QuickCommandService(user_commands_path=commands_path)

    resolved = service.resolve_text(
        "@[agent_responder] @[instruct_evidence]@[prompt_triage] Priority {priority}.",
        args={"region": "eu-west-1", "indicator": "203.0.113.7", "priority": 2},
    )

    assert resolved == (
        "Delegate to responder in eu-west-1. Preserve evidence.\n"
        "Classify 203.0.113.7. Priority 2."
    )


def test_resolve_text_keeps_unknown_tokens_and_variables(tmp_path: Path) -> None:
    service = QuickCommandService(user_commands_path=tmp_path / "missing.json")

    resolved = service.resolve_text("@[prompt_nope] keep {unset}.", args={})

    assert resolved == "@[prompt_nope] keep {unset}."


def test_resolve_text_ignores_malformed_user_file(tmp_path: Path) -> None:
    commands_path = tmp_path / "aisoc_quick_commands.json"
    commands_path.write_text("not-json", encoding="utf-8")
    service = QuickCommandService(user_commands_path=commands_path)

    commands = service.list_commands()

    assert any(c.type == "instruct" and c.name == "ontology" for c in commands)
