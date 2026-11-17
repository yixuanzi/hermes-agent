"""The embedded chat PTY resolves the TUI launch argv from Hermes' launcher module."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from workagent.backend.services import tui_embed


def test_resolve_chat_argv_uses_the_tui_launcher_and_pins_this_python(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import hermes_cli.main_tui_launch as launch

    calls: list[Path] = []

    def _fake_make_tui_argv(tui_dir: Path, tui_dev: bool, notices=None):
        calls.append(tui_dir)
        return ["node", "dist/entry.js"], tmp_path

    monkeypatch.setattr(launch, "_make_tui_argv", _fake_make_tui_argv)
    monkeypatch.setenv("HERMES_PYTHON", "/somewhere/else/python")

    argv, cwd, env = tui_embed.resolve_chat_argv(sidecar_url="ws://127.0.0.1:1/api/chat/ws")

    assert argv == ["node", "dist/entry.js"]
    assert cwd == str(tmp_path)
    assert calls and calls[0].name == "ui-tui"
    assert env["HERMES_PYTHON"] == sys.executable
    assert env["HERMES_TUI_SIDECAR_URL"] == "ws://127.0.0.1:1/api/chat/ws"
