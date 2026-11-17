"""``hermes workagent`` picks a per-module default port; an explicit ``--port`` wins."""

from __future__ import annotations

import pytest

from workagent.backend import a2a_server, server
from workagent.backend.main import build_parser, cmd_workagent


@pytest.fixture
def started(monkeypatch, tmp_path):
    calls: dict[str, int] = {}
    monkeypatch.setattr(a2a_server, "start_a2a_server", lambda **kw: calls.setdefault("a2a", kw["port"]))
    monkeypatch.setattr(server, "start_server", lambda **kw: calls.setdefault("server", kw["port"]))
    (tmp_path / "index.html").write_text("<html></html>")
    monkeypatch.setenv("WORKAGENT_WEB_DIST", str(tmp_path))
    return calls


@pytest.mark.parametrize(
    ("argv", "module", "port"),
    [
        (["--module", "a2a"], "a2a", 9086),
        (["--module", "server", "--skip-build"], "server", 9120),
        (["--skip-build"], "server", 9120),
        (["--module", "a2a", "--port", "9200"], "a2a", 9200),
        (["--module", "server", "--skip-build", "--port", "9300"], "server", 9300),
    ],
)
def test_module_default_port(started, argv, module, port):
    cmd_workagent(build_parser().parse_args(argv))
    assert started == {module: port}
