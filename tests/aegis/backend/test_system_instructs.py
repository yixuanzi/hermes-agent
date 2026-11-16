from __future__ import annotations

import sqlite3

from aegis.backend.services.system_instruct_store import SystemInstructStore


def _create_non_admin_headers(client, admin_headers: dict[str, str]) -> dict[str, str]:
    created = client.post(
        "/api/users",
        headers=admin_headers,
        json={
            "username": "system-instruct-analyst",
            "password": "Password123!",
            "email": "system-instruct-analyst@example.com",
            "status": "enabled",
        },
    )
    assert created.status_code == 201
    login = client.post(
        "/api/auth/login",
        json={"username": "system-instruct-analyst", "password": "Password123!"},
    )
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_system_instructs_are_admin_only_and_persist_in_aegis_db(
    client,
    auth_headers: dict[str, str],
    hermes_home,
) -> None:
    assert client.get("/api/system-instructs").status_code == 401
    analyst_headers = _create_non_admin_headers(client, auth_headers)
    assert client.get("/api/system-instructs", headers=analyst_headers).status_code == 403

    created = client.post(
        "/api/system-instructs",
        headers=auth_headers,
        json={
            "name": "Incident response baseline",
            "describe": "Baseline guidance for incident response.",
            "instruct": "Prioritize containment and preserve evidence.",
            "status": "enabled",
        },
    )
    assert created.status_code == 201
    instruction = created.json()
    assert set(instruction) == {
        "id",
        "name",
        "describe",
        "instruct",
        "create_time",
        "update_time",
        "status",
    }
    assert instruction["create_time"] == instruction["update_time"]
    assert instruction["describe"] == "Baseline guidance for incident response."

    with sqlite3.connect(hermes_home / "aegis.db") as conn:
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(system_instructs)").fetchall()
        }
    assert columns == {"id", "name", "describe", "instruct", "create_time", "update_time", "status"}

    listed = client.get("/api/system-instructs", headers=auth_headers)
    assert listed.status_code == 200
    assert listed.json() == {"instructions": [instruction]}

    updated = client.put(
        f"/api/system-instructs/{instruction['id']}",
        headers=auth_headers,
        json={
            "name": "Incident response baseline",
            "describe": "Containment and evidence preservation order.",
            "instruct": "Contain first, then preserve evidence.",
            "status": "disabled",
        },
    )
    assert updated.status_code == 200
    assert updated.json()["id"] == instruction["id"]
    assert updated.json()["describe"] == "Containment and evidence preservation order."
    assert updated.json()["instruct"] == "Contain first, then preserve evidence."
    assert updated.json()["status"] == "disabled"

    deleted = client.delete(
        f"/api/system-instructs/{instruction['id']}",
        headers=auth_headers,
    )
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True, "id": instruction["id"]}
    assert client.get("/api/system-instructs", headers=auth_headers).json() == {
        "instructions": []
    }


def test_system_instructs_validate_input_and_missing_records(
    client,
    auth_headers: dict[str, str],
) -> None:
    invalid = client.post(
        "/api/system-instructs",
        headers=auth_headers,
        json={"name": "   ", "instruct": "   ", "status": "unknown"},
    )
    assert invalid.status_code == 422

    assert client.put(
        "/api/system-instructs/missing",
        headers=auth_headers,
        json={"name": "Valid", "instruct": "Valid instruction", "status": "enabled"},
    ).status_code == 404
    assert client.delete(
        "/api/system-instructs/missing",
        headers=auth_headers,
    ).status_code == 404


def test_system_instruct_store_migrates_legacy_records(tmp_path) -> None:
    database_path = tmp_path / "aegis.db"
    with sqlite3.connect(database_path) as conn:
        conn.execute(
            "CREATE TABLE system_instructs ("
            "id TEXT PRIMARY KEY, name TEXT NOT NULL, instruct TEXT NOT NULL, "
            "create_time TEXT NOT NULL, update_time TEXT NOT NULL, status TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO system_instructs VALUES (?, ?, ?, ?, ?, ?)",
            ("legacy", "Legacy instruction", "Preserve existing record.", "2026-07-30T10:00:00Z", "2026-07-30T10:00:00Z", "enabled"),
        )

    store = SystemInstructStore(database_path)

    assert store.get("legacy") == {
        "id": "legacy",
        "name": "Legacy instruction",
        "describe": "",
        "instruct": "Preserve existing record.",
        "create_time": "2026-07-30T10:00:00Z",
        "update_time": "2026-07-30T10:00:00Z",
        "status": "enabled",
    }
