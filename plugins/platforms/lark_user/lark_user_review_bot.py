"""The review bot: a separate Lark app that sends review cards to the user and receives their clicks.

Card button callbacks (``card.action.trigger``) only reach an app's event connection, never a user
token, and a Lark app's long connection hands each event to ONE of its connected clients: the app
must not be the one another gateway platform (Feishu) already holds open. Credentials come from
``LARK_USER_INTERACTION_APP_ID`` / ``LARK_USER_INTERACTION_APP_SECRET`` and reach lark-cli only
through the child's environment (no lark-cli profile is created). lark-cli does not mint a tenant
token from an env secret, so this module fetches one and hands it over with the credentials.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import time
from typing import Any, Awaitable, Callable, Optional

import httpx

from .lark_user_cli import LarkCli, LarkCliError, SentMessage

logger = logging.getLogger(__name__)

APP_ID_ENV = "LARK_USER_INTERACTION_APP_ID"
APP_SECRET_ENV = "LARK_USER_INTERACTION_APP_SECRET"
_OPEN_API = {"lark": "https://open.larksuite.com", "feishu": "https://open.feishu.cn"}
_TOKEN_MARGIN = 600.0  # refresh this long before the token expires
_CONSUME_WINDOW = "90m"  # restart the consumer well inside the token's 2h life
_ONLINE_RE = re.compile(r"online_instance_cnt=(\d+)")

OnAction = Callable[[dict[str, Any]], Awaitable[None]]


class ReviewBot:
    def __init__(self, *, cli: LarkCli, app_id: str, app_secret: str, brand: str):
        self._cli = cli
        self.app_id = app_id
        self._secret = app_secret
        self._brand = brand if brand in _OPEN_API else "feishu"
        self._token = ""
        self._token_expiry = 0.0
        self._consumer: Optional[asyncio.subprocess.Process] = None

    async def _tenant_token(self) -> str:
        if self._token and time.time() < self._token_expiry - _TOKEN_MARGIN:
            return self._token
        url = f"{_OPEN_API[self._brand]}/open-apis/auth/v3/tenant_access_token/internal"
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                data = (await client.post(url, json={"app_id": self.app_id, "app_secret": self._secret})).json()
        except (httpx.HTTPError, ValueError) as exc:
            raise LarkCliError("timeout", f"review app token request failed: {exc}") from None
        if data.get("code") != 0 or not data.get("tenant_access_token"):
            raise LarkCliError("auth", f"review app rejected its credentials: {data.get('msg') or data.get('code')}")
        self._token = str(data["tenant_access_token"])
        self._token_expiry = time.time() + float(data.get("expire") or 7200)
        return self._token

    async def _env(self) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if not k.startswith("LARKSUITE_CLI_")}
        env.update(LARKSUITE_CLI_APP_ID=self.app_id, LARKSUITE_CLI_APP_SECRET=self._secret,
                   LARKSUITE_CLI_BRAND=self._brand, LARKSUITE_CLI_TENANT_ACCESS_TOKEN=await self._tenant_token(),
                   LARKSUITE_CLI_NO_UPDATE_NOTIFIER="1")
        return env

    async def _call(self, args: list[str], *, stdin: Optional[str] = None) -> dict[str, Any]:
        return await self._cli.call(args, stdin=stdin, env=await self._env())

    async def check(self) -> str:
        """Validate the credentials; returns the bot's name."""
        info = await self._call(["api", "GET", "/open-apis/bot/v3/info", "--as", "bot"])
        return str((info.get("bot") or {}).get("app_name") or "")

    async def send_card(self, union_id: str, card: str) -> SentMessage:
        body = json.dumps({"receive_id": union_id, "msg_type": "interactive", "content": card}, ensure_ascii=False)
        data = await self._call(["api", "POST", "/open-apis/im/v1/messages", "--params",
                                 json.dumps({"receive_id_type": "union_id"}), "--data", "-", "--as", "bot"], stdin=body)
        return SentMessage(str(data.get("message_id") or ""), str(data.get("chat_id") or ""))

    async def update_after_click(self, token: str, card: str) -> None:
        """Re-render a clicked card through the click's delayed-update token (30 min, 2 uses): the
        path Lark honours right after an interaction, where a plain message patch can be dropped."""
        await self._call(["api", "POST", "/open-apis/interactive/v1/card/update", "--data", "-", "--as", "bot"],
                         stdin=json.dumps({"token": token, "card": json.loads(card)}, ensure_ascii=False))

    async def patch_card(self, message_id: str, card: str) -> None:
        await self._call(["im", "messages", "patch", "--as", "bot", "--params", json.dumps({"message_id": message_id}),
                          "--data", "-"], stdin=json.dumps({"content": card}, ensure_ascii=False))

    # ── click intake ────────────────────────────────────────────────────────

    async def consume(self, on_action: OnAction, *, max_backoff: float = 300.0) -> None:
        """Feed every card click to ``on_action`` until cancelled; restarts on exit or failure."""
        backoff = 5.0
        while True:
            started = time.monotonic()
            try:
                await self._consume_once(on_action)
            except asyncio.CancelledError:
                raise
            except Exception:  # a dead consumer must come back, whatever killed it
                logger.warning("lark_user: review card listener failed; restarting", exc_info=True)
            if time.monotonic() - started > 60:
                backoff = 5.0
            else:
                await asyncio.sleep(backoff)
                backoff = min(max_backoff, backoff * 2)

    async def _consume_once(self, on_action: OnAction) -> None:
        binary = self._cli.resolve_binary()
        if not binary:
            raise LarkCliError("missing", "lark-cli not found")
        proc = await asyncio.create_subprocess_exec(
            binary, "event", "consume", "card.action.trigger", "--as", "bot", "--timeout", _CONSUME_WINDOW,
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env=await self._env())
        self._consumer = proc
        stderr_task = asyncio.create_task(self._watch_stderr(proc))
        try:
            while line := await proc.stdout.readline():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if isinstance(event, dict):
                    await on_action(event)
        finally:
            await self._stop(proc)
            # Drain stderr to EOF so the pipe transport closes with the process, not at GC.
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stderr_task, timeout=5)
            self._consumer = None

    async def _watch_stderr(self, proc: asyncio.subprocess.Process) -> None:
        while line := await proc.stderr.readline():
            text = line.decode("utf-8", errors="replace").strip()
            if (match := _ONLINE_RE.search(text)) and int(match.group(1)) > 0:
                logger.warning("lark_user: review app %s already has %s other event connection(s); card clicks "
                               "will be split between them. Give the review bot an app nothing else connects to.",
                               self.app_id, match.group(1))
            elif text.startswith("{") or "error" in text.lower():
                # health: allow HX017 -- lark-cli's own stderr diagnostics (error envelopes, connection state); clicks arrive on stdout
                logger.warning("lark_user: review card listener: %s", text[:300])
            else:
                logger.debug("lark_user: review card listener: %s", text)

    @staticmethod
    async def _stop(proc: asyncio.subprocess.Process) -> None:
        if proc.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()  # SIGTERM: lark-cli cleans up its subscription; never kill -9 first
        try:
            await asyncio.wait_for(proc.wait(), timeout=10)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(proc.wait(), timeout=5)

    async def close(self) -> None:
        if self._consumer is not None:
            await self._stop(self._consumer)
