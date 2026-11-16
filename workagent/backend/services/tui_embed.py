"""TUI embedding helpers for WORKAGENT chat."""

from __future__ import annotations

from typing import Optional
import os
import re
import urllib.parse

from workagent.backend.services import session_service

try:
    from hermes_cli.pty_bridge import PtyBridge, PtyUnavailableError

    PTY_BRIDGE_AVAILABLE = True
except ImportError:  # pragma: no cover - platform specific
    PtyBridge = None  # type: ignore[assignment]
    PTY_BRIDGE_AVAILABLE = False

    class PtyUnavailableError(RuntimeError):  # type: ignore[no-redef]
        """Raised when PTY bridge is unavailable on current platform."""


RESIZE_RE = re.compile(rb"\x1b\[RESIZE:(\d+);(\d+)\]")
VALID_CHANNEL_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def pty_bridge_available() -> bool:
    return PTY_BRIDGE_AVAILABLE


def resolve_chat_argv(
    resume: Optional[str] = None,
    sidecar_url: Optional[str] = None,
) -> tuple[list[str], Optional[str], Optional[dict]]:
    """Resolve argv + cwd + env for the embedded `hermes --tui` child."""
    from hermes_cli.main import PROJECT_ROOT, _make_tui_argv

    argv, cwd = _make_tui_argv(PROJECT_ROOT / "ui-tui", tui_dev=False)
    env = os.environ.copy()
    env.setdefault("NODE_ENV", "production")
    env.setdefault("HERMES_TUI_DISABLE_MOUSE", "1")
    env.setdefault("HERMES_TUI_INLINE", "1")

    if resume:
        latest = session_service.get_latest_descendant(resume)
        if latest and latest.get("session_id"):
            resume = str(latest["session_id"])
        env["HERMES_TUI_RESUME"] = resume

    if sidecar_url:
        env["HERMES_TUI_SIDECAR_URL"] = sidecar_url

    return list(argv), str(cwd) if cwd else None, env


def parse_resize_escape(raw: bytes) -> tuple[int, int] | None:
    match = RESIZE_RE.match(raw)
    if not match or match.end() != len(raw):
        return None
    cols = int(match.group(1))
    rows = int(match.group(2))
    return cols, rows


def channel_or_none(channel: str) -> str | None:
    cleaned = (channel or "").strip()
    if VALID_CHANNEL_RE.match(cleaned):
        return cleaned
    return None


def build_sidecar_url(
    *,
    host: str,
    port: int,
    token: str,
    channel: str,
) -> str:
    """Build websocket URL for PTY sidecar publishing endpoint."""
    connect_host = host
    if host == "0.0.0.0":
        connect_host = "127.0.0.1"
    elif host == "::":
        connect_host = "::1"

    netloc = (
        f"[{connect_host}]:{port}"
        if ":" in connect_host and not connect_host.startswith("[")
        else f"{connect_host}:{port}"
    )
    qs = urllib.parse.urlencode({"token": token, "channel": channel})
    return f"ws://{netloc}/api/chat/pub?{qs}"
