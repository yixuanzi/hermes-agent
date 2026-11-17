"""``lines`` must be a positive count: a tail slice of 0 or -N would return
the whole log or all but its last N lines."""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture
def agent_log() -> Path:
    log = Path(os.environ["HERMES_HOME"]) / "logs" / "agent.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("".join(f"line {i}\n" for i in range(1, 6)), encoding="utf-8")
    return log


@pytest.mark.parametrize("lines", [0, -2])
def test_non_positive_line_count_is_rejected(test_client, auth_headers, agent_log, lines):
    resp = test_client.get(f"/api/logs?file=agent&lines={lines}", headers=auth_headers)

    assert resp.status_code == 422


def test_positive_line_count_returns_the_tail(test_client, auth_headers, agent_log):
    resp = test_client.get("/api/logs?file=agent&lines=2", headers=auth_headers)

    assert resp.status_code == 200
    assert [line.strip() for line in resp.json()["lines"]] == ["line 4", "line 5"]
