from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import os
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from tests.aegis.backend.conftest import BOOTSTRAP_PASSWORD


def _load_backend_main():
    spec = importlib.util.find_spec("aegis.backend.main")
    assert spec is not None, "aegis.backend.main should exist"
    return importlib.import_module("aegis.backend.main")


def _ns(**kwargs) -> argparse.Namespace:
    defaults = dict(
        port=9130,
        host="127.0.0.1",
        no_open=False,
        insecure=False,
        skip_build=False,
        stop=False,
        status=False,
        profile=None,
    )
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def test_backend_main_module_exists() -> None:
    _load_backend_main()


def test_build_parser_defaults_to_standalone_port() -> None:
    backend_main = _load_backend_main()

    parser = backend_main.build_parser()
    args = parser.parse_args([])

    assert args.port == 9130
    assert args.host == "127.0.0.1"
    assert args.skip_build is False


def test_cmd_aegis_dispatches_server_start(monkeypatch: pytest.MonkeyPatch) -> None:
    backend_main = _load_backend_main()
    called: dict[str, object] = {}

    def _fake_start_server(**kwargs) -> None:
        called.update(kwargs)

    monkeypatch.setattr(backend_main, "_ensure_server_dist_available", lambda skip_build: called.setdefault("skip_build", skip_build))
    monkeypatch.setattr("aegis.backend.server.start_server", _fake_start_server)

    backend_main.cmd_aegis(_ns(port=9135, host="0.0.0.0", no_open=True, insecure=True))

    assert called == {
        "skip_build": False,
        "host": "0.0.0.0",
        "port": 9135,
        "open_browser": False,
        "allow_public": True,
    }


def test_main_invokes_cmd_aegis(monkeypatch: pytest.MonkeyPatch) -> None:
    backend_main = _load_backend_main()
    called: dict[str, object] = {}

    def _fake_cmd_aegis(args: argparse.Namespace) -> None:
        called["port"] = args.port
        called["host"] = args.host

    monkeypatch.setattr(backend_main, "cmd_aegis", _fake_cmd_aegis)

    exit_code = backend_main.main(["--port", "9137", "--host", "127.0.0.1"])

    assert exit_code == 0
    assert called == {"port": 9137, "host": "127.0.0.1"}


def test_start_server_formats_ipv6_browser_url(monkeypatch: pytest.MonkeyPatch) -> None:
    backend_server = importlib.import_module("aegis.backend.server")
    opened: dict[str, object] = {}

    monkeypatch.setattr(
        backend_server,
        "create_app",
        lambda settings: SimpleNamespace(
            state=SimpleNamespace(admin_setup_required=False)
        ),
    )
    monkeypatch.setattr(
        backend_server,
        "load_aegis_settings",
        lambda **kwargs: backend_server.AegisSettings(**kwargs, jwt_secret="token"),
    )
    monkeypatch.setattr(
        backend_server.webbrowser,
        "open",
        lambda url: opened.setdefault("url", url),
    )
    monkeypatch.setattr(
        backend_server.uvicorn,
        "run",
        lambda app, host, port, log_level, proxy_headers: opened.update(
            {"host": host, "port": port}
        ),
    )

    backend_server.start_server(host="::1", port=9130, open_browser=True)

    assert opened["url"] == "http://[::1]:9130/login"


def test_start_server_uses_the_launching_agents_hermes_home(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    backend_server = importlib.import_module("aegis.backend.server")
    agent_home = tmp_path / "agent-home"
    agent_home.mkdir()
    (agent_home / "home").mkdir()
    launcher_dir = tmp_path / "launcher"
    launcher_dir.mkdir()
    monkeypatch.chdir(launcher_dir)
    monkeypatch.setenv("HERMES_HOME", str(agent_home))
    monkeypatch.setattr(
        backend_server,
        "create_app",
        lambda settings: SimpleNamespace(
            state=SimpleNamespace(admin_setup_required=False)
        ),
    )
    monkeypatch.setattr(
        backend_server,
        "load_aegis_settings",
        lambda **kwargs: backend_server.AegisSettings(**kwargs, jwt_secret="token"),
    )
    monkeypatch.setattr(backend_server.uvicorn, "run", lambda *_args, **_kwargs: None)

    backend_server.start_server(open_browser=False)

    assert os.environ["HERMES_HOME"] == str(agent_home)
    assert os.environ["HOME"] == str(agent_home / "home")
    assert os.getcwd() == str(agent_home)


def test_root_serves_index_html(tmp_path) -> None:
    backend_server = importlib.import_module("aegis.backend.server")
    config = importlib.import_module("aegis.backend.config")

    dist_dir = tmp_path / "web_dist"
    dist_dir.mkdir(parents=True)
    marker = "<div id='aegis-root'>aegis</div>"
    (dist_dir / "index.html").write_text(marker, encoding="utf-8")

    app = backend_server.create_app(config.load_aegis_settings(dist_dir=dist_dir))
    client = TestClient(app)

    resp_root = client.get("/")
    assert resp_root.status_code == 200
    assert marker in resp_root.text

    resp_spa = client.get("/login")
    assert resp_spa.status_code == 200
    assert marker in resp_spa.text


def test_create_app_keeps_public_endpoints_available_when_store_is_invalid(
    load_backend,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("AEGIS_JWT_SECRET", "test-jwt-secret-1234567890-abcdef")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "a2a.json").write_text(json.dumps({"a2a": [], "global": []}))

    backend_server = load_backend("aegis.backend.server")
    app = backend_server.create_app()

    with TestClient(app) as client:
        health_response = client.get("/health")
        assert health_response.status_code == 200
        assert health_response.json() == {"status": "ok", "pid": os.getpid()}

        session_response = client.get("/api/auth/session")
        assert session_response.status_code == 200
        assert session_response.json() == {
            "authenticated": False,
        }

    with TestClient(app, raise_server_exceptions=False) as client:
        login_response = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": BOOTSTRAP_PASSWORD},
        )
        token = login_response.json()["access_token"]
        protected_response = client.get(
            "/api/agents",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert protected_response.status_code == 500
