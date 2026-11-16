from __future__ import annotations

import json
import threading
import time

import pytest


def test_get_aegis_store_creates_default_file_and_returns_shared_instance(
    load_backend,
    hermes_home,
) -> None:
    store_module = load_backend("aegis.backend.services.store")

    first = store_module.get_aegis_store()
    second = store_module.get_aegis_store()

    assert first is second
    assert first.read_locked() == {"a2a": {}, "global": []}
    assert json.loads((hermes_home / "a2a.json").read_text()) == {
        "a2a": {},
        "global": [],
    }


@pytest.mark.parametrize(
    ("initial_payload", "expected_payload"),
    [
        ({"a2a": {}}, {"a2a": {}, "global": []}),
        ({"global": []}, {"a2a": {}, "global": []}),
    ],
)
def test_get_aegis_store_normalizes_missing_top_level_keys(
    load_backend,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    initial_payload: dict[str, object],
    expected_payload: dict[str, object],
) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "a2a.json").write_text(json.dumps(initial_payload))

    store_module = load_backend("aegis.backend.services.store")
    store = store_module.get_aegis_store()

    assert store.read_locked() == expected_payload
    assert json.loads((tmp_path / "a2a.json").read_text()) == expected_payload


def test_get_aegis_store_raises_for_malformed_json(
    load_backend,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "a2a.json").write_text("{not-json")

    with pytest.raises(json.JSONDecodeError):
        load_backend("aegis.backend.services.store").get_aegis_store()


@pytest.mark.parametrize(
    "invalid_payload",
    [
        {"a2a": [], "global": []},
        {"a2a": {}, "global": {}},
        [],
    ],
)
def test_get_aegis_store_rejects_invalid_top_level_shapes(
    load_backend,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    invalid_payload: object,
) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "a2a.json").write_text(json.dumps(invalid_payload))

    with pytest.raises(ValueError):
        load_backend("aegis.backend.services.store").get_aegis_store()


def test_mutate_locked_updates_shared_object_and_persists_to_disk(
    load_backend,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    store_module = load_backend("aegis.backend.services.store")
    store = store_module.get_aegis_store()

    seen: dict[str, object] = {}

    def _mutate(payload: dict[str, object]) -> None:
        seen["payload"] = payload
        payload["global"].append({"id": "policy-1"})
        payload["a2a"]["agent-1"] = {"enabled": True}

    store.mutate_locked(_mutate)

    expected = {
        "a2a": {"agent-1": {"enabled": True}},
        "global": [{"id": "policy-1"}],
    }
    assert seen["payload"] is not store.read_locked()
    assert store.read_locked() == expected
    assert json.loads((tmp_path / "a2a.json").read_text()) == expected


def test_mutate_locked_does_not_expose_live_store_reference(
    load_backend,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    store_module = load_backend("aegis.backend.services.store")
    store = store_module.get_aegis_store()
    escaped: dict[str, object] = {}

    def _mutate(payload: dict[str, object]) -> None:
        escaped["payload"] = payload
        payload["global"].append({"id": "policy-1"})

    store.mutate_locked(_mutate)
    cast_payload = escaped["payload"]
    assert isinstance(cast_payload, dict)

    cast_payload["a2a"]["agent-1"] = {"enabled": True}
    cast_payload["global"].append({"id": "policy-2"})

    assert store.read_locked() == {"a2a": {}, "global": [{"id": "policy-1"}]}
    assert json.loads((tmp_path / "a2a.json").read_text()) == {
        "a2a": {},
        "global": [{"id": "policy-1"}],
    }


def test_read_locked_returns_copy_that_cannot_mutate_store_state(
    load_backend,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    store_module = load_backend("aegis.backend.services.store")
    store = store_module.get_aegis_store()
    snapshot = store.read_locked()

    snapshot["global"].append({"id": "policy-1"})
    snapshot["a2a"]["agent-1"] = {"enabled": True}

    assert store.read_locked() == {"a2a": {}, "global": []}
    assert json.loads((tmp_path / "a2a.json").read_text()) == {
        "a2a": {},
        "global": [],
    }


def test_read_locked_reloads_external_file_changes(
    load_backend,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    store_module = load_backend("aegis.backend.services.store")
    store = store_module.get_aegis_store()

    assert store.read_locked() == {"a2a": {}, "global": []}

    external_payload = {
        "a2a": {"agent-1": {"url": "http://127.0.0.1:9086/a2a"}},
        "global": [{"id": "policy-1"}],
    }
    (tmp_path / "a2a.json").write_text(json.dumps(external_payload), encoding="utf-8")

    assert store.read_locked() == external_payload


def test_mutate_locked_reconciles_external_file_changes_before_writing(
    load_backend,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    store_module = load_backend("aegis.backend.services.store")
    store = store_module.get_aegis_store()

    (tmp_path / "a2a.json").write_text(
        json.dumps(
            {
                "a2a": {
                    "agent-1": {"url": "http://127.0.0.1:9086/a2a"},
                },
                "global": [{"id": "policy-1"}],
            }
        ),
        encoding="utf-8",
    )

    store.mutate_locked(
        lambda payload: payload["a2a"].__setitem__(
            "agent-2", {"url": "http://127.0.0.1:9087/a2a"}
        )
    )

    assert store.read_locked() == {
        "a2a": {
            "agent-1": {"url": "http://127.0.0.1:9086/a2a"},
            "agent-2": {"url": "http://127.0.0.1:9087/a2a"},
        },
        "global": [{"id": "policy-1"}],
    }


def test_mutate_locked_rolls_back_after_write_failure(
    load_backend,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    store_module = load_backend("aegis.backend.services.store")
    store = store_module.get_aegis_store()
    original_disk = json.loads((tmp_path / "a2a.json").read_text())

    def _fail_write(payload: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(store, "_write", _fail_write)

    with pytest.raises(OSError, match="disk full"):
        store.mutate_locked(
            lambda payload: (
                payload["global"].append({"id": "policy-1"}),
                payload["a2a"].__setitem__("agent-1", {"enabled": True}),
            )
        )

    assert store.read_locked() == {"a2a": {}, "global": []}
    assert json.loads((tmp_path / "a2a.json").read_text()) == original_disk


def test_mutate_locked_does_not_persist_failed_mutations(
    load_backend,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    store_module = load_backend("aegis.backend.services.store")
    store = store_module.get_aegis_store()

    with pytest.raises(RuntimeError, match="boom"):
        store.mutate_locked(
            lambda payload: (
                payload["global"].append({"id": "policy-1"}),
                (_ for _ in ()).throw(RuntimeError("boom")),
            )
        )

    assert store.read_locked() == {"a2a": {}, "global": []}
    assert json.loads((tmp_path / "a2a.json").read_text()) == {
        "a2a": {},
        "global": [],
    }


def test_mutate_locked_invalidates_a2a_caches_after_successful_write(
    load_backend,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    store_module = load_backend("aegis.backend.services.store")
    a2a_delegate_tool = load_backend("tools.a2a_delegate_tool")
    store = store_module.get_aegis_store()

    a2a_delegate_tool.A2A_REGISTRY["cached-agent"] = {
        "name": "cached-agent",
        "url": "http://cached.local/a2a",
        "available": True,
    }
    a2a_delegate_tool.A2A_CONTEXT = "<aegis_context>stale</aegis_context>"

    store.mutate_locked(
        lambda payload: payload["a2a"].__setitem__(
            "agent-1",
            {"url": "http://127.0.0.1:9086/a2a"},
        )
    )

    assert a2a_delegate_tool.A2A_REGISTRY == {}
    assert a2a_delegate_tool.A2A_CONTEXT == ""


def test_mutate_locked_keeps_a2a_caches_when_write_fails(
    load_backend,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    store_module = load_backend("aegis.backend.services.store")
    a2a_delegate_tool = load_backend("tools.a2a_delegate_tool")
    store = store_module.get_aegis_store()

    a2a_delegate_tool.A2A_REGISTRY["cached-agent"] = {
        "name": "cached-agent",
        "url": "http://cached.local/a2a",
        "available": True,
    }
    a2a_delegate_tool.A2A_CONTEXT = "<aegis_context>stale</aegis_context>"

    def _fail_write(payload: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(store, "_write", _fail_write)

    with pytest.raises(OSError, match="disk full"):
        store.mutate_locked(
            lambda payload: payload["a2a"].__setitem__(
                "agent-1",
                {"url": "http://127.0.0.1:9086/a2a"},
            )
        )

    assert a2a_delegate_tool.A2A_REGISTRY == {
        "cached-agent": {
            "name": "cached-agent",
            "url": "http://cached.local/a2a",
            "available": True,
        }
    }
    assert a2a_delegate_tool.A2A_CONTEXT == "<aegis_context>stale</aegis_context>"


def test_get_aegis_store_initializes_singleton_once_under_parallel_access(
    load_backend,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    store_module = load_backend("aegis.backend.services.store")
    created: list[int] = []
    original_store_cls = store_module.AegisStore

    class CountingStore(original_store_cls):
        def __init__(self, *args, **kwargs) -> None:
            time.sleep(0.02)
            super().__init__(*args, **kwargs)
            created.append(id(self))

    monkeypatch.setattr(store_module, "AegisStore", CountingStore)

    barrier = threading.Barrier(8)
    results: list[object] = []

    def _load_store() -> None:
        barrier.wait()
        results.append(store_module.get_aegis_store())

    threads = [threading.Thread(target=_load_store) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(created) == 1
    assert len({id(store) for store in results}) == 1
