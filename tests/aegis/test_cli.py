from __future__ import annotations

import sys

from fastapi.testclient import TestClient
import pytest

import hermes_cli.main as main_mod
from aegis.backend.config import load_aegis_settings
from aegis.backend.server import create_app


def test_hermes_aegis_routes_through_the_products_plugin(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def _fake_cmd_aegis(args) -> None:
        captured["args"] = args
        raise SystemExit(0)

    # `hermes aegis` is a plugin CLI command (plugins/aegis_products); its handler calls the backend.
    monkeypatch.setattr("aegis.backend.main.cmd_aegis", _fake_cmd_aegis)
    monkeypatch.setattr(sys, "argv", ["hermes", "aegis"])

    with pytest.raises(SystemExit) as exc:
        main_mod.main()

    assert exc.value.code == 0
    args = captured["args"]
    assert args is not None
    assert args.port == 9130
    assert args.host == "127.0.0.1"
    assert args.no_open is False
    assert args.insecure is False
    assert args.skip_build is False
    assert args.stop is False
    assert args.status is False


def test_root_serves_index_html(tmp_path) -> None:
    dist_dir = tmp_path / "web_dist"
    dist_dir.mkdir(parents=True)
    marker = "<div id='aegis-root'>aegis</div>"
    (dist_dir / "index.html").write_text(marker, encoding="utf-8")

    settings = load_aegis_settings(dist_dir=dist_dir)
    app = create_app(settings)
    client = TestClient(app)

    resp_root = client.get("/")
    assert resp_root.status_code == 200
    assert marker in resp_root.text

    resp_spa = client.get("/policy")
    assert resp_spa.status_code == 200
    assert marker in resp_spa.text
