from __future__ import annotations


def test_session_detail_includes_tool_call_id_for_history_reconstruction(monkeypatch) -> None:
    """The frontend groups consecutive role="tool" rows into a reconstructed
    tool-call bubble on refresh (chatRuntime.tsx messagesFromSessionDetail),
    keyed by tool_call_id. Regression guard: don't drop that field again."""
    from aisoc.backend.services import session_service

    class FakeSessionDB:
        def resolve_session_id(self, session_id: str) -> str:
            return session_id

        def get_session(self, sid: str) -> dict:
            return {
                "source": "aisoc_web",
                "model": "test-model",
                "started_at": 1,
                "ended_at": 2,
                "message_count": 3,
                "input_tokens": 10,
                "output_tokens": 20,
                "user_id": "admin-uid",
            }

        def get_messages(self, sid: str) -> list[dict]:
            return [
                {"role": "user", "content": "run the scan", "timestamp": 1},
                {
                    "role": "tool",
                    "content": "scan complete, 0 findings",
                    "tool_name": "run_shell",
                    "tool_call_id": "call_abc",
                    "timestamp": 2,
                },
                {"role": "assistant", "content": "Scan finished.", "timestamp": 3},
            ]

        def close(self) -> None:
            pass

    monkeypatch.setattr(session_service, "SessionDB", FakeSessionDB)

    payload = session_service.get_session_detail_with_messages("sess-1", user_id="admin-uid")

    assert payload is not None
    tool_messages = [m for m in payload["messages"] if m["role"] == "tool"]
    assert len(tool_messages) == 1
    assert tool_messages[0]["tool_call_id"] == "call_abc"
    assert tool_messages[0]["tool_name"] == "run_shell"
    assert tool_messages[0]["content"] == "scan complete, 0 findings"


def test_ownerless_session_is_readable_by_any_user(monkeypatch) -> None:
    """Sessions with no user_id (legacy rows, tui/discord/cron/etc.) must stay
    visible on the browse/search page regardless of who's asking — only
    owned sessions are exclusive. Regression guard for the per-user isolation
    change accidentally hiding all pre-existing/cross-platform history."""
    from aisoc.backend.services import session_service

    class FakeSessionDB:
        def resolve_session_id(self, session_id: str) -> str:
            return session_id

        def get_session(self, sid: str) -> dict:
            return {"source": "tui", "user_id": None}

        def get_messages(self, sid: str) -> list[dict]:
            return []

        def close(self) -> None:
            pass

    monkeypatch.setattr(session_service, "SessionDB", FakeSessionDB)

    assert session_service.get_session_detail("sess-tui", user_id="some-user") is not None
    assert session_service.get_session_messages("sess-tui", user_id="some-user") is not None


def test_owned_session_stays_exclusive_to_its_owner(monkeypatch) -> None:
    """A session with a real owner must still be denied to a different user —
    the ownerless-is-public relaxation must not reopen the isolation gap."""
    from aisoc.backend.services import session_service

    class FakeSessionDB:
        def resolve_session_id(self, session_id: str) -> str:
            return session_id

        def get_session(self, sid: str) -> dict:
            return {"source": "aisoc_web", "user_id": "owner-uid"}

        def close(self) -> None:
            pass

    monkeypatch.setattr(session_service, "SessionDB", FakeSessionDB)

    assert session_service.get_session_detail("sess-1", user_id="other-uid") is None
    assert session_service.get_session_detail("sess-1", user_id="owner-uid") is not None


def test_ownerless_session_cannot_be_deleted_by_anyone(monkeypatch) -> None:
    """Delete stays strict: nobody can delete an unclaimed cross-platform
    session just because they can now read it."""
    from aisoc.backend.services import session_service

    class FakeSessionDB:
        def resolve_session_id(self, session_id: str) -> str:
            return session_id

        def get_session(self, sid: str) -> dict:
            return {"source": "discord", "user_id": ""}

        def close(self) -> None:
            pass

    monkeypatch.setattr(session_service, "SessionDB", FakeSessionDB)

    assert session_service.delete_session("sess-discord", user_id="some-user") is False


def test_list_sessions_default_includes_ownerless(monkeypatch) -> None:
    """Browse/search page behavior: default listing still surfaces ownerless
    (legacy/cross-platform) sessions alongside the user's own."""
    from aisoc.backend.services import session_service

    calls = {}

    class FakeSessionDB:
        def list_sessions_rich(self, **kwargs) -> list[dict]:
            calls["list_kwargs"] = kwargs
            return []

        def session_count(self, **kwargs) -> int:
            calls["count_kwargs"] = kwargs
            return 0

        def close(self) -> None:
            pass

    monkeypatch.setattr(session_service, "SessionDB", FakeSessionDB)

    session_service.list_sessions(user_id="some-user")

    assert calls["list_kwargs"]["include_ownerless"] is True
    assert calls["count_kwargs"]["include_ownerless"] is True


def test_list_sessions_strict_owner_excludes_ownerless(monkeypatch) -> None:
    """The private per-user chat sidebar must ask for strict_owner=True so
    brand-new users don't inherit every unclaimed legacy/cross-platform
    session in the database."""
    from aisoc.backend.services import session_service

    calls = {}

    class FakeSessionDB:
        def list_sessions_rich(self, **kwargs) -> list[dict]:
            calls["list_kwargs"] = kwargs
            return []

        def session_count(self, **kwargs) -> int:
            calls["count_kwargs"] = kwargs
            return 0

        def close(self) -> None:
            pass

    monkeypatch.setattr(session_service, "SessionDB", FakeSessionDB)

    session_service.list_sessions(user_id="some-user", strict_owner=True)

    assert calls["list_kwargs"]["include_ownerless"] is False
    assert calls["count_kwargs"]["include_ownerless"] is False


def test_latest_descendant_returns_resume_target(test_client, auth_headers, monkeypatch) -> None:
    from aisoc.backend.services import session_service

    monkeypatch.setattr(
        session_service,
        "get_latest_descendant",
        lambda session_id, *, user_id: {
            "requested_session_id": session_id,
            "session_id": "sess-child",
            "path": [session_id, "sess-child"],
            "changed": True,
        },
    )
    resp = test_client.get(
        "/api/sessions/root/latest-descendant", headers=auth_headers
    )
    assert resp.status_code == 200
    assert resp.json()["session_id"] == "sess-child"
