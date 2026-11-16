from __future__ import annotations


def test_toggle_skill_persists_enabled_flag(test_client, auth_headers, monkeypatch) -> None:
    from aisoc.backend.services import skill_service

    monkeypatch.setattr(
        skill_service,
        "toggle_skill",
        lambda name, enabled: {"ok": True, "name": name, "enabled": enabled},
    )
    resp = test_client.put(
        "/api/skills/toggle",
        headers=auth_headers,
        json={"name": "demo", "enabled": False},
    )
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False
