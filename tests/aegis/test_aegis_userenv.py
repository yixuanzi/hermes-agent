"""Aegis user_env_admin store and /api/my/user-env routes against a temp store file."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import aegis.backend.routes.user_env_admin as user_env_routes
from aegis.backend.routes.user_env_admin import build_user_env_router
from aegis.backend.services.user_env_admin_store import (
    UserEnvAdminError,
    UserEnvAdminStore,
    make_aegis_user_key,
    mask_value,
)


@pytest.fixture
def store_path(tmp_path: Path) -> Path:
    return tmp_path / "users.env.json"


@pytest.fixture
def store(store_path: Path) -> UserEnvAdminStore:
    return UserEnvAdminStore(store_path)


@pytest.mark.parametrize(
    ("value", "masked"),
    [
        ("sk-abcdef1234567890xyz", "sk-a**************0xyz"),
        ("abc", "***"),
        ("12345678", "********"),
        ("123456789", "1234*6789"),
    ],
)
def test_mask_value_keeps_four_chars_each_side_only_for_long_values(value: str, masked: str) -> None:
    assert mask_value(value) == masked


def test_user_key_is_namespaced_under_aegis() -> None:
    assert make_aegis_user_key("u123") == "aegis.u123"


def test_set_returns_masked_values_and_stores_plaintext_with_user_name(
    store: UserEnvAdminStore, store_path: Path
) -> None:
    assert store.list_for_user("u1") == {}

    env = store.set_var("u1", "alice", "MY_KEY", "sk-secret-value-12345")

    assert env["MY_KEY"] == "sk-s*************2345"
    listed = store.list_for_user("u1")
    assert listed["MY_KEY"] == "sk-s*************2345"
    assert "CURRENT_USER_NAME" not in listed
    raw = json.loads(store_path.read_text(encoding="utf-8"))
    assert raw["aegis.u1"]["MY_KEY"] == "sk-secret-value-12345"
    assert raw["aegis.u1"]["CURRENT_USER_NAME"] == "alice"


def test_reserved_user_name_key_cannot_be_set_or_deleted(store: UserEnvAdminStore) -> None:
    with pytest.raises(UserEnvAdminError):
        store.set_var("u1", "alice", "CURRENT_USER_NAME", "evil")
    with pytest.raises(UserEnvAdminError):
        store.delete_var("u1", "CURRENT_USER_NAME")


@pytest.mark.parametrize("bad_key", ["", "A=B", "A\x00B"])
def test_invalid_keys_are_rejected(store: UserEnvAdminStore, bad_key: str) -> None:
    with pytest.raises(UserEnvAdminError):
        store.set_var("u1", "alice", bad_key, "v")


def test_set_overwrites_an_existing_key(store: UserEnvAdminStore) -> None:
    store.set_var("u1", "alice", "MY_KEY", "sk-secret-value-12345")
    store.set_var("u1", "alice", "MY_KEY", "new-value-987654")

    assert store.list_for_user("u1")["MY_KEY"] == "new-********7654"


def test_users_are_isolated_into_separate_partitions(store: UserEnvAdminStore, store_path: Path) -> None:
    store.set_var("u1", "alice", "MY_KEY", "sk-secret-value-12345")

    assert store.list_for_user("u2") == {}
    env2 = store.set_var("u2", "bob", "OTHER", "value2")
    assert "OTHER" in env2
    assert "MY_KEY" not in env2
    raw = json.loads(store_path.read_text(encoding="utf-8"))
    assert {"aegis.u1", "aegis.u2"} <= set(raw)


def test_delete_reports_whether_a_key_was_removed(store: UserEnvAdminStore) -> None:
    store.set_var("u1", "alice", "MY_KEY", "sk-secret-value-12345")

    assert store.delete_var("u1", "MY_KEY") is True
    assert store.delete_var("u1", "MY_KEY") is False
    assert store.list_for_user("u1") == {}


def test_corrupt_store_file_reads_as_empty(store: UserEnvAdminStore, store_path: Path) -> None:
    store_path.write_text("{broken json", encoding="utf-8")

    assert store.list_for_user("u1") == {}


class _FakeUser:
    def __init__(self, uid: str, username: str) -> None:
        self.uid, self.username, self.is_admin = uid, username, False


@pytest.fixture
def current_user(monkeypatch: pytest.MonkeyPatch) -> dict[str, _FakeUser]:
    holder = {"user": _FakeUser("u1", "alice")}
    monkeypatch.setattr(
        user_env_routes,
        "require_authenticated_user",
        lambda request, settings, user_service: (holder["user"], {}),
    )
    return holder


@pytest.fixture
def client(store: UserEnvAdminStore, current_user: dict[str, _FakeUser]) -> TestClient:
    app = FastAPI()
    app.include_router(build_user_env_router(object(), object(), store))
    return TestClient(app)


def test_routes_set_list_and_delete_the_callers_own_vars(client: TestClient) -> None:
    response = client.get("/api/my/user-env")
    assert response.status_code == 200
    assert response.json()["variables"] == []

    response = client.put("/api/my/user-env", json={"key": "API_KEY", "value": "sk-live-abcdef9999"})
    assert response.status_code == 200
    assert {"key": "API_KEY", "masked_value": "sk-l**********9999"}.items() <= next(
        v for v in response.json()["variables"] if v["key"] == "API_KEY"
    ).items()

    response = client.get("/api/my/user-env")
    assert response.status_code == 200
    assert len(response.json()["variables"]) == 1

    response = client.delete("/api/my/user-env/API_KEY")
    assert response.status_code == 200
    assert response.json()["deleted"] is True
    assert client.delete("/api/my/user-env/API_KEY").status_code == 404


def test_routes_reject_reserved_and_blank_keys(client: TestClient) -> None:
    assert client.put("/api/my/user-env", json={"key": "CURRENT_USER_NAME", "value": "x"}).status_code == 400
    assert client.put("/api/my/user-env", json={"key": "", "value": "x"}).status_code == 422


def test_routes_never_show_another_users_vars(
    client: TestClient, store: UserEnvAdminStore, current_user: dict[str, _FakeUser]
) -> None:
    store.set_var("u1", "alice", "PRIVATE", "secret-xyz-9876")
    current_user["user"] = _FakeUser("u2", "bob")

    response = client.get("/api/my/user-env")

    assert response.status_code == 200
    assert response.json()["variables"] == []
