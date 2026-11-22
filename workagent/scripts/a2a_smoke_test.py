#!/usr/bin/env python3
"""Smoke-test a Hermes A2A server with the official A2A SDK."""

from __future__ import annotations

import argparse
import asyncio
import uuid
from collections.abc import Iterable
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


VALID_TASK_STATES = frozenset(
    {
        TaskState.TASK_STATE_SUBMITTED,
        TaskState.TASK_STATE_WORKING,
        TaskState.TASK_STATE_COMPLETED,
        TaskState.TASK_STATE_FAILED,
        TaskState.TASK_STATE_CANCELED,
        TaskState.TASK_STATE_INPUT_REQUIRED,
        TaskState.TASK_STATE_REJECTED,
        TaskState.TASK_STATE_AUTH_REQUIRED,
    }
)
TERMINAL_TASK_STATES = frozenset(
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


def _task_text(task: Task) -> str:
    message = task.status.message
    parts = list(getattr(message, "parts", []) or [])
    if not parts or not getattr(parts[0], "text", ""):
        raise AssertionError(f"Task {task.id!r} has no text response: {task!r}")
    return parts[0].text


async def _send_text(
    client: Client,
    text: str,
    *,
    context_id: str | None = None,
    task_id: str | None = None,
) -> Task:
    request = SendMessageRequest(
        message=Message(
            message_id=str(uuid.uuid4()),
            role=Role.ROLE_USER,
            context_id=context_id or "",
            task_id=task_id or "",
            parts=[Part(text=text)],
        ),
        configuration=SendMessageConfiguration(return_immediately=True),
    )
    responses = [event async for event in client.send_message(request)]
    if not responses:
        raise RuntimeError("A2A SDK returned no response events.")
    first = responses[0]
    if first.HasField("task"):
        return first.task
    if first.HasField("message"):
        return Task(
            id=first.message.task_id,
            context_id=first.message.context_id,
            status={"state": TaskState.TASK_STATE_COMPLETED},
            history=[first.message],
        )
    raise RuntimeError("Unexpected A2A response without task or message.")


def _event_text(event) -> str:
    if event.HasField("message"):
        parts = list(getattr(event.message, "parts", []) or [])
        return "".join(part.text for part in parts if getattr(part, "text", ""))
    if event.HasField("task"):
        status = getattr(event.task, "status", None)
        message = getattr(status, "message", None)
        parts = list(getattr(message, "parts", []) or [])
        return "".join(part.text for part in parts if getattr(part, "text", ""))
    if event.HasField("status_update"):
        message = getattr(event.status_update.status, "message", None)
        parts = list(getattr(message, "parts", []) or [])
        return "".join(part.text for part in parts if getattr(part, "text", ""))
    return ""


def _event_task_info(event) -> tuple[str | None, str | None]:
    if event.HasField("task"):
        return event.task.id or None, event.task.context_id or None
    if event.HasField("message"):
        return event.message.task_id or None, event.message.context_id or None
    if event.HasField("status_update"):
        return event.status_update.task_id or None, event.status_update.context_id or None
    return None, None


def _event_state(event) -> int | None:
    if event.HasField("task"):
        return event.task.status.state
    if event.HasField("status_update"):
        return event.status_update.status.state
    if event.HasField("message"):
        return TaskState.TASK_STATE_COMPLETED
    return None


def _state_label(state: int | None) -> str:
    if state is None:
        return "unknown"
    try:
        return TaskState.Name(state).replace("TASK_STATE_", "").lower()
    except Exception:
        return str(state)


async def _wait_for_state(
    client: Client,
    task_id: str,
    expected_states: Iterable[int],
    *,
    timeout: float = 60.0,
    poll_interval: float = 0.02,
) -> Task:
    expected = set(expected_states)
    invalid = expected - VALID_TASK_STATES
    if invalid:
        raise ValueError(f"Unsupported task states: {sorted(invalid)}")

    deadline = asyncio.get_running_loop().time() + timeout
    last_task: Task | None = None
    while asyncio.get_running_loop().time() < deadline:
        task = await client.get_task(GetTaskRequest(id=task_id))
        state = task.status.state
        if state not in VALID_TASK_STATES:
            raise AssertionError(f"Server returned invalid task state: {state!r}")
        last_task = task
        if state in expected:
            return task
        await asyncio.sleep(poll_interval)
    raise TimeoutError(
        f"Timed out waiting for states {sorted(expected)} after {timeout}s; "
        f"last={last_task}"
    )


async def _run_single_turn(
    client: Client,
    message: str,
    *,
    expected_response: str | None = None,
    timeout: float = 60.0,
) -> Task:
    task = await _send_text(client, message)
    completed = await _wait_for_state(client, task.id, {TaskState.TASK_STATE_COMPLETED}, timeout=timeout)
    actual = _task_text(completed)
    if expected_response is not None and actual != expected_response:
        raise AssertionError(
            f"Single-turn response mismatch: expected={expected_response!r} actual={actual!r}"
        )
    print(f"single_turn: ok -> {actual}")
    return completed


async def _run_streaming_turn(
    client: Client,
    message: str,
    *,
    context_id: str | None = None,
    expected_response: str | None = None,
    timeout: float = 60.0,
) -> Task:
    request = SendMessageRequest(
        message=Message(
            message_id=str(uuid.uuid4()),
            role=Role.ROLE_USER,
            context_id=context_id or "",
            task_id="",
            parts=[Part(text=message)],
        ),
        configuration=SendMessageConfiguration(return_immediately=True),
    )
    task_id: str | None = None
    resolved_context_id = context_id
    working_texts: list[str] = []
    final_text = ""
    terminal_state: int | None = None

    async for event in client.send_message(request):
        task_id, event_context_id = _event_task_info(event)
        resolved_context_id = event_context_id or resolved_context_id
        state = _event_state(event)
        text = _event_text(event)
        if state == TaskState.TASK_STATE_WORKING and text:
            working_texts.append(text)
            print(f"stream_event: {_state_label(state)} -> {text}")
        if state in TERMINAL_TASK_STATES and text:
            final_text = text
            terminal_state = state

    if not task_id:
        raise RuntimeError("Streaming response did not expose a task id.")

    completed = await _wait_for_state(client, task_id, {TaskState.TASK_STATE_COMPLETED}, timeout=timeout)
    actual = _task_text(completed)
    if expected_response is not None and actual != expected_response:
        raise AssertionError(
            f"Streaming response mismatch: expected={expected_response!r} actual={actual!r}"
        )
    if not working_texts:
        raise AssertionError(
            f"Streaming response did not include working text updates. final={final_text!r} state={terminal_state!r}"
        )
    if resolved_context_id and completed.context_id != resolved_context_id:
        raise AssertionError(
            f"Context mismatch after streaming turn: expected={resolved_context_id!r} actual={completed.context_id!r}"
        )
    print(f"streaming_turn: ok -> {actual}")
    return completed


async def _run_multi_turn(
    client: Client,
    first_message: str,
    second_message: str,
    *,
    expected_first_response: str | None = None,
    expected_second_response: str | None = None,
    timeout: float = 60.0,
) -> tuple[Task, Task]:
    first_task = await _send_text(client, first_message)
    first_done = await _wait_for_state(client, first_task.id, {TaskState.TASK_STATE_COMPLETED}, timeout=timeout)
    first_actual = _task_text(first_done)
    if expected_first_response is not None and first_actual != expected_first_response:
        raise AssertionError(
            f"Multi-turn first response mismatch: expected={expected_first_response!r} actual={first_actual!r}"
        )

    second_task = await _send_text(
        client,
        second_message,
        context_id=first_done.context_id,
    )
    second_done = await _wait_for_state(client, second_task.id, {TaskState.TASK_STATE_COMPLETED}, timeout=timeout)
    second_actual = _task_text(second_done)
    if expected_second_response is not None and second_actual != expected_second_response:
        raise AssertionError(
            f"Multi-turn second response mismatch: expected={expected_second_response!r} actual={second_actual!r}"
        )
    print(f"multi_turn: ok -> {first_actual} | {second_actual}")
    return first_done, second_done


async def _create_client(
    base_url: str,
    *,
    streaming: bool = False,
    headers: dict[str, str] | None = None,
) -> tuple[httpx.AsyncClient, Client]:
    http_client = httpx.AsyncClient(headers=headers)
    factory = ClientFactory(
        ClientConfig(
            httpx_client=http_client,
            streaming=streaming,
            polling=True,
        )
    )
    sdk_client = await factory.create_from_url(base_url)
    return http_client, sdk_client


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Smoke-test a Hermes A2A server.")
    parser.add_argument("--base-url", default="http://127.0.0.1:9086")
    parser.add_argument("--single-message", default="hello from a2a smoke")
    parser.add_argument("--multi-first-message", default="first turn")
    parser.add_argument("--multi-second-message", default="follow up")
    parser.add_argument("--expected-single-response")
    parser.add_argument("--expected-multi-first-response")
    parser.add_argument("--expected-multi-second-response")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--streaming", action="store_true")
    parser.add_argument("--auth-token", help="Bearer token for protected A2A HTTP routes.")
    args = parser.parse_args(argv)

    base_url = args.base_url.rstrip("/")
    headers = _auth_headers(args.auth_token)

    async with httpx.AsyncClient() as probe:
        card_response = await probe.get(
            f"{base_url}/.well-known/agent-card.json",
            headers=headers,
        )
        card_response.raise_for_status()
        card = card_response.json()
        print(f"agent_card_url: {card['supportedInterfaces'][0]['url']}")

    http_client, sdk_client = await _create_client(
        base_url,
        streaming=args.streaming,
        headers=headers,
    )
    try:
        if args.streaming:
            first_done = await _run_streaming_turn(
                sdk_client,
                args.single_message,
                expected_response=args.expected_single_response,
                timeout=args.timeout,
            )
            second_done = await _run_streaming_turn(
                sdk_client,
                args.multi_first_message,
                context_id=first_done.context_id,
                expected_response=args.expected_multi_first_response,
                timeout=args.timeout,
            )
            await _run_streaming_turn(
                sdk_client,
                args.multi_second_message,
                context_id=second_done.context_id,
                expected_response=args.expected_multi_second_response,
                timeout=args.timeout,
            )
        else:
            await _run_single_turn(
                sdk_client,
                args.single_message,
                expected_response=args.expected_single_response,
                timeout=args.timeout,
            )
            await _run_multi_turn(
                sdk_client,
                args.multi_first_message,
                args.multi_second_message,
                expected_first_response=args.expected_multi_first_response,
                expected_second_response=args.expected_multi_second_response,
                timeout=args.timeout,
            )
    finally:
        await sdk_client.close()
        await http_client.aclose()

    print("a2a smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
