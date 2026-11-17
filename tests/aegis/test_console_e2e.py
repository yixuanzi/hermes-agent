from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from tests.aegis.backend.conftest import BOOTSTRAP_PASSWORD


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.mark.asyncio
async def test_hermes_aegis_e2e_hosted_console_and_api_roundtrip(tmp_path) -> None:
    port = _free_port()
    (tmp_path / ".env").write_text(
        f"AEGIS_BOOTSTRAP_ADMIN_PASSWORD={BOOTSTRAP_PASSWORD}\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["HERMES_HOME"] = str(tmp_path)
    env["PYTHONPATH"] = os.getcwd()
    env["PYTHONUNBUFFERED"] = "1"
    env["AEGIS_BOOTSTRAP_ADMIN_PASSWORD"] = BOOTSTRAP_PASSWORD

    frontend_dir = Path(__file__).resolve().parents[2] / "aegis" / "frontend"
    build_result = await asyncio.to_thread(
        subprocess.run,
        ["npm", "run", "build"],
        cwd=frontend_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert build_result.returncode == 0, f"stdout={build_result.stdout}\nstderr={build_result.stderr}"

    proc = await asyncio.to_thread(
        subprocess.Popen,
        [
            sys.executable,
            "-m",
            "hermes_cli.main",
            "aegis",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--no-open",
            "--skip-build",
        ],
        cwd=os.getcwd(),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        username = "admin"
        password = BOOTSTRAP_PASSWORD

        async with httpx.AsyncClient() as probe:
            deadline = time.monotonic() + 60
            last_error = None
            while time.monotonic() < deadline:
                try:
                    response = await probe.get(f"http://127.0.0.1:{port}/health")
                    if response.status_code == 200:
                        break
                except Exception as exc:  # pragma: no cover - diagnostic path
                    last_error = exc
                await asyncio.sleep(0.2)
            else:
                stdout, stderr = proc.communicate(timeout=5)
                raise AssertionError(
                    f"Aegis server did not become healthy. last_error={last_error!r}\nstdout={stdout}\nstderr={stderr}"
                )

            login_page = await probe.get(f"http://127.0.0.1:{port}/login")
            assert login_page.status_code == 200
            assert 'id="root"' in login_page.text or "id='root'" in login_page.text

            unauthorized = await probe.get(f"http://127.0.0.1:{port}/api/agents")
            assert unauthorized.status_code == 401

            login_response = await probe.post(
                f"http://127.0.0.1:{port}/api/auth/login",
                json={"username": username, "password": password},
            )
            assert login_response.status_code == 200
            payload = login_response.json()
            assert payload["authenticated"] is True
            token = payload["access_token"]

            headers = {"Authorization": f"Bearer {token}"}

            agents_before = await probe.get(f"http://127.0.0.1:{port}/api/agents", headers=headers)
            assert agents_before.status_code == 200
            assert agents_before.json() == {"agents": [], "invalid": []}

            create_agent = await probe.post(
                f"http://127.0.0.1:{port}/api/agents/e2e-agent",
                headers=headers,
                json={
                    "url": "127.0.0.1:9086/a2a",
                    "description": "End-to-end test agent",
                    "headers": {"Authorization": "Bearer upstream"},
                    "status": "active",
                    "extcapabilities": ["query-domain", "query-ip"],
                },
            )
            assert create_agent.status_code == 201
            assert create_agent.json()["agent_id"] == "e2e-agent"
            assert create_agent.json()["url"] == "http://127.0.0.1:9086/a2a"

            update_agent = await probe.put(
                f"http://127.0.0.1:{port}/api/agents/e2e-agent",
                headers=headers,
                json={
                    "url": "http://127.0.0.1:9087/a2a",
                    "description": "Updated e2e agent",
                    "headers": {},
                    "status": "idle",
                    "extcapabilities": ["query-url"],
                },
            )
            assert update_agent.status_code == 200
            assert update_agent.json()["status"] == "idle"

            create_rule = await probe.post(
                f"http://127.0.0.1:{port}/api/routing/global",
                headers=headers,
                json={
                    "name": "E2E Rule",
                    "policy": "Route suspicious domains to e2e-agent",
                    "status": "active",
                },
            )
            assert create_rule.status_code == 201
            rule_id = create_rule.json()["id"]

            update_rule = await probe.put(
                f"http://127.0.0.1:{port}/api/routing/global/{rule_id}",
                headers=headers,
                json={
                    "name": "E2E Rule Updated",
                    "policy": "Disable fallback route during maintenance",
                    "status": "inactive",
                },
            )
            assert update_rule.status_code == 200
            assert update_rule.json()["status"] == "inactive"

            delete_rule = await probe.delete(
                f"http://127.0.0.1:{port}/api/routing/global/{rule_id}",
                headers=headers,
            )
            assert delete_rule.status_code == 200
            assert delete_rule.json() == {"deleted": True, "id": rule_id}

            delete_agent = await probe.delete(
                f"http://127.0.0.1:{port}/api/agents/e2e-agent",
                headers=headers,
            )
            assert delete_agent.status_code == 200
            assert delete_agent.json() == {"deleted": True, "agent_id": "e2e-agent"}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
