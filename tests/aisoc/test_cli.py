from __future__ import annotations

import argparse
import sys

from fastapi.testclient import TestClient
import pytest

import hermes_cli.main as main_mod
from aisoc.backend.config import load_aisoc_settings
from aisoc.backend.server import create_app


def test_aisoc_registered_in_builtin_subcommands() -> None:
    assert "aisoc" in main_mod._BUILTIN_SUBCOMMANDS


def test_main_routes_aisoc_with_expected_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def _fake_cmd_aisoc(args) -> None:
        captured["args"] = args
        raise SystemExit(0)

    monkeypatch.setattr(main_mod, "cmd_aisoc", _fake_cmd_aisoc)
    monkeypatch.setattr(sys, "argv", ["hermes", "aisoc"])

    with pytest.raises(SystemExit) as exc:
        main_mod.main()

    assert exc.value.code == 0
    args = captured["args"]
    assert args is not None
    assert args.port == 9120
    assert args.host == "127.0.0.1"
    assert args.tui is False
    assert args.no_open is False
    assert args.insecure is False
    assert args.skip_build is False


def test_root_serves_index_html(tmp_path) -> None:
    dist_dir = tmp_path / "web_dist"
    dist_dir.mkdir(parents=True)
    marker = "<div id='aisoc-root'>aisoc</div>"
    (dist_dir / "index.html").write_text(marker, encoding="utf-8")

    settings = load_aisoc_settings(dist_dir=dist_dir)
    app = create_app(settings)
    client = TestClient(app)

    resp_root = client.get("/")
    assert resp_root.status_code == 200
    assert marker in resp_root.text

    resp_spa = client.get("/chat")
    assert resp_spa.status_code == 200
    assert marker in resp_spa.text


def test_hermes_cmd_aisoc_delegates_to_backend_main(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def _fake_cmd_aisoc(args: argparse.Namespace) -> None:
        captured["args"] = args

    monkeypatch.setattr("aisoc.backend.main.cmd_aisoc", _fake_cmd_aisoc, raising=False)

    args = argparse.Namespace(module="server", port=9120)
    main_mod.cmd_aisoc(args)

    assert captured["args"] is args
