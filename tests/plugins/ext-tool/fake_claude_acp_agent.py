"""Fake claude-agent-acp for the /claude tests: an ACP v1 agent on stdio.

Message shapes mirror what claude-agent-acp 0.93.0 emits (captured live). The
prompt text picks a scripted turn: ``tool``, ``slow``, ``/compact``, ``crash``,
``error``; anything else echoes the current permission mode. Every inbound
message is appended to ``$FAKE_ACP_LOG`` (JSON lines) for assertions.
"""

import json
import os
import sys

LOG = os.environ.get("FAKE_ACP_LOG")
USAGE = {"inputTokens": 10, "outputTokens": 5, "cachedReadTokens": 100, "cachedWriteTokens": 20, "totalTokens": 135}
BASH = {"claudeCode": {"toolName": "Bash"}}
state = {"mode": "default"}


def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def read():
    line = sys.stdin.readline()
    if not line:
        sys.exit(0)
    message = json.loads(line)
    if LOG:
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(message) + "\n")
    return message


def update(sid, **fields):
    send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": sid, "update": fields}})


def chunk(sid, text, message_id):
    update(sid, sessionUpdate="agent_message_chunk", content={"type": "text", "text": text}, messageId=message_id)


def result(rid, payload):
    send({"jsonrpc": "2.0", "id": rid, "result": payload})


def tool_turn(rid, sid):
    update(sid, sessionUpdate="agent_thought_chunk", content={"type": "text", "text": "plan it"})
    chunk(sid, "Checking.", "m1")
    update(sid, sessionUpdate="tool_call", toolCallId="t1", name="Bash", rawInput={}, status="pending",
           title="Terminal", kind="execute", content=[], _meta=BASH)
    update(sid, sessionUpdate="tool_call_update", toolCallId="t1", rawInput={"command": "echo hi"},
           title="echo hi", _meta=BASH)
    send({"jsonrpc": "2.0", "id": 0, "method": "session/request_permission", "params": {
        "sessionId": sid,
        "toolCall": {"toolCallId": "t1", "name": "Bash", "status": "pending", "title": "echo hi",
                     "kind": "execute", "rawInput": {"command": "echo hi"}, "_meta": BASH},
        "options": [
            {"optionId": "allow-once", "name": "Yes", "kind": "allow_once"},
            {"optionId": "allow-with-updates", "name": "Yes, and don't ask again for echo", "kind": "allow_always"},
            {"optionId": "reject", "name": "No", "kind": "reject_once"},
        ],
    }})
    reply = read()
    while reply.get("id") != 0 or "method" in reply:
        reply = read()
    outcome = (reply.get("result") or {}).get("outcome") or {}
    if outcome.get("optionId") in ("allow-once", "allow-with-updates"):
        update(sid, sessionUpdate="tool_call_update", toolCallId="t1", status="completed", rawOutput="hi", _meta=BASH)
    else:
        update(sid, sessionUpdate="tool_call_update", toolCallId="t1", status="failed",
               rawOutput="denied by user", _meta=BASH)
    chunk(sid, "All ", "m2")
    chunk(sid, "done.", "m2")
    update(sid, sessionUpdate="usage_update", used=1234, size=200000)
    result(rid, {"stopReason": "end_turn", "usage": USAGE})


def slow_turn(rid, sid):
    chunk(sid, "Working", "m1")
    while True:
        message = read()
        if message.get("method") == "session/cancel":
            result(rid, {"stopReason": "cancelled", "usage": USAGE})
            return
        if message.get("method") == "_session/steering":
            result(message["id"], {"outcome": "injected"})
            chunk(sid, " steered:" + message["params"]["prompt"][0]["text"], "m1")
            result(rid, {"stopReason": "end_turn", "usage": USAGE})
            return


def prompt(message):
    rid, sid = message["id"], message["params"]["sessionId"]
    text = "".join(block.get("text", "") for block in message["params"]["prompt"])
    if text == "tool":
        tool_turn(rid, sid)
    elif text == "slow":
        slow_turn(rid, sid)
    elif text == "/compact":
        meta = {"claudeCode": {"toolName": "compact"}}
        update(sid, sessionUpdate="tool_call", toolCallId="c1", title="Compact conversation", kind="think",
               status="in_progress", _meta=meta)
        update(sid, sessionUpdate="tool_call_update", toolCallId="c1", status="completed",
               rawOutput={"trigger": "manual", "preTokens": 900, "postTokens": 100}, _meta=meta)
        result(rid, {"stopReason": "end_turn", "usage": dict.fromkeys(USAGE, 0)})
    elif text == "crash":
        chunk(sid, "about to crash", "m1")
        sys.exit(3)
    elif text == "error":
        send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32603, "message": "Internal error",
                                                       "data": {"details": "query closed"}}})
    else:
        chunk(sid, f"mode={state['mode']} echo={text}", "m1")
        result(rid, {"stopReason": "end_turn", "usage": USAGE})


def session_state(sid):
    return {
        "sessionId": sid,
        "modes": {"currentModeId": state["mode"], "availableModes": [
            {"id": mode, "name": mode} for mode in ("default", "acceptEdits", "plan", "auto", "bypassPermissions")
        ]},
        "configOptions": [{"id": "model", "currentValue": "sonnet",
                           "options": [{"value": "sonnet", "name": "Sonnet 5.5"}]}],
    }


def auth_status():
    send({"jsonrpc": "2.0", "method": "_auth/status_update", "params": {
        "authStatus": {"kind": os.environ.get("FAKE_ACP_AUTH", "account"), "label": "Claude Team"},
    }})


while True:
    msg = read()
    method, rid = msg.get("method"), msg.get("id")
    if method == "initialize":
        result(rid, {"protocolVersion": 1, "agentInfo": {"name": "fake-claude-acp", "version": "9.9.9"},
                     "agentCapabilities": {}, "_meta": {"steering": {"supported": True}}})
    elif method == "session/new":
        auth_status()
        result(rid, session_state("new-session"))
    elif method == "session/resume":
        auth_status()
        if msg["params"]["sessionId"] == "known-session":
            result(rid, session_state("known-session"))
        else:
            send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32002, "message": "Resource not found"}})
    elif method == "session/set_mode":
        if msg["params"]["modeId"] not in ("default", "acceptEdits", "plan", "auto", "bypassPermissions"):
            send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32603, "message": "Invalid Mode"}})
            continue
        state["mode"] = msg["params"]["modeId"]
        update(msg["params"]["sessionId"], sessionUpdate="current_mode_update", currentModeId=state["mode"])
        result(rid, {})
    elif method == "session/prompt":
        prompt(msg)
    elif rid is not None and method:
        send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"unsupported {method}"}})
