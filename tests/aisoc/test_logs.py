from __future__ import annotations


def test_logs_endpoint_filters_by_level_and_component(test_client, auth_headers, monkeypatch) -> None:
    from aisoc.backend.services import log_service

    monkeypatch.setattr(
        log_service,
        "read_logs",
        lambda **kwargs: {"file": kwargs.get("file"), "lines": ["ERROR test"]},
    )
    resp = test_client.get(
        "/api/logs?file=agent&level=ERROR&component=cron",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["lines"] == ["ERROR test"]
