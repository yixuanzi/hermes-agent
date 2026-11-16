"""Session ownership: an account attached after the fact scopes listings and counts to its owner."""

from __future__ import annotations

import pytest

from hermes_state import SessionDB


@pytest.fixture
def db(tmp_path):
    store = SessionDB(db_path=tmp_path / "state.db")
    for session_id, source in (("mine", "aisoc_web"), ("theirs", "aisoc_web"), ("legacy", "cli")):
        store.create_session(session_id, source)
        store.append_message(session_id, "user", content=f"hello from {session_id}")
    assert store.set_session_user_id("mine", "u-1") is True
    assert store.set_session_user_id("theirs", "u-2") is True
    yield store
    store.close()


def _ids(rows) -> set[str]:
    return {row["id"] for row in rows}


def test_set_session_user_id_reports_whether_a_row_exists(db) -> None:
    assert db.set_session_user_id("missing", "u-1") is False


def test_owner_listing_includes_ownerless_history_by_default(db) -> None:
    assert _ids(db.list_sessions_rich(user_id="u-1")) == {"mine", "legacy"}
    assert db.session_count(user_id="u-1") == 2


def test_private_owner_listing_shows_only_owned_rows(db) -> None:
    assert _ids(db.list_sessions_rich(user_id="u-1", include_ownerless=False)) == {"mine"}
    assert db.session_count(user_id="u-1", include_ownerless=False) == 1


def test_no_user_id_lists_everything(db) -> None:
    assert _ids(db.list_sessions_rich()) == {"mine", "theirs", "legacy"}
    assert db.session_count() == 3


def test_clearing_the_owner_makes_a_row_ownerless_again(db) -> None:
    db.set_session_user_id("theirs", None)

    assert _ids(db.list_sessions_rich(user_id="u-1")) == {"mine", "legacy", "theirs"}
    assert _ids(db.list_sessions_rich(user_id="u-2", include_ownerless=False)) == set()


def test_pinned_backfill_obeys_the_owner_filter(db) -> None:
    db.set_session_pinned("theirs", True)

    rows = db.list_sessions_rich(user_id="u-1", include_ownerless=False, include_pinned=True)

    assert _ids(rows) == {"mine"}
