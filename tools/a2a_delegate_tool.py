"""A2A foreground delegation tools."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import random
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit
from xml.sax.saxutils import escape

import httpx

from hermes_cli.profiles import get_active_profile_name
from hermes_constants import get_hermes_home
from tools import a2a_delegate_aegis
from tools.registry import registry, tool_error

logger = logging.getLogger(__name__)

_DELEGATE_FOREGROUND_INPUT_TIMEOUT_SECONDS = 25 * 60
_DELEGATE_INPUT_TIMEOUT = object()
_A2A_STOP_MESSAGE = "已按用户请求停止当前 A2A 委托，无需重试。"
_A2A_INPUT_TIMEOUT_MESSAGE = "waitting user input timeout,close remote session, do not retry"
_A2A_POLL_TIMEOUT_ENV = "A2A_POLL_TIMEOUT"
_A2A_POLL_INTERVAL_ENV = "A2A_POLL_INTERVAL"
_A2A_POLL_TIMEOUT_DEFAULT = 120.0
_A2A_POLL_INTERVAL_DEFAULT = 1.0
_A2A_CANCEL_STATUS_NOOP = "noop"
_A2A_CANCEL_STATUS_SENT = "sent"
_A2A_CANCEL_STATUS_COMPLETED = "completed"
_ACTIVE_A2A_SESSION_ATTR = "_active_a2a_delegate_session"
_ACTIVE_A2A_SESSION_LOCK_ATTR = "_active_a2a_delegate_session_lock"
A2A_REGISTRY: dict[str, dict[str, Any]] = {}
A2A_CONTEXT = ""


def _json_result(**payload) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _xml_text(value: Any) -> str:
    return escape(str(value if value is not None else ""), {"'": "&apos;", '"': "&quot;"})


def _a2a_registry_path() -> Path:
    return Path(get_hermes_home()) / "a2a.json"


def _normalize_a2a_base_url(value: Any) -> str:
    text = str(value or "").strip()
    return text.rstrip("/")


def _normalize_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str) and item]


def _normalize_a2a_headers(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): str(item)
        for key, item in value.items()
        if isinstance(key, str) and key.strip() and item is not None
    }


def _fetch_agent_card(base_url: str, *, headers: dict[str, str] | None = None) -> tuple[dict[str, Any] | None, str | None]:
    card_url = base_url.rstrip("/") + "/.well-known/agent-card.json"
    try:
        response = httpx.get(card_url, timeout=5.0, headers=headers or None)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        return None, str(exc)
    if not isinstance(payload, dict):
        return None, "agent card must be a JSON object"
    return payload, None


def _normalize_a2a_registry_entry(name: Any, raw_entry: Any) -> dict[str, Any]:
    entry_name = str(name)
    if isinstance(raw_entry, str):
        return {
            "name": entry_name,
            "url": _normalize_a2a_base_url(raw_entry),
            "status": None,
            "headers": {},
            "extcapabilities": [],
        }
    if not isinstance(raw_entry, dict):
        raise ValueError(f"A2A agent {entry_name!r} must be a URL string or object")
    return {
        "name": entry_name,
        "url": _normalize_a2a_base_url(raw_entry.get("url", "")),
        "description": raw_entry.get("description") if isinstance(raw_entry.get("description"), str) else None,
        "status": str(raw_entry.get("status") or "").strip().lower() or None,
        "headers": _normalize_a2a_headers(raw_entry.get("headers")),
        "extcapabilities": _normalize_string_list(raw_entry.get("extcapabilities")),
    }


def _extract_a2a_skill_strings(card_json: dict[str, Any] | None) -> list[str]:
    if not isinstance(card_json, dict):
        return []
    extracted: list[str] = []
    for skill in card_json.get("skills", []):
        if not isinstance(skill, dict):
            continue
        parts: list[str] = []
        for field in ("id", "name", "description"):
            value = skill.get(field)
            if isinstance(value, str) and value.strip():
                parts.append(f"{field}={value.strip()}")
        tags = skill.get("tags")
        if isinstance(tags, list):
            cleaned = [str(tag).strip() for tag in tags if str(tag).strip()]
            if cleaned:
                parts.append(f"tags={','.join(cleaned)}")
        examples = skill.get("examples")
        if isinstance(examples, list):
            cleaned = [str(example).strip() for example in examples if str(example).strip()]
            if cleaned:
                parts.append(f"examples={'; '.join(cleaned)}")
        if parts:
            extracted.append(" | ".join(parts))
    return list(dict.fromkeys(extracted))


def _summarize_agent_card(card_json: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(card_json, dict):
        return None
    skills: list[str] = []
    for skill in card_json.get("skills", []):
        if isinstance(skill, dict):
            name = skill.get("name") or skill.get("id")
            if isinstance(name, str) and name:
                skills.append(name)
    raw_extensions = card_json.get("extensions") or card_json.get("supported_extensions") or []
    extensions = [item for item in raw_extensions if isinstance(item, dict)] if isinstance(raw_extensions, list) else []
    interaction_extension = next(
        (
            item
            for item in extensions
            if str(item.get("uri") or "") == "https://hermes.dev/extensions/interaction/v1"
        ),
        None,
    )
    return {
        "name": card_json.get("name"),
        "description": card_json.get("description"),
        "version": card_json.get("version"),
        "skills": skills,
        "supported_interfaces": card_json.get("supported_interfaces", card_json.get("supportedInterfaces", [])),
        "extensions": extensions,
        "interaction_extension": interaction_extension,
    }


def _active_global_routing_rules(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []

    rules: list[dict[str, str]] = []
    for raw_rule in value:
        if not isinstance(raw_rule, dict):
            continue
        if str(raw_rule.get("status") or "").strip().lower() != "active":
            continue
        rule_id = raw_rule.get("id")
        name = raw_rule.get("name")
        policy = raw_rule.get("policy")
        if not all(isinstance(field, str) for field in (rule_id, name, policy)):
            continue
        rules.append(
            {
                "id": rule_id,
                "name": name,
                "policy": policy,
                "status": "active",
            }
        )
    return rules


def _refresh_a2a_registry() -> tuple[list[dict[str, Any]], list[dict[str, str]], str | None]:
    path = _a2a_registry_path()
    if not path.exists():
        A2A_REGISTRY.clear()
        return [], [], f"A2A registry not found: {path}"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        A2A_REGISTRY.clear()
        return [], [], f"Could not read A2A registry {path}: {exc}"
    raw_agents = payload.get("a2a", payload) if isinstance(payload, dict) else {}
    if not isinstance(raw_agents, dict):
        A2A_REGISTRY.clear()
        return [], [], "A2A registry must contain an object of agents"
    global_routing = _active_global_routing_rules(payload.get("global", [])) if isinstance(payload, dict) else []

    entries: list[dict[str, Any]] = []
    A2A_REGISTRY.clear()
    for name, raw_entry in raw_agents.items():
        try:
            entry = _normalize_a2a_registry_entry(name, raw_entry)
        except ValueError as exc:
            entries.append({"name": str(name), "available": False, "error": str(exc)})
            continue
        if entry.get("status") != "active":
            continue
        if not entry.get("url"):
            entry.update({"available": False, "error": "missing url"})
            entries.append(entry)
            continue
        card, error = _fetch_agent_card(entry["url"], headers=entry.get("headers") or None)
        capabilities = _extract_a2a_skill_strings(card) + list(entry.get("extcapabilities") or [])
        entry.update(
            {
                "available": error is None,
                "agent_card": _summarize_agent_card(card),
                "capabilities": list(dict.fromkeys(capabilities)),
                "error": error,
            }
        )
        A2A_REGISTRY[str(name)] = entry
        entries.append(entry)
    return entries, global_routing, None


def _build_aegis_xml(entries: list[dict[str, Any]], global_routing: list[dict[str, str]]) -> str:
    lines = ["<aegis_context>", "  <agents>"]
    for entry in entries:
        attributes: list[str] = []
        for field in ("name", "url", "status"):
            value = entry.get(field)
            if value is not None:
                attributes.append(f'{field}="{_xml_text(value)}"')
        if "available" in entry:
            available = "true" if entry["available"] else "false"
            attributes.append(f'available="{available}"')
        attribute_text = f" {' '.join(attributes)}" if attributes else ""
        lines.append(f"    <agent{attribute_text}>")

        for field in ("description", "error"):
            value = entry.get(field)
            if value is not None:
                lines.append(f"      <{field}>{_xml_text(value)}</{field}>")

        if "capabilities" in entry:
            capabilities = entry["capabilities"]
            lines.append("      <capabilities>")
            if isinstance(capabilities, list):
                for capability in capabilities:
                    lines.append(f"        <capability>{_xml_text(capability)}</capability>")
            lines.append("      </capabilities>")
        lines.append("    </agent>")
    lines.extend(["  </agents>", "  <global_routing>"])
    for rule in global_routing:
        lines.append(
            f"    <rule id=\"{_xml_text(rule['id'])}\" status=\"{_xml_text(rule['status'])}\">"
        )
        lines.append(f"      <name>{_xml_text(rule['name'])}</name>")
        lines.append(f"      <policy>{_xml_text(rule['policy'])}</policy>")
        lines.append("    </rule>")
    lines.extend(["  </global_routing>", "</aegis_context>"])
    return "\n".join(lines)


def a2a_list(otype: str = "xml") -> str:
    global A2A_CONTEXT
    if otype not in {"json", "xml"}:
        return tool_error("otype must be 'json' or 'xml'")

    entries, global_routing, error = _refresh_a2a_registry()
    xml = _build_aegis_xml(entries, global_routing)
    if error is None:
        A2A_CONTEXT = xml
    if otype == "xml":
        return xml

    return _json_result(
        success=error is None,
        error=error,
        global_routing=global_routing,
        agents=[
            {
                key: value
                for key, value in entry.items()
                if key not in {"headers", "extcapabilities","agent_card"}
            }
            for entry in entries
        ],
    )


def _emit(output, event_type: str, content: Any, session_id: str | None) -> None:
    if output is None:
        return
    emit = getattr(output, "emit", None)
    if callable(emit):
        emit("delegate", event_type, content, session_id=session_id)


def _run_coro_sync(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result: dict[str, Any] = {}
    error: dict[str, BaseException] = {}

    def _runner() -> None:
        try:
            result["value"] = asyncio.run(coro)
        except BaseException as exc:  # pragma: no cover - passthrough guard
            error["exc"] = exc

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()
    if "exc" in error:
        raise error["exc"]
    return result.get("value")


def _default_session_id(kind: str) -> str:
    try:
        profile = get_active_profile_name()
    except Exception:
        profile = "default"
    suffix = random.randint(1, 99)
    return f"delegate_{profile}_{kind}_{time.strftime('%Y%m%d_%H%M%S')}_{suffix:02d}"


def _normalize_delegate_session_id(value: Any) -> str | None:
    if value is None:
        return None
    session_id = str(value).strip()
    return session_id or None


def _resolve_delegate_session_id(session_id: Any = None) -> str:
    return _normalize_delegate_session_id(session_id) or _default_session_id("a2a")


def _read_delegate_input(input_adapter, timeout: float | None = None):
    if input_adapter is None:
        return None
    read_line = getattr(input_adapter, "read_line", None)
    if not callable(read_line):
        return None
    if timeout is None:
        line = read_line()
    else:
        try:
            line = read_line(timeout=timeout)
        except TypeError as exc:
            if "timeout" not in str(exc):
                raise
            line = read_line()
    if line is None and _delegate_input_timed_out(input_adapter):
        return _DELEGATE_INPUT_TIMEOUT
    return line


def _delegate_input_timed_out(input_adapter) -> bool:
    timed_out = getattr(input_adapter, "last_read_timed_out", None)
    if callable(timed_out):
        try:
            return bool(timed_out())
        except Exception:
            return False
    return bool(getattr(input_adapter, "_last_read_timed_out", False))


def _enter_delegate_foreground(input_adapter) -> bool:
    enter = getattr(input_adapter, "enter_foreground", None)
    if callable(enter):
        return bool(enter())
    return True


def _exit_delegate_foreground(input_adapter) -> None:
    exit_foreground = getattr(input_adapter, "exit_foreground", None)
    if callable(exit_foreground):
        exit_foreground()


def _touch_parent_activity_for_delegate_input(parent_agent) -> None:
    touch = getattr(parent_agent, "_touch_activity", None)
    if not callable(touch):
        return
    try:
        touch("a2a_delegate: received foreground input")
    except Exception:
        pass


def _format_aegis_source_header(parent_agent) -> str:
    raw_user_id = getattr(parent_agent, "_user_id", "")
    raw_user_name = getattr(parent_agent, "_user_name", "")
    raw_platform = getattr(parent_agent, "platform", "")
    user_id = raw_user_id.strip() if isinstance(raw_user_id, str) else ""
    user_name = raw_user_name.strip() if isinstance(raw_user_name, str) else ""
    platform = raw_platform.strip() if isinstance(raw_platform, str) else ""
    if not user_id and not user_name:
        return ""
    source_data = {"platform": platform, "conn": "a2a"}
    if user_id:
        source_data["uid"] = user_id
    if user_name:
        source_data["uname"] = user_name
    source_payload = json.dumps(source_data, ensure_ascii=False, separators=(",", ":"))
    return f"<source>{source_payload}</source>"


def _decorate_a2a_user_message(text: str, parent_agent, *, context: str | None = None) -> str:
    parts: list[str] = []
    source_header = _format_aegis_source_header(parent_agent)
    if source_header:
        parts.append(source_header)
    if context:
        parts.append(f"<context>{context}</context>")
    parts.append(str(text or ""))
    return "\n\n".join(parts)


def _a2a_field(value, name: str, default=None):
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _a2a_message_text(message) -> str:
    parts = list(_a2a_field(message, "parts", []) or [])
    chunks = [getattr(part, "text", "") for part in parts if getattr(part, "text", "")]
    if chunks:
        return "".join(chunks)
    content = _a2a_field(message, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_chunks: list[str] = []
        for item in content:
            if isinstance(item, str):
                text_chunks.append(item)
            elif isinstance(item, dict) and item.get("type") == "text":
                text_chunks.append(str(item.get("text", "")))
        return "".join(text_chunks)
    if content is None:
        return ""
    if isinstance(content, (dict, list)):
        return json.dumps(content, ensure_ascii=True, separators=(",", ":"))
    return str(content)


def _a2a_message_metadata(message) -> dict[str, Any]:
    metadata = _a2a_field(message, "metadata", None)
    if metadata is None:
        return {}
    if isinstance(metadata, dict):
        return metadata
    try:
        from google.protobuf.json_format import MessageToDict
        return MessageToDict(metadata)
    except Exception:
        return {}


def _a2a_hermes_metadata(message) -> dict[str, Any]:
    hermes = _a2a_message_metadata(message).get("hermes")
    return hermes if isinstance(hermes, dict) else {}


def _a2a_is_tool_message(message) -> bool:
    if message is None:
        return False
    from a2a.types import Role

    hermes = _a2a_hermes_metadata(message)
    if hermes.get("kind") == "tool_result":
        return True
    role = _a2a_field(message, "role", None)
    return role in ("tool", "ROLE_TOOL", getattr(Role, "ROLE_TOOL", object()))


def _a2a_message_tool_calls(message) -> list[dict[str, Any]]:
    tool_calls = list(_a2a_field(message, "tool_calls", []) or [])
    if tool_calls:
        return tool_calls
    hermes = _a2a_hermes_metadata(message)
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


def _compact_json_text(value: Any) -> str:
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


def _a2a_tool_call_details(tool_call) -> tuple[str, str, str]:
    call_id = str(_a2a_field(tool_call, "id", "") or "")
    function = _a2a_field(tool_call, "function", {}) or {}
    tool_name = str(_a2a_field(function, "name", "tool") or "tool")
    arguments = _compact_json_text(_a2a_field(function, "arguments", ""))
    return call_id, tool_name, arguments


def _a2a_is_agent_message(message) -> bool:
    if message is None:
        return False
    from a2a.types import Role

    hermes = _a2a_hermes_metadata(message)
    if hermes.get("kind") in {"tool_call", "tool_result"}:
        return False
    role = _a2a_field(message, "role", None)
    return role in (None, Role.ROLE_AGENT, "assistant", "agent", "ROLE_AGENT")


def _a2a_assistant_message_text(message) -> str:
    if not _a2a_is_agent_message(message):
        return ""
    return _a2a_message_text(message)


def _a2a_task_text(task) -> str:
    status = getattr(task, "status", None)
    text = _a2a_assistant_message_text(_a2a_field(status, "message", None))
    if text:
        return text
    for message in reversed(list(_a2a_field(task, "history", []) or [])):
        text = _a2a_assistant_message_text(message)
        if text:
            return text
    return ""


def _a2a_task_messages(task) -> list[Any]:
    messages = list(_a2a_field(task, "history", []) or [])
    status = _a2a_field(task, "status", None)
    status_message = _a2a_field(status, "message", None)
    if status_message is not None:
        messages.append(status_message)
    return messages


def _a2a_event_has_field(event, name: str) -> bool:
    has_field = getattr(event, "HasField", None)
    if callable(has_field):
        try:
            return bool(has_field(name))
        except Exception:
            pass
    return getattr(event, name, None) is not None


def _a2a_event_messages(event) -> list[Any]:
    if _a2a_event_has_field(event, "task"):
        return _a2a_task_messages(event.task)
    if _a2a_event_has_field(event, "message"):
        return [event.message]
    if _a2a_event_has_field(event, "status_update"):
        status = _a2a_field(event.status_update, "status", None)
        message = _a2a_field(status, "message", None)
        return [message] if message is not None else []
    return []


def _a2a_event_to_task(event):
    if _a2a_event_has_field(event, "task"):
        return event.task
    if _a2a_event_has_field(event, "message"):
        return SimpleNamespace(
            id=getattr(event.message, "task_id", ""),
            context_id=getattr(event.message, "context_id", ""),
            status=SimpleNamespace(state="completed", message=event.message),
            history=[event.message],
        )
    if _a2a_event_has_field(event, "status_update"):
        status = event.status_update.status
        return SimpleNamespace(
            id=event.status_update.task_id,
            context_id=event.status_update.context_id,
            status=SimpleNamespace(
                state=getattr(status, "state", None),
                message=getattr(status, "message", None),
            ),
            history=[],
        )
    return None


def _a2a_final_task_states() -> set[Any]:
    from a2a.types import TaskState

    return {
        TaskState.TASK_STATE_COMPLETED,
        TaskState.TASK_STATE_FAILED,
        TaskState.TASK_STATE_CANCELED,
        TaskState.TASK_STATE_INPUT_REQUIRED,
        TaskState.TASK_STATE_REJECTED,
        TaskState.TASK_STATE_AUTH_REQUIRED,
        "completed",
        "failed",
        "canceled",
        "cancelled",
        "input_required",
        "rejected",
        "auth_required",
    }


def _a2a_completed_state() -> Any:
    from a2a.types import TaskState

    return TaskState.TASK_STATE_COMPLETED


def _a2a_input_required_state() -> Any:
    from a2a.types import TaskState

    return TaskState.TASK_STATE_INPUT_REQUIRED


def _a2a_canceled_state() -> Any:
    from a2a.types import TaskState

    return TaskState.TASK_STATE_CANCELED


def _a2a_state_name(state: Any) -> str:
    from a2a.types import TaskState

    if isinstance(state, str):
        return state.lower()
    try:
        return TaskState.Name(state).replace("TASK_STATE_", "").lower()
    except Exception:
        return str(state)


def _a2a_sdk_available() -> bool:
    try:
        import a2a.client  # noqa: F401
        import a2a.types  # noqa: F401
    except Exception:
        return False
    return True


def _state_equals(state: Any, expected_factory) -> bool:
    if isinstance(state, str):
        factory_name = getattr(expected_factory, "__name__", "")
        expected_name = {
            "_a2a_completed_state": "completed",
            "_a2a_input_required_state": "input_required",
            "_a2a_canceled_state": "canceled",
        }.get(factory_name, "")
        normalized = state.lower()
        if expected_name == "canceled" and normalized == "cancelled":
            return True
        return normalized == expected_name
    return state == expected_factory()


def _is_benign_a2a_stop_exception(exc: BaseException) -> bool:
    message = str(exc or "").strip().lower()
    if not message:
        return False
    benign_markers = (
        "event loop is closed",
        "attached to a different loop",
        "bound to a different event loop",
        "different event loop",
        "non-thread-safe operation invoked on an event loop",
        "event loop stopped before future completed",
    )
    return any(marker in message for marker in benign_markers)


def _build_a2a_stop_final_response(last_text: str | None) -> str:
    cleaned = str(last_text or "").strip()
    if not cleaned:
        return _A2A_STOP_MESSAGE
    return f"{_A2A_STOP_MESSAGE}\n\n停止前最后输出：{cleaned}"


def _read_a2a_poll_setting(env_name: str, default: float) -> float:
    raw_value = os.getenv(env_name)
    if raw_value is None or not raw_value.strip():
        return default
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        logger.warning("Ignoring invalid %s=%r; using %.3f", env_name, raw_value, default)
        return default
    if not math.isfinite(value) or value < 0:
        logger.warning("Ignoring invalid %s=%r; using %.3f", env_name, raw_value, default)
        return default
    return value


class _A2ADelegateSession:
    def __init__(
        self,
        base_url: str,
        *,
        output=None,
        parent_agent=None,
        timeout: float | None = None,
        poll_interval: float | None = None,
        session_id: str | None = None,
        headers: dict[str, str] | None = None,
        response_path: str | None = None,
        interaction_supported: bool = False,
    ):
        self.base_url = base_url
        self.output = output
        self.parent_agent = parent_agent
        self.timeout = (
            float(timeout)
            if timeout is not None
            else _read_a2a_poll_setting(_A2A_POLL_TIMEOUT_ENV, _A2A_POLL_TIMEOUT_DEFAULT)
        )
        self.poll_interval = (
            float(poll_interval)
            if poll_interval is not None
            else _read_a2a_poll_setting(_A2A_POLL_INTERVAL_ENV, _A2A_POLL_INTERVAL_DEFAULT)
        )
        self.context_id = session_id
        self.task_id: str | None = None
        self.headers = headers or {}
        self._response_path = str(response_path or "").strip()
        self._interaction_supported = bool(interaction_supported)
        self._client = None
        self._http_client = None
        self._rendered_tool_entries: set[str] = set()
        self._rendered_interactions: set[tuple[str, str]] = set()
        self._pending_interactions: set[str] = set()
        self._tool_names_by_call_id: dict[str, str] = {}
        self._streamed_assistant_text = ""
        self._last_assistant_text = ""
        self._state_lock = threading.RLock()
        self._owner_loop = None
        self._owner_thread_id = None
        self._stop_requested = False

    def _bind_owner_runtime(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        with self._state_lock:
            if self._owner_loop is None:
                self._owner_loop = loop
            if self._owner_thread_id is None:
                self._owner_thread_id = threading.get_ident()

    def _set_last_assistant_text(self, text: str) -> None:
        with self._state_lock:
            self._last_assistant_text = text

    def owner_runtime(self):
        with self._state_lock:
            return self._owner_loop, self._owner_thread_id

    def mark_stop_requested(self) -> None:
        with self._state_lock:
            self._stop_requested = True

    def stop_requested(self) -> bool:
        with self._state_lock:
            return self._stop_requested

    def latest_assistant_text(self) -> str:
        with self._state_lock:
            return self._last_assistant_text

    def bind_output_adapter(self) -> None:
        binder = getattr(self.output, "bind_a2a_interaction_session", None)
        if callable(binder):
            binder(self)

    def unbind_output_adapter(self) -> None:
        unbinder = getattr(self.output, "unbind_a2a_interaction_session", None)
        if callable(unbinder):
            unbinder(self)

    def schedule_interaction_response(
        self,
        interaction_id: str,
        kind: str,
        value: Any,
    ) -> bool:
        """Schedule a remote response without blocking the caller's loop."""
        with self._state_lock:
            loop = self._owner_loop
            owner_thread_id = self._owner_thread_id
        if loop is None or not loop.is_running():
            return False
        coroutine = self.respond_interaction(interaction_id, kind, value)
        if owner_thread_id == threading.get_ident():
            loop.create_task(coroutine)
            return True
        future = asyncio.run_coroutine_threadsafe(coroutine, loop)

        def _log_failure(done) -> None:
            try:
                done.result()
            except Exception:
                logger.warning("A2A interaction response failed", exc_info=True)

        future.add_done_callback(_log_failure)
        return True

    def should_suppress_stop_exception(self, exc: BaseException) -> bool:
        return self.stop_requested() and _is_benign_a2a_stop_exception(exc)

    def _snapshot_remote_ids(self) -> tuple[str | None, str | None]:
        with self._state_lock:
            return self.task_id, self.context_id

    def _set_remote_ids(self, *, task_id: str | None, context_id: str | None) -> None:
        with self._state_lock:
            self.task_id = task_id or self.task_id
            self.context_id = context_id or self.context_id

    def _has_pending_interaction(self) -> bool:
        with self._state_lock:
            return bool(self._pending_interactions)

    def _update_interaction_wait_state(self, kind: str, interaction_id: str) -> None:
        with self._state_lock:
            if kind.endswith("_request"):
                self._pending_interactions.add(interaction_id)
            elif kind.endswith("_resolved"):
                self._pending_interactions.discard(interaction_id)

    def _clear_pending_interactions(self) -> None:
        with self._state_lock:
            self._pending_interactions.clear()

    def _build_http_client(self):
        return httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=None, write=60.0, pool=60.0),
            headers=self.headers or None,
        )

    async def _create_remote_client(self, http_client):
        from a2a.client import ClientConfig, ClientFactory

        factory = ClientFactory(
            ClientConfig(
                httpx_client=http_client,
                streaming=True,
                polling=True,
            )
        )
        return await factory.create_from_url(self.base_url)

    async def open(self) -> None:
        self._bind_owner_runtime()
        if self._client is not None:
            return
        if not _a2a_sdk_available():
            raise RuntimeError("A2A SDK is not installed; install the configured a2a SDK package to use remote delegation")
        self._http_client = self._build_http_client()
        self._client = await self._create_remote_client(self._http_client)

    async def cancel(self, *, use_existing_client: bool = True):
        from a2a.types import CancelTaskRequest

        self.mark_stop_requested()
        if use_existing_client:
            self._bind_owner_runtime()
            await self.open()
            client = self._client
            http_client = None
        else:
            http_client = self._build_http_client()
            client = await self._create_remote_client(http_client)

        task_id, _context_id = self._snapshot_remote_ids()
        if not task_id:
            if http_client is not None:
                try:
                    await client.close()
                except Exception:
                    logger.debug("Failed to close temporary A2A client", exc_info=True)
                try:
                    await http_client.aclose()
                except Exception:
                    logger.debug("Failed to close temporary A2A HTTP client", exc_info=True)
            raise RuntimeError("No active remote A2A task to cancel.")
        try:
            task = await client.cancel_task(CancelTaskRequest(id=task_id))
        finally:
            if http_client is not None:
                try:
                    await client.close()
                except Exception:
                    logger.debug("Failed to close temporary A2A client", exc_info=True)
                try:
                    await http_client.aclose()
                except Exception:
                    logger.debug("Failed to close temporary A2A HTTP client", exc_info=True)

        self._set_remote_ids(
            task_id=getattr(task, "id", None),
            context_id=getattr(task, "context_id", None),
        )
        self._emit_tool_messages(
            _a2a_task_messages(task),
            session_id=getattr(task, "context_id", None) or self.context_id,
        )
        self._emit_text_deltas(
            task,
            session_id=getattr(task, "context_id", None) or self.context_id,
        )
        return task

    async def send_turn(self, text: str, *, is_delegate_output: bool = True) -> dict[str, Any]:
        await self.open()
        self._rendered_tool_entries.clear()
        self._tool_names_by_call_id.clear()
        self._streamed_assistant_text = ""
        task = await self._send_text(text)
        self.context_id = getattr(task, "context_id", None) or self.context_id
        self.task_id = getattr(task, "id", None) or self.task_id
        finished = await self._wait_for_final(task)
        self.context_id = getattr(finished, "context_id", None) or self.context_id
        self.task_id = getattr(finished, "id", None) or self.task_id
        final_response = _a2a_task_text(finished)
        if final_response:
            self._set_last_assistant_text(final_response)
        if is_delegate_output and final_response:
            _emit(self.output, "ai", final_response, self.context_id)
        state = _a2a_field(getattr(finished, "status", None), "state", None)
        return {
            "task": finished,
            "final_response": final_response,
            "state": state,
            "state_name": _a2a_state_name(state),
            "context_id": self.context_id,
            "task_id": self.task_id,
        }

    def _interaction_url(self) -> str:
        response_path = self._response_path or "/hermes/interaction/respond"
        if response_path.startswith("http://") or response_path.startswith("https://"):
            return response_path
        base = self.base_url.rstrip("/")
        base_parts = urlsplit(base)
        base_path = base_parts.path.rstrip("/")
        if response_path.startswith("/"):
            if base_path and response_path.startswith(base_path + "/"):
                return f"{base_parts.scheme}://{base_parts.netloc}{response_path}"
            return f"{base_parts.scheme}://{base_parts.netloc}{response_path}"
        return f"{base}/{response_path.lstrip('/')}"

    async def respond_interaction(self, interaction_id: str, kind: str, value: Any) -> dict[str, Any]:
        if not self._interaction_supported:
            raise RuntimeError("remote A2A agent does not declare Hermes interaction support")
        await self.open()
        task_id, context_id = self._snapshot_remote_ids()
        if not task_id or not context_id:
            raise RuntimeError("remote A2A task is not available for interaction response")
        payload: dict[str, Any] = {
            "task_id": task_id,
            "context_id": context_id,
            "interaction_id": str(interaction_id),
            "kind": str(kind),
        }
        if kind == "approval":
            payload["choice"] = str(value)
        else:
            if isinstance(value, list):
                payload["answer"] = [str(item) for item in value]
            else:
                payload["answer"] = str(value)
        response = await self._http_client.post(self._interaction_url(), json=payload)
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail")
            except Exception:
                detail = response.text
            raise RuntimeError(f"A2A interaction response failed ({response.status_code}): {detail}")
        result = response.json()
        return result if isinstance(result, dict) else {"ok": True}

    async def _send_text(self, text: str):
        from a2a.types import Message, Part, Role, SendMessageConfiguration, SendMessageRequest

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
        last_task = None
        got_response = False
        async for event in self._client.send_message(request):
            got_response = True
            self._emit_tool_messages(
                _a2a_event_messages(event),
                session_id=getattr(last_task, "context_id", None) or self.context_id,
            )
            last_task = _a2a_event_to_task(event) or last_task
            if last_task is not None:
                if not self.context_id:
                    self.context_id = getattr(last_task, "context_id", None) or self.context_id
                self.task_id = getattr(last_task, "id", None) or self.task_id
                state = _a2a_field(getattr(last_task, "status", None), "state", None)
                is_final = state in _a2a_final_task_states()
                self._emit_task_text_delta(
                    last_task,
                    session_id=getattr(last_task, "context_id", None) or self.context_id,
                    is_final=is_final,
                )
                if is_final:
                    return last_task
        if not got_response:
            raise RuntimeError("A2A SDK returned no response events.")
        if last_task is None:
            raise RuntimeError("Unexpected A2A response without task or message.")
        return last_task

    async def _wait_for_final(self, task):
        from a2a.types import GetTaskRequest

        current_task = task
        deadline = time.monotonic() + self.timeout
        interaction_paused_at: float | None = None
        while True:
            self._emit_tool_messages(
                _a2a_task_messages(current_task),
                session_id=getattr(current_task, "context_id", None) or self.context_id,
            )
            now = time.monotonic()
            if self._has_pending_interaction():
                # The user-controlled interaction may outlive the ordinary
                # task polling deadline; pause that deadline until a matching
                # resolved event arrives.
                if interaction_paused_at is None:
                    interaction_paused_at = now
            elif interaction_paused_at is not None:
                deadline += now - interaction_paused_at
                interaction_paused_at = None
            state = _a2a_field(getattr(current_task, "status", None), "state", None)
            is_final = state in _a2a_final_task_states()
            self._emit_task_text_delta(
                current_task,
                session_id=getattr(current_task, "context_id", None) or self.context_id,
                is_final=is_final,
            )
            if is_final:
                self._clear_pending_interactions()
                return current_task
            if interaction_paused_at is None and now >= deadline:
                raise TimeoutError(f"Timed out waiting for task {getattr(current_task, 'id', None)!r}.")
            await asyncio.sleep(self.poll_interval)
            current_task = await self._client.get_task(GetTaskRequest(id=current_task.id))
            self._touch_parent_activity_after_poll()

    def _touch_parent_activity_after_poll(self) -> None:
        """Record a successful remote task poll without affecting delegation."""
        touch = getattr(self.parent_agent, "_touch_activity", None)
        if not callable(touch):
            return
        try:
            touch("a2a_delegate: received remote task update")
        except Exception:
            logger.debug("Failed to record A2A remote task polling activity", exc_info=True)

    def _emit_task_text_delta(self, task, *, session_id: str | None, is_final: bool) -> None:
        if not is_final:
            self._emit_text_deltas(task, session_id=session_id)
            return

        final_text = _a2a_task_text(task)
        if not self._streamed_assistant_text or final_text.startswith(self._streamed_assistant_text):
            self._emit_text_deltas(task, session_id=session_id)
        elif final_text:
            self._set_last_assistant_text(final_text)

    def _emit_tool_messages(self, messages: list[Any], *, session_id: str | None) -> None:
        for message in messages:
            if message is None:
                continue
            self._emit_interaction_message(message, session_id=session_id)
            for tool_call in _a2a_message_tool_calls(message):
                call_id, tool_name, arguments = _a2a_tool_call_details(tool_call)
                if call_id:
                    self._tool_names_by_call_id[call_id] = tool_name
                key = f"assistant-tool:{call_id}:{tool_name}:{arguments}"
                if key in self._rendered_tool_entries:
                    continue
                self._rendered_tool_entries.add(key)
                content = f"{tool_name} {arguments}" if arguments else tool_name
                _emit(self.output, "tool_call", content, session_id)

            if _a2a_is_tool_message(message):
                continue

    def _emit_interaction_message(self, message, *, session_id: str | None) -> None:
        metadata = _a2a_hermes_metadata(message)
        kind = str(metadata.get("kind") or "")
        if kind not in {
            "approval_request",
            "clarify_request",
            "approval_resolved",
            "clarify_resolved",
        }:
            return
        if kind.endswith("_request") and not self._interaction_supported:
            # A legacy remote card has no authenticated response channel.
            # Do not manufacture UI controls that can never unblock the
            # remote worker; its existing fail-closed behavior remains intact.
            return
        interaction_id = str(metadata.get("interaction_id") or "")
        if not interaction_id:
            return
        event_type = kind
        key = (interaction_id, event_type)
        if key in self._rendered_interactions:
            return
        self._rendered_interactions.add(key)
        payload = dict(metadata)
        payload.setdefault("task_id", self.task_id)
        payload.setdefault("context_id", self.context_id)
        self._update_interaction_wait_state(kind, interaction_id)
        _emit(self.output, event_type, _compact_json_text(payload), session_id)

    def _emit_text_deltas(self, task, *, session_id: str | None) -> None:
        text = _a2a_task_text(task)
        if not text or text == self._streamed_assistant_text:
            return
        if self._streamed_assistant_text and text.startswith(self._streamed_assistant_text):
            delta = text[len(self._streamed_assistant_text):]
        else:
            delta = text
        self._streamed_assistant_text = text
        self._set_last_assistant_text(text)
        if delta:
            _emit(self.output, "ai_delta", delta, session_id)

    async def close(self) -> None:
        self._bind_owner_runtime()
        if self._client is not None and hasattr(self._client, "close"):
            await self._client.close()
        if self._http_client is not None and hasattr(self._http_client, "aclose"):
            await self._http_client.aclose()
        self._client = None
        self._http_client = None


def _resolve_a2a_entry(agent_name: str) -> dict[str, Any] | None:
    if agent_name in A2A_REGISTRY:
        return A2A_REGISTRY[agent_name]
    _refresh_a2a_registry()
    return A2A_REGISTRY.get(agent_name)


def _resolve_a2a_remote_url(entry: dict[str, Any]) -> str:
    agent_card = entry.get("agent_card")
    if isinstance(agent_card, dict):
        for interface in agent_card.get("supported_interfaces", []):
            if not isinstance(interface, dict):
                continue
            url = interface.get("url")
            if isinstance(url, str) and url:
                return _normalize_a2a_base_url(url)
    return _normalize_a2a_base_url(str(entry.get("url") or ""))


def _a2a_interaction_config(entry: dict[str, Any]) -> tuple[bool, str | None]:
    card = entry.get("agent_card")
    if not isinstance(card, dict):
        return False, None
    extension = card.get("interaction_extension")
    if not isinstance(extension, dict):
        return False, None
    params = extension.get("params") or {}
    response_path = params.get("response_path") if isinstance(params, dict) else None
    return True, str(response_path).strip() if response_path else None


class _RemoteA2ADelegateCancelHandle:
    def __init__(self, session: "_A2ADelegateSession") -> None:
        self._session = session
        self._lock = threading.Lock()

    def has_live_task(self) -> bool:
        return bool(getattr(self._session, "task_id", None))

    def _log_async_cancel_outcome(self, future) -> None:
        try:
            future.result()
        except Exception as exc:
            if self._session.should_suppress_stop_exception(exc):
                logger.debug("Suppressing benign async A2A stop exception", exc_info=True)
                return
            logger.warning("Asynchronous A2A cancel failed after request dispatch: %s", exc, exc_info=True)

    def cancel(self) -> str:
        self._session.mark_stop_requested()
        if not self.has_live_task():
            return _A2A_CANCEL_STATUS_NOOP
        with self._lock:
            if not self.has_live_task():
                return _A2A_CANCEL_STATUS_NOOP
            owner_loop, owner_thread_id = self._session.owner_runtime()
            current_thread_id = threading.get_ident()
            if owner_loop is not None and owner_loop.is_running() and owner_thread_id != current_thread_id:
                future = asyncio.run_coroutine_threadsafe(
                    self._session.cancel(use_existing_client=True),
                    owner_loop,
                )
                if future.done():
                    future.result()
                    return _A2A_CANCEL_STATUS_COMPLETED
                future.add_done_callback(self._log_async_cancel_outcome)
                return _A2A_CANCEL_STATUS_SENT
            _run_coro_sync(self._session.cancel(use_existing_client=False))
            return _A2A_CANCEL_STATUS_COMPLETED


def _parent_active_a2a_session_lock(parent_agent) -> threading.Lock:
    lock = getattr(parent_agent, _ACTIVE_A2A_SESSION_LOCK_ATTR, None)
    if lock is None:
        lock = threading.Lock()
        setattr(parent_agent, _ACTIVE_A2A_SESSION_LOCK_ATTR, lock)
    return lock


def _register_active_a2a_session(parent_agent, session: "_A2ADelegateSession"):
    if parent_agent is None:
        return None
    handle = _RemoteA2ADelegateCancelHandle(session)
    lock = _parent_active_a2a_session_lock(parent_agent)
    with lock:
        setattr(parent_agent, _ACTIVE_A2A_SESSION_ATTR, handle)
    return handle


def _clear_active_a2a_session(parent_agent, handle) -> None:
    if parent_agent is None or handle is None:
        return
    lock = _parent_active_a2a_session_lock(parent_agent)
    with lock:
        if getattr(parent_agent, _ACTIVE_A2A_SESSION_ATTR, None) is handle:
            setattr(parent_agent, _ACTIVE_A2A_SESSION_ATTR, None)


def _build_a2a_payload(
    *,
    success: bool,
    goal: str,
    agent_name: str,
    session: _A2ADelegateSession | None,
    is_loop: bool,
    loop_exit_reason: str,
    duration_seconds: float,
    final_response: str,
    completed: bool,
    error_message: str | None = None,
) -> dict[str, Any]:
    payload = {
        "success": success,
        "type": "a2a",
        "agent_name": agent_name,
        "goal": goal,
        "session_id": getattr(session, "context_id", None),
        "is_loop": bool(is_loop),
        "completed": completed,
        "loop_exit_reason": loop_exit_reason,
        "api_calls": 0,
        "duration_seconds": round(duration_seconds, 3),
        "final_response": final_response,
    }
    if error_message:
        payload["error"] = error_message
    return payload


def _build_interrupted_a2a_payload(
    *,
    goal: str,
    agent_name: str,
    session: _A2ADelegateSession | None,
    is_loop: bool,
    duration_seconds: float,
    last_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    last_text = ""
    if isinstance(last_result, dict):
        last_text = str(last_result.get("final_response") or "")
    if not last_text and session is not None:
        latest_text = getattr(session, "latest_assistant_text", None)
        if callable(latest_text):
            try:
                last_text = str(latest_text() or "")
            except Exception:
                last_text = ""
    return _build_a2a_payload(
        success=True,
        goal=goal,
        agent_name=agent_name,
        session=session,
        is_loop=is_loop,
        loop_exit_reason="interrupted",
        duration_seconds=duration_seconds,
        final_response=_build_a2a_stop_final_response(last_text),
        completed=False,
    )


def _run_remote_delegate(
    *,
    goal: str,
    context: str | None,
    agent_name: str | None,
    session_id: str | None,
    is_delegate_output: bool,
    is_loop: bool,
    input=None,
    output=None,
    parent_agent=None,
) -> dict[str, Any]:
    start = time.monotonic()
    if not agent_name:
        return {"success": False, "type": "a2a", "error": "agent_name is required for A2A delegation"}
    if is_loop and input is None:
        return {
            "success": False,
            "type": "a2a",
            "agent_name": agent_name,
            "error": "a2a_delegate loop mode requires an input adapter.",
        }
    entry = _resolve_a2a_entry(agent_name)
    if not entry:
        return {"success": False, "type": "a2a", "agent_name": agent_name, "error": f"Unknown A2A agent: {agent_name}"}
    if entry.get("error"):
        return {"success": False, "type": "a2a", "agent_name": agent_name, "error": entry.get("error")}
    if not _a2a_sdk_available():
        return {
            "success": False,
            "type": "a2a",
            "agent_name": agent_name,
            "error": "A2A SDK is not installed. Install the configured a2a SDK package to use remote delegation.",
        }

    # When output is disabled for a non-looping delegation, suppress the
    # adapter at the session boundary so every event type (including status,
    # tool calls, deltas, and errors) follows the same policy.
    if not is_delegate_output:
        output = None

    remote_session_id = _resolve_delegate_session_id(session_id)
    interaction_supported, response_path = _a2a_interaction_config(entry)
    session = _A2ADelegateSession(
        _resolve_a2a_remote_url(entry),
        output=output,
        parent_agent=parent_agent,
        session_id=remote_session_id,
        headers=entry.get("headers") or {},
        response_path=response_path,
        interaction_supported=interaction_supported,
    )
    bind_output = getattr(session, "bind_output_adapter", None)
    if callable(bind_output):
        bind_output()
    active_handle = _register_active_a2a_session(parent_agent, session)

    async def _run_loop() -> dict[str, Any]:
        last_result: dict[str, Any] = {"final_response": "", "state": None, "state_name": "idle"}
        entered_foreground = False
        try:
            if is_loop:
                entered_foreground = _enter_delegate_foreground(input)
                if not entered_foreground:
                    return _build_a2a_payload(
                        success=False,
                        goal=goal,
                        agent_name=agent_name,
                        session=session,
                        is_loop=is_loop,
                        loop_exit_reason="error",
                        duration_seconds=time.monotonic() - start,
                        final_response="",
                        completed=False,
                        error_message="a2a_delegate could not enter foreground mode.",
                    )
                _emit(output, "status", "entered foreground loop", getattr(session, "context_id", None))

            last_result = await session.send_turn(
                _decorate_a2a_user_message(goal, parent_agent, context=context),
                is_delegate_output=is_delegate_output,
            )
            if _state_equals(last_result.get("state"), _a2a_canceled_state):
                if is_loop:
                    _emit(output, "status", "interrupted", getattr(session, "context_id", None))
                return _build_interrupted_a2a_payload(
                    goal=goal,
                    agent_name=agent_name,
                    session=session,
                    is_loop=is_loop,
                    duration_seconds=time.monotonic() - start,
                    last_result=last_result,
                )
            if not is_loop:
                state = last_result.get("state")
                completed = _state_equals(state, _a2a_completed_state)
                success = completed or _state_equals(state, _a2a_input_required_state)
                error_message = None if success else last_result.get("final_response") or last_result.get("state_name")
                return _build_a2a_payload(
                    success=success,
                    goal=goal,
                    agent_name=agent_name,
                    session=session,
                    is_loop=is_loop,
                    loop_exit_reason="completed" if success else "error",
                    duration_seconds=time.monotonic() - start,
                    final_response=str(last_result.get("final_response") or ""),
                    completed=completed,
                    error_message=error_message,
                )

            while True:
                next_message = await asyncio.to_thread(
                    _read_delegate_input,
                    input,
                    _DELEGATE_FOREGROUND_INPUT_TIMEOUT_SECONDS,
                )
                if next_message is _DELEGATE_INPUT_TIMEOUT:
                    _emit(output, "status", "input timeout", getattr(session, "context_id", None))
                    return _build_a2a_payload(
                        success=True,
                        goal=goal,
                        agent_name=agent_name,
                        session=session,
                        is_loop=is_loop,
                        loop_exit_reason="input_timeout",
                        duration_seconds=time.monotonic() - start,
                        final_response=_A2A_INPUT_TIMEOUT_MESSAGE,
                        completed=_state_equals(last_result.get("state"), _a2a_completed_state),
                    )
                if next_message is None:
                    return _build_a2a_payload(
                        success=True,
                        goal=goal,
                        agent_name=agent_name,
                        session=session,
                        is_loop=is_loop,
                        loop_exit_reason="input_closed",
                        duration_seconds=time.monotonic() - start,
                        final_response=str(last_result.get("final_response") or ""),
                        completed=_state_equals(last_result.get("state"), _a2a_completed_state),
                    )
                stripped = str(next_message).strip()
                if stripped in {"/main", "/exit"}:
                    _emit(output, "status", "return to main", getattr(session, "context_id", None))
                    return _build_a2a_payload(
                        success=True,
                        goal=goal,
                        agent_name=agent_name,
                        session=session,
                        is_loop=is_loop,
                        loop_exit_reason="main_command",
                        duration_seconds=time.monotonic() - start,
                        final_response=str(last_result.get("final_response") or ""),
                        completed=_state_equals(last_result.get("state"), _a2a_completed_state),
                    )

                _touch_parent_activity_for_delegate_input(parent_agent)
                last_result = await session.send_turn(
                    _decorate_a2a_user_message(stripped, parent_agent),
                    is_delegate_output=is_delegate_output,
                )
                if _state_equals(last_result.get("state"), _a2a_canceled_state):
                    _emit(output, "status", "interrupted", getattr(session, "context_id", None))
                    return _build_interrupted_a2a_payload(
                        goal=goal,
                        agent_name=agent_name,
                        session=session,
                        is_loop=is_loop,
                        duration_seconds=time.monotonic() - start,
                        last_result=last_result,
                    )
        except Exception as exc:
            suppress = False
            should_suppress = getattr(session, "should_suppress_stop_exception", None)
            if callable(should_suppress):
                suppress = bool(should_suppress(exc))
            if suppress:
                if is_delegate_output:
                    _emit(output, "status", "interrupted", getattr(session, "context_id", None))
                return _build_interrupted_a2a_payload(
                    goal=goal,
                    agent_name=agent_name,
                    session=session,
                    is_loop=is_loop,
                    duration_seconds=time.monotonic() - start,
                    last_result=last_result,
                )
            if is_delegate_output:
                _emit(output, "error", str(exc), getattr(session, "context_id", None) or remote_session_id)
            return _build_a2a_payload(
                success=False,
                goal=goal,
                agent_name=agent_name,
                session=session,
                is_loop=is_loop,
                loop_exit_reason="error",
                duration_seconds=time.monotonic() - start,
                final_response=str(last_result.get("final_response") or ""),
                completed=False,
                error_message=str(exc),
            )
        finally:
            _clear_active_a2a_session(parent_agent, active_handle)
            if entered_foreground:
                _exit_delegate_foreground(input)
            try:
                await session.close()
            except Exception as exc:
                should_suppress = getattr(session, "should_suppress_stop_exception", None)
                if callable(should_suppress) and should_suppress(exc):
                    logger.debug("Suppressing benign A2A close exception after stop", exc_info=True)
                else:
                    raise
            finally:
                unbind_output = getattr(session, "unbind_output_adapter", None)
                if callable(unbind_output):
                    unbind_output()

    return _run_coro_sync(_run_loop())


def a2a_delegate(
    goal: str,
    context: str | None = None,
    agent_name: str | None = None,
    session_id: str | None = None,
    is_delegate_output: bool = True,
    is_loop: bool = False,
    input=None,
    output=None,
    parent_agent=None,
    **_kwargs,
) -> str:
    if parent_agent is None:
        return tool_error("a2a_delegate requires a parent agent")
    goal_text = str(goal or "").strip()
    if not goal_text:
        return tool_error("a2a_delegate requires a non-empty goal")

    requested_delegate_output = bool(is_delegate_output)
    loop_mode = bool(is_loop)
    effective_delegate_output = loop_mode or requested_delegate_output
    security_enabled = a2a_delegate_aegis.is_aegis_delegate_security_enabled()
    if security_enabled:
        aegis_check = a2a_delegate_aegis.run_aegis_checked_delegate(
            parent_agent=parent_agent,
            goal=goal_text,
            agent_name=agent_name,
            session_id=session_id,
            is_loop=loop_mode,
            is_delegate_output=requested_delegate_output,
        )
        if not aegis_check.allowed:
            return _json_result(**(aegis_check.failure_payload or {}))

    payload = _run_remote_delegate(
        goal=goal_text,
        context=context,
        agent_name=agent_name,
        session_id=session_id,
        is_delegate_output=effective_delegate_output,
        is_loop=loop_mode,
        input=input,
        output=output,
        parent_agent=parent_agent,
    )
    return _json_result(**payload)


A2A_LIST_SCHEMA = {
    "name": "a2a_list",
    "description": "List active A2A agents and global routing rules.",
    "parameters": {"type": "object", "properties": {}},
}


A2A_DELEGATE_SCHEMA = {
    "name": "a2a_delegate",
    "description": "Delegate work to an active remote A2A agent.",
    "parameters": {
        "type": "object",
        "properties": {
            "goal": {"type": "string", "description": "The task goal to delegate."},
            "context": {"type": "string", "description": "Optional context for the delegate."},
            "agent_name": {"type": "string", "description": "Remote A2A agent name."},
            "session_id": {"type": "string", "description": "Optional delegate session/context id."},
            "is_delegate_output": {"type": "boolean", "description": "Whether to forward delegate output for non-looping delegation; loop mode always forwards output. Default: true."},
            "is_loop": {"type": "boolean", "description": "Whether to keep foreground input loop active."},
        },
        "required": ["goal", "agent_name"],
    },
}


registry.register(
    name="a2a_list",
    toolset="a2a",
    schema=A2A_LIST_SCHEMA,
    handler=lambda args, **kw: a2a_list(),
    emoji="A2A",
)

registry.register(
    name="a2a_delegate",
    toolset="a2a",
    schema=A2A_DELEGATE_SCHEMA,
    handler=lambda args, **kw: a2a_delegate(
        goal=args.get("goal", ""),
        context=args.get("context"),
        agent_name=args.get("agent_name"),
        session_id=args.get("session_id"),
        is_delegate_output=args.get("is_delegate_output", True),
        is_loop=args.get("is_loop", False),
        parent_agent=kw.get("agent") or kw.get("parent_agent"),
    ),
    emoji="A2A",
)
