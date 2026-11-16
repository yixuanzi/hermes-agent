from __future__ import annotations


def test_chat_quick_commands_use_the_default_agent_template(
    client,
    auth_headers: dict[str, str],
    monkeypatch,
) -> None:
    monkeypatch.delenv("USE_ACTIVE_AGENT_PROMPT", raising=False)
    assert client.post(
        "/api/agents/default-template-agent",
        headers=auth_headers,
        json={
            "url": "http://127.0.0.1:9010/a2a",
            "description": "Uses the default agent template.",
            "headers": {},
            "status": "active",
            "extcapabilities": [],
        },
    ).status_code == 201

    response = client.get("/api/chat/quick-commands", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["commands"][0]["content"] == "<use_agent>{name}</use_agent>"


def test_chat_quick_command_cache_reuses_results_and_invalidates_after_writes(
    client,
    auth_headers: dict[str, str],
    monkeypatch,
) -> None:
    service = client.app.state.quick_command_service
    calls = 0
    original_list_agents = service._agent_service.list_agents

    def _counted_list_agents():
        nonlocal calls
        calls += 1
        return original_list_agents()

    monkeypatch.setattr(service._agent_service, "list_agents", _counted_list_agents)

    assert client.get("/api/chat/quick-commands", headers=auth_headers).status_code == 200
    assert client.get("/api/chat/quick-commands", headers=auth_headers).status_code == 200
    assert calls == 1

    assert client.post(
        "/api/prompt-templates",
        headers=auth_headers,
        json={"tag": "Cached", "desc": "Invalidates this user's cache", "prompt": "Cached prompt"},
    ).status_code == 201
    assert client.get("/api/chat/quick-commands", headers=auth_headers).status_code == 200
    assert calls == 2

    assert client.post(
        "/api/agents/cache-agent",
        headers=auth_headers,
        json={
            "url": "http://127.0.0.1:9011/a2a",
            "description": "Globally invalidates shortcuts.",
            "headers": {},
            "status": "active",
            "extcapabilities": [],
        },
    ).status_code == 201
    assert client.get("/api/chat/quick-commands", headers=auth_headers).status_code == 200
    assert calls == 3

    assert client.post(
        "/api/system-instructs",
        headers=auth_headers,
        json={
            "name": "Cache instruction",
            "describe": "Globally invalidates shortcuts.",
            "instruct": "Use cache-safe handling.",
            "status": "enabled",
        },
    ).status_code == 201
    assert client.get("/api/chat/quick-commands", headers=auth_headers).status_code == 200
    assert calls == 4


def test_chat_quick_commands_are_authenticated_and_aggregate_available_content(
    client,
    auth_headers: dict[str, str],
    monkeypatch,
) -> None:
    assert client.get("/api/chat/quick-commands").status_code == 401

    monkeypatch.setenv("USE_ACTIVE_AGENT_PROMPT", "Delegate to {agent_name} as {name}")
    active_agent = client.post(
        "/api/agents/incident-responder",
        headers=auth_headers,
        json={
            "url": "http://127.0.0.1:9086/a2a",
            "description": "Investigates active incidents.",
            "headers": {},
            "status": "active",
            "extcapabilities": [],
        },
    )
    assert active_agent.status_code == 201
    idle_agent = client.post(
        "/api/agents/idling-agent",
        headers=auth_headers,
        json={
            "url": "http://127.0.0.1:9087/a2a",
            "description": "Must not be offered.",
            "headers": {},
            "status": "idle",
            "extcapabilities": [],
        },
    )
    assert idle_agent.status_code == 201

    prompt = client.post(
        "/api/prompt-templates",
        headers=auth_headers,
        json={"tag": "Triage", "desc": "Assess incident severity.", "prompt": "Classify this alert."},
    )
    assert prompt.status_code == 201
    enabled_instruction = client.post(
        "/api/system-instructs",
        headers=auth_headers,
        json={
            "name": "Evidence handling",
            "describe": "Preserve evidence during response.",
            "instruct": "Do not modify original evidence.",
            "status": "enabled",
        },
    )
    assert enabled_instruction.status_code == 201
    disabled_instruction = client.post(
        "/api/system-instructs",
        headers=auth_headers,
        json={
            "name": "Disabled instruction",
            "describe": "Must not be offered.",
            "instruct": "Ignore this.",
            "status": "disabled",
        },
    )
    assert disabled_instruction.status_code == 201

    response = client.get("/api/chat/quick-commands", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == {
        "commands": [
            {
                "type": "agent",
                "name": "incident-responder",
                "desc": "Investigates active incidents.",
                "content": "Delegate to {agent_name} as {name}",
            },
            {
                "type": "instruct",
                "name": "Evidence handling",
                "desc": "Preserve evidence during response.",
                "content": "Do not modify original evidence.",
            },
            {
                "type": "prompt",
                "name": "Triage",
                "desc": "Assess incident severity.",
                "content": "Classify this alert.",
            },
        ]
    }


def test_chat_quick_commands_only_return_the_authenticated_users_prompts(
    client,
    auth_headers: dict[str, str],
) -> None:
    created_user = client.post(
        "/api/users",
        headers=auth_headers,
        json={
            "username": "quick-command-user",
            "password": "Password123!",
            "email": "quick-command-user@example.com",
            "status": "enabled",
        },
    )
    assert created_user.status_code == 201
    login = client.post(
        "/api/auth/login",
        json={"username": "quick-command-user", "password": "Password123!"},
    )
    assert login.status_code == 200
    user_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    assert client.post(
        "/api/prompt-templates",
        headers=auth_headers,
        json={"tag": "Admin prompt", "desc": "Admin only", "prompt": "Admin content"},
    ).status_code == 201
    assert client.post(
        "/api/prompt-templates",
        headers=user_headers,
        json={"tag": "Analyst prompt", "desc": "Analyst only", "prompt": "Analyst content"},
    ).status_code == 201

    response = client.get("/api/chat/quick-commands", headers=user_headers)

    assert response.status_code == 200
    assert [command for command in response.json()["commands"] if command["type"] == "prompt"] == [
        {
            "type": "prompt",
            "name": "Analyst prompt",
            "desc": "Analyst only",
            "content": "Analyst content",
        }
    ]
