from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
import sqlite3

import jwt
from fastapi.testclient import TestClient

from aegis.backend.chat.service import TaskAuditStore
from tools.a2a_delegate_aegis import AegisDelegateStore


AUTH_TOKEN = jwt.encode(
    {
        "sub": "0000000000000001",
        "username": "admin",
        "email": "admin@aegis.local",
        "iat": 1,
        "exp": 4102444800,
    },
    "test-jwt-secret-1234567890-abcdef",
    algorithm="HS256",
)
AUTH_HEADERS = {"Authorization": f"Bearer {AUTH_TOKEN}"}


def _record_logs(hermes_home) -> None:
    store = AegisDelegateStore(hermes_home / "aegis.db")
    common = {
        "platform": "aegis",
        "agent_name": "responder",
        "session_id": "remote-session",
        "is_loop": False,
        "is_delegate_output": True,
    }
    store.record_audit(
        **common,
        user_id="u-1",
        user_name="Alice",
        goal="Investigate phishing",
        status="succ",
        audit_id="audit-1",
        timestamp="2026-07-21T01:00:00.000000Z",
    )
    store.record_audit(
        **common,
        user_id="u-2",
        user_name="Bob",
        goal="Investigate malware",
        status="fail",
        audit_id="audit-2",
        timestamp="2026-07-21T02:00:00.000000Z",
    )
    store.record_audit(
        **{**common, "is_loop": True},
        user_id="u-1",
        user_name="Alice",
        goal="Investigate phishing attachment",
        status="auth_denied",
        audit_id="audit-3",
        timestamp="2026-07-21T03:00:00.000000Z",
    )


def _record_task_logs(hermes_home) -> None:
    store = TaskAuditStore(hermes_home / "aegis.db")
    store.record_task_audit(
        uid="u-1",
        uname="Alice",
        session_id="session-1",
        prompt="Investigate phishing",
        audit_id="task-1",
        create_time="2026-07-21T01:00:00.000000Z",
    )
    store.record_task_audit(
        uid="u-2",
        uname="Bob",
        session_id="session-2",
        prompt="Investigate malware",
        audit_id="task-2",
        create_time="2026-07-21T02:00:00.000000Z",
    )


def test_task_audit_store_pages_newest_first_and_uses_final_schema(hermes_home) -> None:
    _record_task_logs(hermes_home)
    store = TaskAuditStore(hermes_home / "aegis.db")

    first = store.query_task_audits(page=1, page_size=1)
    assert first.total == 2
    assert [row["id"] for row in first.logs] == ["task-2"]
    updated = store.query_task_audits(id="task-1")
    assert list(updated.logs[0]) == ["id", "uid", "uname", "session_id", "prompt", "create_time"]


def test_task_audit_store_migrates_legacy_columns_without_losing_rows(hermes_home) -> None:
    with sqlite3.connect(hermes_home / "aegis.db") as conn:
        conn.execute(
            "CREATE TABLE task_audit ("
            "id TEXT PRIMARY KEY, timestamp TEXT NOT NULL, uid TEXT NOT NULL, "
            "uname TEXT NOT NULL, session_id TEXT NOT NULL, prompt TEXT NOT NULL, "
            "create_time TEXT NOT NULL, last_time TEXT NOT NULL"
            ")"
        )
        conn.execute(
            "INSERT INTO task_audit "
            "(id, timestamp, uid, uname, session_id, prompt, create_time, last_time) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "legacy-task",
                "2026-07-21T01:00:00.000000Z",
                "u-legacy",
                "Legacy",
                "legacy-session",
                "Legacy prompt",
                "2026-07-21T01:00:00.000000Z",
                "2026-07-21T01:01:00.000000Z",
            ),
        )

    store = TaskAuditStore(hermes_home / "aegis.db")
    with sqlite3.connect(hermes_home / "aegis.db") as conn:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(task_audit)")]
    assert columns == ["id", "uid", "uname", "session_id", "prompt", "create_time"]
    assert store.query_task_audits().logs == [
        {
            "id": "legacy-task",
            "uid": "u-legacy",
            "uname": "Legacy",
            "session_id": "legacy-session",
            "prompt": "Legacy prompt",
            "create_time": "2026-07-21T01:00:00.000000Z",
        }
    ]


def test_task_audit_api_combines_text_and_create_time_filters(
    client: TestClient,
    hermes_home,
) -> None:
    _record_task_logs(hermes_home)
    response = client.get(
        "/api/audit/tasks",
        headers=AUTH_HEADERS,
        params={
            "id": "TASK",
            "uid": "U-1",
            "uname": "ALI",
            "session_id": "SESSION-1",
            "prompt": "PHISHING",
            "create_time_from": "2026-07-21T00:30:00Z",
            "create_time_to": "2026-07-21T01:30:00Z",
        },
    )
    assert response.status_code == 200
    assert response.json() == {
        "logs": [
            {
                "id": "task-1",
                "uid": "u-1",
                "uname": "Alice",
                "session_id": "session-1",
                "prompt": "Investigate phishing",
                "create_time": "2026-07-21T01:00:00.000000Z",
            }
        ],
        "total": 1,
        "page": 1,
        "page_size": 50,
    }


def test_audit_api_pages_newest_first(client: TestClient, hermes_home) -> None:
    _record_logs(hermes_home)
    first = client.get(
        "/api/audit/a2a-delegates",
        headers=AUTH_HEADERS,
        params={"page": 1, "page_size": 2},
    )
    assert first.status_code == 200
    assert first.json()["total"] == 3
    assert first.json()["page_size"] == 2
    assert [row["id"] for row in first.json()["logs"]] == ["audit-3", "audit-2"]

    second = client.get(
        "/api/audit/a2a-delegates",
        headers=AUTH_HEADERS,
        params={"page": 2, "page_size": 2},
    )
    assert [row["id"] for row in second.json()["logs"]] == ["audit-1"]


def test_audit_api_combines_independent_filters(client: TestClient, hermes_home) -> None:
    _record_logs(hermes_home)
    response = client.get(
        "/api/audit/a2a-delegates",
        headers=AUTH_HEADERS,
        params={
            "id": "audit",
            "platform": "AEG",
            "user_id": "u-1",
            "user_name": "ali",
            "agent_name": "POND",
            "goal": "PHISHING",
            "session_id": "SESSION",
            "status": "auth_denied",
            "is_loop": "true",
            "is_delegate_output": "true",
            "timestamp_from": "2026-07-21T02:30:00Z",
            "timestamp_to": "2026-07-21T03:30:00Z",
        },
    )
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["logs"][0]["id"] == "audit-3"


def test_audit_api_requires_admin(client: TestClient) -> None:
    create = client.post(
        "/api/users",
        headers=AUTH_HEADERS,
        json={
            "username": "analyst",
            "password": "Password123!",
            "email": "analyst@example.com",
            "status": "enabled",
        },
    )
    assert create.status_code == 201
    login = client.post(
        "/api/auth/login",
        json={"username": "analyst", "password": "Password123!"},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    response = client.get("/api/audit/a2a-delegates", headers=headers)
    assert response.status_code == 403
    task_response = client.get("/api/audit/tasks", headers=headers)
    assert task_response.status_code == 403


def test_audit_api_validates_pagination_and_status(client: TestClient) -> None:
    assert client.get(
        "/api/audit/a2a-delegates",
        headers=AUTH_HEADERS,
        params={"page": 0},
    ).status_code == 422


def test_overview_stats_api_returns_aggregates_to_authenticated_non_admin_users(
    client: TestClient,
    hermes_home,
) -> None:
    store = AegisDelegateStore(hermes_home / "aegis.db")
    store.record_audit(
        platform="slack",
        user_id="u-1",
        user_name="Alice",
        agent_name="responder",
        goal="Investigate phishing",
        session_id="remote-1",
        is_loop=False,
        is_delegate_output=True,
        status="succ",
        timestamp=datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z"),
    )
    create = client.post(
        "/api/users",
        headers=AUTH_HEADERS,
        json={
            "username": "analyst",
            "password": "Password123!",
            "email": "analyst@example.com",
            "status": "enabled",
        },
    )
    assert create.status_code == 201
    login = client.post(
        "/api/auth/login",
        json={"username": "analyst", "password": "Password123!"},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    unauthenticated = client.get("/api/overview/stats")
    assert unauthenticated.status_code == 401

    response = client.get("/api/overview/stats", headers=headers)
    assert response.status_code == 200
    assert response.json() == {
        "window_start": response.json()["window_start"],
        "window_end": response.json()["window_end"],
        "executing_agent_count": 1,
        "source_platform_count": 1,
        "active_user_count": 1,
        "delegation_total": 1,
        "success_count": 1,
        "success_rate": 1.0,
        "status_counts": {"succ": 1, "fail": 0, "auth_denied": 0},
        "comparison": {
            "previous_delegation_total": 0,
            "delegation_volume_change_percent": None,
            "previous_success_rate": None,
            "success_rate_change_percentage_points": None,
        },
    }
    assert client.get(
        "/api/audit/a2a-delegates",
        headers=AUTH_HEADERS,
        params={"status": "pending"},
    ).status_code == 422


def test_overview_topology_api_returns_the_three_layer_starmapping(client: TestClient) -> None:
    unauthenticated = client.get("/api/overview/topology")
    assert unauthenticated.status_code == 401

    response = client.get("/api/overview/topology", headers=AUTH_HEADERS)
    assert response.status_code == 200
    topology = response.json()

    assert topology["center"] == {
        "id": "aegis",
        "layer": "center",
        "name": "Aegis",
        "symbol": "aegis-connection",
        "role": "Security & Operations Orchestration Core",
        "description": "跨域任务路由、策略约束、Agent 委派、证据汇聚、风险升级与可审计闭环的中枢。",
        "capabilities": [
            "intent-routing",
            "policy-enforcement",
            "agent-delegation",
            "evidence-correlation",
            "risk-prioritization",
            "audit-trail",
        ],
        "layout": {"x": 0.5, "y": 0.5, "radius": "core"},
    }
    assert len(topology["agents"]) == 7
    assert sum(len(agent["star_nodes"]) for agent in topology["agents"]) == 84
    assert len(topology["edges"]) == 91
    assert topology["agents"][0]["id"] == "ai-soc"
    assert topology["agents"][0]["symbol"] == "argus-eyes"
    assert [agent["product_service_code"] for agent in topology["agents"]] == [
        "WORKAGENT-AI-SOC",
        "WORKAGENT-AI-GRC",
        "WORKAGENT-AI-REDTEAM",
        "WORKAGENT-AI-SDLC",
        "WORKAGENT-AI-UEBA",
        "WORKAGENT-AI-ITOps",
        "WORKAGENT-AI-Web3",
    ]
    assert topology["agents"][0]["runtime"] == {
        "status": "planned",
        "source": "starmapping_baseline",
    }


def test_overview_topology_api_overlays_matching_a2a_agent_runtime(client: TestClient) -> None:
    created = client.post(
        "/api/agents/ai-soc",
        headers=AUTH_HEADERS,
        json={
            "url": "http://127.0.0.1:9086/a2a",
            "description": "Argus runtime",
            "headers": {},
            "status": "active",
            "extcapabilities": [],
        },
    )
    assert created.status_code == 201

    response = client.get("/api/overview/topology", headers=AUTH_HEADERS)
    assert response.status_code == 200
    argus = next(agent for agent in response.json()["agents"] if agent["id"] == "ai-soc")
    assert argus["runtime"] == {"status": "active", "source": "a2a_registry"}


def test_overview_topology_api_falls_back_for_legacy_starmapping_data(client: TestClient, monkeypatch) -> None:
    from aegis.backend.routes import overview

    legacy_starmapping = deepcopy(overview._load_starmapping())
    legacy_starmapping["agent_ring"][0].pop("product_service_code")
    monkeypatch.setattr(overview, "_load_starmapping", lambda: legacy_starmapping)

    response = client.get("/api/overview/topology", headers=AUTH_HEADERS)

    assert response.status_code == 200
    argus = next(agent for agent in response.json()["agents"] if agent["id"] == "ai-soc")
    assert argus["product_service_code"] == "WORKAGENT-AI-SOC"


def test_topology_openapi_schema_includes_product_service_code(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()

    topology_schema = schema["components"]["schemas"]["TopologyAgentResponse"]
    assert "product_service_code" in topology_schema["properties"]
    assert "product_service_code" in topology_schema["required"]
