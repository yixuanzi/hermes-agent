from __future__ import annotations

from fastapi.testclient import TestClient


def test_admin_can_manage_users(client: TestClient, auth_headers: dict[str, str]) -> None:
    create_response = client.post(
        "/api/users",
        headers=auth_headers,
        json={
            "username": "alice",
            "password": "Password123!",
            "email": "alice@example.com",
            "status": "enabled",
        },
    )
    assert create_response.status_code == 201
    created_user = create_response.json()
    assert created_user["username"] == "alice"
    assert created_user["email"] == "alice@example.com"
    assert created_user["status"] == "enabled"
    assert created_user["role"] == "user"
    assert created_user["is_admin"] is False
    assert len(created_user["uid"]) == 16

    list_response = client.get("/api/users", headers=auth_headers)
    assert list_response.status_code == 200
    usernames = [item["username"] for item in list_response.json()["users"]]
    assert usernames == ["admin", "alice"]

    uid = created_user["uid"]
    disable_response = client.put(
        f"/api/users/{uid}/status",
        headers=auth_headers,
        json={"status": "disabled"},
    )
    assert disable_response.status_code == 200
    assert disable_response.json()["status"] == "disabled"

    password_response = client.put(
        f"/api/users/{uid}/password",
        headers=auth_headers,
        json={"password": "NewPassword123!"},
    )
    assert password_response.status_code == 200
    assert password_response.json() == {"updated": True, "uid": uid}

    delete_response = client.delete(f"/api/users/{uid}", headers=auth_headers)
    assert delete_response.status_code == 200
    assert delete_response.json() == {"deleted": True, "uid": uid}


def test_non_admin_cannot_access_user_management(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    create_response = client.post(
        "/api/users",
        headers=auth_headers,
        json={
            "username": "bob",
            "password": "Password123!",
            "email": "bob@example.com",
            "status": "enabled",
        },
    )
    assert create_response.status_code == 201

    login_response = client.post(
        "/api/auth/login",
        json={"username": "bob", "password": "Password123!"},
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]
    user_headers = {"Authorization": f"Bearer {token}"}

    list_response = client.get("/api/users", headers=user_headers)
    assert list_response.status_code == 403
    assert list_response.json() == {"detail": "Admin access required."}

    role_response = client.put(
        f"/api/users/{create_response.json()['uid']}/role",
        headers=user_headers,
        json={"role": "operator"},
    )
    assert role_response.status_code == 403
    assert role_response.json() == {"detail": "Admin access required."}


def test_admin_can_update_user_role_and_role_admin_grants_management_access(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    create_response = client.post(
        "/api/users",
        headers=auth_headers,
        json={
            "username": "role-user",
            "password": "Password123!",
            "email": "role-user@example.com",
            "status": "enabled",
        },
    )
    assert create_response.status_code == 201
    uid = create_response.json()["uid"]

    operator_response = client.put(
        f"/api/users/{uid}/role",
        headers=auth_headers,
        json={"role": "operator"},
    )
    assert operator_response.status_code == 200
    assert operator_response.json()["role"] == "operator"
    assert operator_response.json()["is_admin"] is False

    admin_response = client.put(
        f"/api/users/{uid}/role",
        headers=auth_headers,
        json={"role": "admin"},
    )
    assert admin_response.status_code == 200
    assert admin_response.json()["role"] == "admin"
    assert admin_response.json()["is_admin"] is True

    login_response = client.post(
        "/api/auth/login",
        json={"username": "role-user", "password": "Password123!"},
    )
    assert login_response.status_code == 200
    role_headers = {"Authorization": f"Bearer {login_response.json()['access_token']}"}
    assert client.get("/api/users", headers=role_headers).status_code == 200


def test_user_role_validation_and_bootstrap_admin_protection(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    users_response = client.get("/api/users", headers=auth_headers)
    admin_user = next(user for user in users_response.json()["users"] if user["username"] == "admin")

    invalid_response = client.put(
        f"/api/users/{admin_user['uid']}/role",
        headers=auth_headers,
        json={"role": "owner"},
    )
    assert invalid_response.status_code == 422

    demote_response = client.put(
        f"/api/users/{admin_user['uid']}/role",
        headers=auth_headers,
        json={"role": "user"},
    )
    assert demote_response.status_code == 400
    assert demote_response.json() == {"detail": "The bootstrap admin role cannot be changed."}


def test_admin_account_cannot_be_disabled_or_deleted(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    users_response = client.get("/api/users", headers=auth_headers)
    assert users_response.status_code == 200
    admin_user = next(user for user in users_response.json()["users"] if user["username"] == "admin")
    assert admin_user["uid"] == "0000000000000001"
    assert len(admin_user["uid"]) == 16
    assert admin_user["role"] == "admin"

    disable_response = client.put(
        f"/api/users/{admin_user['uid']}/status",
        headers=auth_headers,
        json={"status": "disabled"},
    )
    assert disable_response.status_code == 400
    assert disable_response.json() == {"detail": "The admin user cannot be disabled."}

    delete_response = client.delete(f"/api/users/{admin_user['uid']}", headers=auth_headers)
    assert delete_response.status_code == 400
    assert delete_response.json() == {"detail": "The admin user cannot be deleted."}
