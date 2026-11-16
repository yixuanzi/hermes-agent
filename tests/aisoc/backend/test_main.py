from __future__ import annotations

import argparse
import importlib
import importlib.util
import os

import pytest


def _load_backend_main():
    spec = importlib.util.find_spec("aisoc.backend.main")
    assert spec is not None, "aisoc.backend.main should exist as the shared AISOC entrypoint"
    return importlib.import_module("aisoc.backend.main")


def _ns(**kwargs) -> argparse.Namespace:
    defaults = dict(
        port=9120,
        host="127.0.0.1",
        no_open=False,
        insecure=False,
        skip_build=False,
        module="server",
    )
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def test_backend_main_module_exists() -> None:
    _load_backend_main()


@pytest.mark.parametrize("module", ["a2a", "extcli"])
def test_build_parser_rejects_removed_modules(module: str) -> None:
    backend_main = _load_backend_main()
    parser = backend_main.build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--module", module])
    assert exc.value.code == 2


@pytest.mark.parametrize(
    "flag",
    [
        "--stop",
        "--status",
        "--db",
        "--db=/tmp/a2a.db",
        "--name",
        "--description",
        "--card",
        "--streaming",
        "--workers",
    ],
)
def test_build_parser_rejects_removed_flags(flag: str) -> None:
    backend_main = _load_backend_main()
    parser = backend_main.build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args([flag])
    assert exc.value.code == 2


def test_main_applies_profile_override_before_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend_main = _load_backend_main()
    called: dict[str, object] = {}

    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.setattr(
        "hermes_cli.profiles.resolve_profile_env",
        lambda profile: f"/tmp/hermes-profile-{profile}",
    )

    def _fake_cmd_aisoc(args: argparse.Namespace) -> None:
        called["module"] = args.module
        called["hermes_home"] = os.environ.get("HERMES_HOME")

    monkeypatch.setattr(backend_main, "cmd_aisoc", _fake_cmd_aisoc)

    exit_code = backend_main.main(["-p", "coder"])

    assert exit_code == 0
    assert called == {
        "module": "server",
        "hermes_home": "/tmp/hermes-profile-coder",
    }


def test_main_supports_equals_profile_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    backend_main = _load_backend_main()
    called: dict[str, object] = {}

    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.setattr(
        "hermes_cli.profiles.resolve_profile_env",
        lambda profile: f"/tmp/hermes-profile-{profile}",
    )

    def _fake_cmd_aisoc(args: argparse.Namespace) -> None:
        called["port"] = args.port
        called["hermes_home"] = os.environ.get("HERMES_HOME")

    monkeypatch.setattr(backend_main, "cmd_aisoc", _fake_cmd_aisoc)

    exit_code = backend_main.main(["--profile=writer", "--port", "9133"])

    assert exit_code == 0
    assert called == {
        "port": 9133,
        "hermes_home": "/tmp/hermes-profile-writer",
    }
