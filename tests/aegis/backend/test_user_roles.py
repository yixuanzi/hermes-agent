from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient


def _role_payload(
    *,
    platform: str = "aegis",
    uid: str = "role-user-1",
    uname: str = "Role User",
    role: str = "user",
) -> dict[str, str]:
    return {"platform": platform, "uid": uid, "uname": uname, "role": role}


def test_user_role_table_has_expected_schema(hermes_home: Path) -> None:
    from aegis.backend.services.user_role_store import UserRoleStore

    UserRoleStore()
    with sqlite3.connect(hermes_home / "aegis.db") as connection:
        columns = connection.execute("PRAGMA table_info(user_roles)").fetchall()
        constraints = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'user_roles'"
        ).fetchone()

    assert [column[1] for column in columns] == [
        "id",
        "platform",
        "uid",
        "uname",
        "role",
        "update_time",
    ]
    assert constraints is not None
    assert "UNIQUE(platform, uid)" in constraints[0]
    assert "'operator'" in constraints[0]


def test_admin_can_create_update_list_and_delete_user_roles(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    first = client.post("/api/user-roles", headers=auth_headers, json=_role_payload())
    assert first.status_code == 201
    created = first.json()
    assert set(created) == {"id", "platform", "uid", "uname", "role", "update_time"}
    assert created["role"] == "user"
    assert len(created["id"]) == 36
    original_update_time = created["update_time"]

    second = client.post(
        "/api/user-roles",
        headers=auth_headers,
        json=_role_payload(uid="role-user-2", uname="Second User", role="operator"),
    )
    assert second.status_code == 201

    updated = client.put(
        f"/api/user-roles/{created['id']}",
        headers=auth_headers,
        json=_role_payload(uname="Updated User", role="admin"),
    )
    assert updated.status_code == 200
    assert updated.json()["uname"] == "Updated User"
    assert updated.json()["role"] == "admin"
    assert updated.json()["update_time"] != original_update_time

    listed = client.get("/api/user-roles", headers=auth_headers)
    assert listed.status_code == 200
    roles = listed.json()["roles"]
    assert roles[0]["id"] == created["id"]
    assert {role["uid"] for role in roles} == {"role-user-1", "role-user-2"}

    deleted = client.delete(
        f"/api/user-roles/{created['id']}",
        headers=auth_headers,
    )
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True, "id": created["id"]}


def test_user_role_validation_and_unique_platform_uid(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    invalid = client.post(
        "/api/user-roles",
        headers=auth_headers,
        json=_role_payload(role="owner"),
    )
    assert invalid.status_code == 422

    created = client.post("/api/user-roles", headers=auth_headers, json=_role_payload())
    assert created.status_code == 201
    duplicate = client.post(
        "/api/user-roles",
        headers=auth_headers,
        json=_role_payload(uname="Another Name", role="operator"),
    )
    assert duplicate.status_code == 409


def test_user_role_missing_records_return_not_found(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    missing_id = "missing-role-id"
    update = client.put(
        f"/api/user-roles/{missing_id}",
        headers=auth_headers,
        json=_role_payload(),
    )
    assert update.status_code == 404

    delete = client.delete(f"/api/user-roles/{missing_id}", headers=auth_headers)
    assert delete.status_code == 404


def test_non_admin_cannot_access_user_roles(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    create_user = client.post(
        "/api/users",
        headers=auth_headers,
        json={
            "username": "role-reader",
            "password": "Password123!",
            "email": "role-reader@example.com",
            "status": "enabled",
        },
    )
    assert create_user.status_code == 201
    login = client.post(
        "/api/auth/login",
        json={"username": "role-reader", "password": "Password123!"},
    )
    assert login.status_code == 200
    user_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    for method, path, kwargs in (
        ("get", "/api/user-roles", {}),
        ("post", "/api/user-roles", {"json": _role_payload()}),
        ("put", "/api/user-roles/missing-role-id", {"json": _role_payload()}),
        ("delete", "/api/user-roles/missing-role-id", {}),
    ):
        response = getattr(client, method)(path, headers=user_headers, **kwargs)
        assert response.status_code == 403
        assert response.json() == {"detail": "Admin access required."}
