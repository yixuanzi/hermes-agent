#!/usr/bin/env python3
"""Interactive CLI for chatting with an A2A agent endpoint."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
from collections.abc import Callable
from types import SimpleNamespace

import httpx
from a2a.client import Client, ClientConfig, ClientFactory
from a2a.types import (
    GetTaskRequest,
    Message,
    Part,
    Role,
    SendMessageConfiguration,
    SendMessageRequest,
    Task,
    TaskState,
)
from google.protobuf.json_format import MessageToDict


EXIT_COMMANDS = frozenset({"/exit", "/quit", "exit", "quit"})
RESET_COMMANDS = frozenset({"/reset"})
HELP_COMMANDS = frozenset({"/help", "/?"})
FINAL_TASK_STATES = frozenset(
    {
        TaskState.TASK_STATE_COMPLETED,
        TaskState.TASK_STATE_FAILED,
        TaskState.TASK_STATE_CANCELED,
        TaskState.TASK_STATE_INPUT_REQUIRED,
        TaskState.TASK_STATE_REJECTED,
        TaskState.TASK_STATE_AUTH_REQUIRED,
    }
)


def _auth_headers(auth_token: str | None) -> dict[str, str] | None:
    token = (auth_token or "").strip()
    if not token:
        return None
    return {"Authorization": f"Bearer {token}"}


def _field(value, name: str, default=None):
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _normalize_base_url(url: str) -> str:
    value = url.strip()
    if not value:
        raise ValueError("A2A agent URL cannot be empty.")
    if "://" not in value:
        value = f"http://{value}"
    value = value.removesuffix("/.well-known/agent-card.json")
    return value.rstrip("/")


def _agent_card_url(base_url: str) -> str:
    return f"{base_url}/.well-known/agent-card.json"


def _message_text(message) -> str:
    parts = list(_field(message, "parts", []) or [])
    chunks = [part.text for part in parts if getattr(part, "text", "")]
    if chunks:
        return "".join(chunks)

    content = _field(message, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_chunks = []
        for item in content:
            if isinstance(item, str):
                text_chunks.append(item)
                continue
            if isinstance(item, dict) and item.get("type") == "text":
                text_chunks.append(str(item.get("text", "")))
        return "".join(text_chunks)
    if content is None:
        return ""
    if isinstance(content, (dict, list)):
        return json.dumps(content, ensure_ascii=True, separators=(",", ":"))
    return str(content)


def _message_metadata(message) -> dict:
    metadata = _field(message, "metadata", None)
    if metadata is None:
        return {}
    if isinstance(metadata, dict):
        return metadata
    try:
        return MessageToDict(metadata)
    except Exception:
        return {}


def _hermes_metadata(message) -> dict:
    metadata = _message_metadata(message)
    hermes = metadata.get("hermes")
    return hermes if isinstance(hermes, dict) else {}


def _is_tool_message(message) -> bool:
    if message is None:
        return False
    hermes = _hermes_metadata(message)
    if hermes.get("kind") == "tool_result":
        return True
    role = _field(message, "role", None)
    return role in ("tool", getattr(Role, "ROLE_TOOL", object()))


def _message_tool_calls(message) -> list:
    tool_calls = list(_field(message, "tool_calls", []) or [])
    if tool_calls:
        return tool_calls
    hermes = _hermes_metadata(message)
    if hermes.get("kind") != "tool_call":
        return []
    return [
        {
            "id": hermes.get("tool_call_id", ""),
            "function": {
                "name": hermes.get("name", "tool"),
                "arguments": hermes.get("arguments", {}),
            },
        }
    ]


def _compact_json_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=True, separators=(",", ":"))
    text = str(value)
    if not text:
        return ""
    try:
        parsed = json.loads(text)
    except Exception:
        return text
    return json.dumps(parsed, ensure_ascii=True, separators=(",", ":"))


def _tool_call_details(tool_call) -> tuple[str, str, str]:
    call_id = str(_field(tool_call, "id", "") or "")
    function = _field(tool_call, "function", {}) or {}
    tool_name = str(_field(function, "name", "tool") or "tool")
    arguments = _compact_json_text(_field(function, "arguments", ""))
    return call_id, tool_name, arguments


def _tool_result_details(message, tool_names_by_call_id: dict[str, str]) -> tuple[str, str]:
    hermes = _hermes_metadata(message)
    if hermes.get("kind") == "tool_result":
        tool_name = str(hermes.get("name") or "tool")
        result = hermes.get("result", "")
        return tool_name, _compact_json_text(result)
    tool_call_id = str(_field(message, "tool_call_id", "") or "")
    tool_name = str(_field(message, "tool_name", "") or "")
    if not tool_name and tool_call_id:
        tool_name = tool_names_by_call_id.get(tool_call_id, "")
    if not tool_name:
        tool_name = "tool"
    return tool_name, _message_text(message)


def _is_agent_message(message) -> bool:
    if message is None:
        return False
    hermes = _hermes_metadata(message)
    if hermes.get("kind") in {"tool_call", "tool_result"}:
        return False
    role = _field(message, "role", None)
    return role in (None, Role.ROLE_AGENT, "assistant", "agent")


def _assistant_message_text(message) -> str:
    if not _is_agent_message(message):
        return ""
    return _message_text(message)


def _task_text(task: Task) -> str:
    status = getattr(task, "status", None)
    text = _assistant_message_text(getattr(status, "message", None))
    if text:
        return text

    for message in reversed(list(getattr(task, "history", []) or [])):
        text = _assistant_message_text(message)
        if text:
            return text
    return ""


def _status_update_text(status_update) -> str:
    if status_update is None:
        return ""
    status = _field(status_update, "status", None)
    return _assistant_message_text(_field(status, "message", None))


def _task_messages(task: Task) -> list:
    messages = list(_field(task, "history", []) or [])
    status = _field(task, "status", None)
    status_message = _field(status, "message", None)
    if status_message is not None:
        messages.append(status_message)
    return messages


def _event_messages(event) -> list:
    if event.HasField("task"):
        return _task_messages(event.task)
    if event.HasField("message"):
        return [event.message]
    if event.HasField("status_update"):
        status = _field(event.status_update, "status", None)
        message = _field(status, "message", None)
        return [message] if message is not None else []
    return []


def _event_to_task(event) -> Task | None:
    if event.HasField("task"):
        return event.task
    if event.HasField("message"):
        return Task(
            id=event.message.task_id,
            context_id=event.message.context_id,
            status={"state": TaskState.TASK_STATE_COMPLETED},
            history=[event.message],
        )
    if event.HasField("status_update"):
        status = event.status_update.status
        if hasattr(status, "CopyFrom"):
            return Task(
                id=event.status_update.task_id,
                context_id=event.status_update.context_id,
                status=status,
            )
        return SimpleNamespace(
            id=event.status_update.task_id,
            context_id=event.status_update.context_id,
            status=SimpleNamespace(
                state=getattr(status, "state", None),
                message=getattr(status, "message", None),
            ),
        )
    return None


def _event_text(event) -> str:
    if event.HasField("task"):
        return _task_text(event.task)
    if event.HasField("message"):
        return _assistant_message_text(event.message)
    if event.HasField("status_update"):
        return _status_update_text(event.status_update)
    return ""


def _text_delta(previous: str, current: str) -> str:
    if not current or current == previous:
        return ""
    if current.startswith(previous):
        return current[len(previous) :]
    return current


class A2AChatSession:
    """Maintains multi-turn context for an A2A conversation."""

    def __init__(
        self,
        client: Client,
        *,
        output=None,
        timeout: float = 60.0,
        poll_interval: float = 0.05,
    ) -> None:
        self.client = client
        self.output = output if output is not None else sys.stdout
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.context_id: str | None = None
        self.task_id: str | None = None
        self._line_start = True
        self._rendered_tool_entries: set[str] = set()
        self._tool_names_by_call_id: dict[str, str] = {}

    def reset(self) -> None:
        self.context_id = None
        self.task_id = None
        self._rendered_tool_entries.clear()
        self._tool_names_by_call_id.clear()

    def start_assistant_line(self) -> None:
        self._line_start = False

    async def send_and_stream(self, text: str) -> str:
        self._rendered_tool_entries.clear()
        self._tool_names_by_call_id.clear()
        task = await self._send_text(text)
        self.context_id = task.context_id or self.context_id
        self.task_id = task.id or self.task_id

        finished = await self._wait_and_stream(task)
        self.context_id = finished.context_id or self.context_id
        self.task_id = finished.id or self.task_id
        return _task_text(finished)

    async def _send_text(self, text: str) -> Task:
        request = SendMessageRequest(
            message=Message(
                message_id=str(uuid.uuid4()),
                role=Role.ROLE_USER,
                context_id=self.context_id or "",
                task_id="",
                parts=[Part(text=text)],
            ),
            configuration=SendMessageConfiguration(return_immediately=True),
        )
        rendered = ""
        last_task: Task | None = None
        got_response = False

        async for event in self.client.send_message(request):
            got_response = True
            self._render_tool_messages(_event_messages(event))
            last_task = _event_to_task(event) or last_task
            rendered = self._render_delta(rendered, _event_text(event))

        if not got_response:
            raise RuntimeError("A2A SDK returned no response events.")

        if last_task is None:
            raise RuntimeError("Unexpected A2A response without task or message.")
        return last_task

    def _render_delta(self, rendered: str, current_text: str) -> str:
        delta = _text_delta(rendered, current_text)
        if delta:
            if rendered and delta == current_text and not self._line_start:
                self._write("\n")
            self._write(delta)
            return current_text
        return rendered

    def _render_tool_messages(self, messages: list) -> None:
        for message in messages:
            if message is None:
                continue
            for tool_call in _message_tool_calls(message):
                call_id, tool_name, arguments = _tool_call_details(tool_call)
                if call_id:
                    self._tool_names_by_call_id[call_id] = tool_name
                call_repr = f"{tool_name}({arguments})" if arguments else f"{tool_name}()"
                key = f"assistant-tool:{call_id}:{tool_name}:{arguments}"
                self._write_line_once(key, f"Assistant(tool): {call_repr}")

            if _is_tool_message(message):
                tool_name, result_text = _tool_result_details(
                    message,
                    self._tool_names_by_call_id,
                )
                tool_call_id = str(_field(message, "tool_call_id", "") or "")
                key = f"tool:{tool_call_id}:{tool_name}:{result_text}"
                self._write_line_once(key, f"Tool: {tool_name} -> {result_text}")

    def _write(self, text: str) -> None:
        self.output.write(text)
        self.output.flush()
        self._line_start = text.endswith("\n")

    def _write_line_once(self, key: str, line: str) -> None:
        if key in self._rendered_tool_entries:
            return
        self._rendered_tool_entries.add(key)
        if not self._line_start:
            self._write("\n")
        self._write(f"{line}\n")

    async def _wait_and_stream(self, task: Task) -> Task:
        deadline = time.monotonic() + self.timeout
        current_task = task
        rendered = _task_text(task)

        while time.monotonic() < deadline:
            self._render_tool_messages(_task_messages(current_task))
            rendered = self._render_delta(rendered, _task_text(current_task))

            if current_task.status.state in FINAL_TASK_STATES:
                self._write("\n")
                return current_task

            await asyncio.sleep(self.poll_interval)
            current_task = await self.client.get_task(GetTaskRequest(id=current_task.id))

        raise TimeoutError(
            f"Timed out waiting for task {task.id!r} after {self.timeout:.1f}s."
        )


async def interactive_chat_loop(
    session: A2AChatSession,
    *,
    input_fn: Callable[[str], str] = input,
    output=None,
) -> int:
    stream = output if output is not None else sys.stdout
    while True:
        try:
            raw = await asyncio.to_thread(input_fn, "You> ")
        except EOFError:
            stream.write("\n")
            stream.flush()
            return 0
        except KeyboardInterrupt:
            stream.write("\n")
            stream.flush()
            return 130

        command = raw.strip()
        if not command:
            continue

        lowered = command.lower()
        if lowered in EXIT_COMMANDS:
            return 0
        if lowered in RESET_COMMANDS:
            session.reset()
            stream.write("Session reset.\n")
            stream.flush()
            continue
        if lowered in HELP_COMMANDS:
            stream.write("Commands: /reset, /quit\n")
            stream.flush()
            continue

        stream.write("Assistant> ")
        stream.flush()
        if hasattr(session, "start_assistant_line"):
            session.start_assistant_line()
        await session.send_and_stream(command)


async def _probe_agent_card(
    base_url: str,
    *,
    headers: dict[str, str] | None = None,
) -> tuple[str, dict]:
    async with httpx.AsyncClient() as client:
        response = await client.get(_agent_card_url(base_url), headers=headers)
        response.raise_for_status()
        card = response.json()

    supported = list(card.get("supportedInterfaces") or [])
    rpc_url = supported[0]["url"] if supported else base_url
    return _normalize_base_url(rpc_url), card


async def _create_client(
    base_url: str,
    *,
    headers: dict[str, str] | None = None,
) -> tuple[httpx.AsyncClient, Client]:
    http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(
            connect=10.0,
            read=None,
            write=60.0,
            pool=60.0,
        ),
        headers=headers,
    )
    factory = ClientFactory(
        ClientConfig(
            httpx_client=http_client,
            streaming=True,
            polling=True,
        )
    )
    sdk_client = await factory.create_from_url(base_url)
    return http_client, sdk_client


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Interactive multi-turn chat CLI for an A2A agent."
    )
    parser.add_argument("base_url", nargs="?", help="A2A agent base URL or agent-card URL.")
    parser.add_argument("--base-url", dest="base_url_flag")
    parser.add_argument("--auth-token", help="Bearer token for protected A2A HTTP routes.")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--poll-interval", type=float, default=0.05)
    args = parser.parse_args(argv)

    raw_url = args.base_url_flag or args.base_url
    if not raw_url:
        raw_url = input("A2A agent URL: ")
    base_url = _normalize_base_url(raw_url)
    headers = _auth_headers(args.auth_token)
    rpc_url, card = await _probe_agent_card(base_url, headers=headers)

    print(f"Connected to: {card.get('name') or 'A2A agent'}")
    print(f"RPC URL: {rpc_url}")
    print("Commands: /reset, /quit")

    http_client, sdk_client = await _create_client(rpc_url, headers=headers)
    try:
        session = A2AChatSession(
            sdk_client,
            output=sys.stdout,
            timeout=args.timeout,
            poll_interval=args.poll_interval,
        )
        return await interactive_chat_loop(session)
    finally:
        await sdk_client.close()
        await http_client.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
