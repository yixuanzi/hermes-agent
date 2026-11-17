"""Rejected chat WebSockets deliver the documented close code to the client.

The routes used to close before ``accept()``; an ASGI server turns that into an HTTP 403
handshake failure (browser code 1006), so 4401/4403/4400 never reached the console.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from workagent.backend.config import WorkagentSettings
from workagent.backend.routes.chat import build_chat_router

_TOKEN = "s3cret-token"
_ENDPOINTS = ("pty", "ws", "pub?channel=c1", "events?channel=c1")


def _close_code(path: str, *, embedded_chat: bool = True) -> int:
    app = FastAPI()
    app.include_router(
        build_chat_router(WorkagentSettings(embedded_chat=embedded_chat, session_token=_TOKEN))
    )
    with TestClient(app) as client, client.websocket_connect(path) as ws:
        # Entering the block means the handshake completed; the reason comes as a close frame.
        message = ws.receive()
    assert message["type"] == "websocket.close"
    return message["code"]


def _with_token(endpoint: str, token: str) -> str:
    sep = "&" if "?" in endpoint else "?"
    return f"/api/chat/{endpoint}{sep}token={token}"


@pytest.mark.parametrize("endpoint", _ENDPOINTS)
def test_bad_token_and_disabled_chat_close_with_their_codes(endpoint: str) -> None:
    assert _close_code(_with_token(endpoint, "wrong")) == 4401
    assert _close_code(_with_token(endpoint, _TOKEN), embedded_chat=False) == 4403


@pytest.mark.parametrize("endpoint", ("pub", "events"))
def test_missing_channel_closes_with_4400(endpoint: str) -> None:
    assert _close_code(_with_token(endpoint, _TOKEN)) == 4400
