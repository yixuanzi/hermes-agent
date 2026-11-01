"""claude_acp — 经 ACP 驱动本机 Claude Code 的会话（/claude 的运行时）。

本机已安装、已登录、~/.claude 已配置好的 Claude Code，由
``@agentclientprotocol/claude-agent-acp`` 以 ACP（JSON-RPC 2.0，换行分隔的 stdio）
暴露为 agent。ClaudeAcpSession 与 core 的 CodexAppServerSession 同形（run_turn /
request_interrupt / request_steer / compact_thread / compact / rebind / close / closed /
turn_active / cwd）：/claude 把它挂到 ``agent._codex_session`` 并把 api_mode 切到
codex_app_server，core 的 run_codex_app_server_turn() 整轮接管路径（消息投影入库、
token 统计、中断、steer、/compress）原样复用，core 无需改动。

协议要点（claude-agent-acp 0.93.0 实测）：
  - initialize 不声明 fs / terminal 能力：读写文件、执行命令都由 Claude Code 自己的工具完成
  - session/new，或 session/resume 续接退役前的同一个 Claude 会话；session/set_mode 设权限模式
  - session/prompt 阻塞到回合结束，结果带 stopReason 与本回合 usage
  - session/update 通知：agent_message_chunk / agent_thought_chunk / tool_call /
    tool_call_update / usage_update / current_mode_update / config_option_update ...
  - session/request_permission：Claude Code 需要授权时的反向请求
  - session/cancel 通知中断；_session/steering 请求把消息注入运行中的回合
"""

from __future__ import annotations

import collections
import json
import logging
import os
import queue
import shlex
import shutil
import signal
import subprocess
import threading
import time
from typing import Any, Callable, Optional

from agent.transports.codex_app_server_session import TurnResult, _coerce_turn_input_text

logger = logging.getLogger(__name__)

# 默认经 npx 运行的 claude-agent-acp 版本（精确钉版，见 AGENTS.md 供应链策略）。
# 本机已全局安装 claude-agent-acp 时优先用它；HERMES_CLAUDE_ACP_COMMAND 可整体覆盖启动命令。
DEFAULT_ACP_PACKAGE = "@agentclientprotocol/claude-agent-acp@0.93.0"
ACP_COMMAND_ENV = "HERMES_CLAUDE_ACP_COMMAND"
ACP_PROTOCOL_VERSION = 1

PERMISSION_MODES = ("default", "acceptEdits", "plan", "auto", "bypassPermissions")
BYPASS_MODE = "bypassPermissions"

# 回合内连续这么久收不到 Claude Code 的任何事件，视为卡死：中断并退役会话
TURN_IDLE_TIMEOUT = 1800.0
# session/cancel 之后等回合收尾的时间
CANCEL_GRACE = 30.0
# 投影进 Hermes 历史（state.db）的单个工具结果上限；完整上下文在 Claude Code 自己的会话里。
# 可经 config.yaml 的 bridge.claude_history_result_limit 覆盖（默认 4000）。
_HISTORY_RESULT_LIMIT = 4000
_PROGRESS_RESULT_LIMIT = 4000
# 投影进 Hermes 历史的单个工具参数（tool 输入）JSON 上限；同样可经
# bridge.claude_args_limit 覆盖。原始参数在 Claude Code 自己的会话里完整保留。
_HISTORY_ARGS_LIMIT = 1500

_STOP_REASON_LABELS = {
    "max_tokens": "输出达到 token 上限",
    "max_turn_requests": "达到单回合请求次数上限",
    "refusal": "模型拒绝继续",
}
_AUTH_HINT = "Claude Code 未登录或登录已失效：在终端运行 `claude` 并执行 /login 后重试"


def _bridge_limits() -> tuple[int, int]:
    """从 config.yaml 的 bridge 段读取投影上限，缺省用模块默认值。

    bridge.claude_history_result_limit — 工具结果上限（默认 4000）
    bridge.claude_args_limit           — 工具参数 JSON 上限（默认 1500）
    """
    history = _HISTORY_RESULT_LIMIT
    args = _HISTORY_ARGS_LIMIT
    try:
        from hermes_cli.config import load_config

        cfg = load_config() or {}
        bridge = cfg.get("bridge") or {}
        raw_h = bridge.get("claude_history_result_limit")
        if isinstance(raw_h, int) and raw_h > 0:
            history = raw_h
        raw_a = bridge.get("claude_args_limit")
        if isinstance(raw_a, int) and raw_a > 0:
            args = raw_a
    except Exception:
        pass
    return history, args


def _clip_args(args: dict, limit: int) -> dict:
    """裁剪工具参数：逐字段截断长字符串值，保留键名与结构（检索友好）。"""
    out: dict[str, Any] = {}
    for k, v in args.items():
        if isinstance(v, str) and len(v) > limit:
            out[k] = v[:limit] + f"…[clipped {len(v) - limit} chars]"
        else:
            out[k] = v
    return out


class ClaudeAcpError(RuntimeError):
    def __init__(self, message: str, *, code: Optional[int] = None, data: Any = None):
        super().__init__(message)
        self.code = code
        self.data = data

    @classmethod
    def from_payload(cls, payload: Any) -> "ClaudeAcpError":
        if not isinstance(payload, dict):
            return cls(str(payload))
        message = str(payload.get("message") or "ACP error")
        data = payload.get("data")
        if isinstance(data, dict) and data.get("details"):
            message = f"{message}: {data['details']}"
        elif isinstance(data, str) and data.strip():
            message = f"{message}: {data.strip()}"
        return cls(message, code=payload.get("code"), data=data)


# ------------------------------------------------------------- 启动命令 ----
def resolve_acp_command() -> list[str]:
    override = os.environ.get(ACP_COMMAND_ENV, "").strip()
    if override:
        return shlex.split(override)
    installed = shutil.which("claude-agent-acp")
    if installed:
        return [installed]
    npx = shutil.which("npx")
    if npx:
        return [npx, "-y", DEFAULT_ACP_PACKAGE]
    raise ClaudeAcpError(
        "未找到 claude-agent-acp 或 npx：安装 Node.js，或 `npm i -g @agentclientprotocol/claude-agent-acp`，"
        f"或用 {ACP_COMMAND_ENV} 指定启动命令"
    )


def resolve_claude_executable() -> Optional[str]:
    return os.environ.get("CLAUDE_CODE_EXECUTABLE", "").strip() or shutil.which("claude")


def check_claude_code() -> tuple[bool, str]:
    """检查本机 Claude Code 与 ACP 适配器可用。返回 (True, 版本) 或 (False, 原因)。"""
    try:
        resolve_acp_command()
    except ClaudeAcpError as exc:
        return False, str(exc)
    executable = resolve_claude_executable()
    if not executable:
        return False, "未找到本机 claude CLI：先安装并登录 Claude Code，或用 CLAUDE_CODE_EXECUTABLE 指定路径"
    try:
        proc = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"`claude --version` 失败：{exc}"
    if proc.returncode != 0:
        return False, f"`claude --version` 退出码 {proc.returncode}：{proc.stderr.strip()[:300]}"
    lines = proc.stdout.strip().splitlines()
    return True, lines[0].replace("(Claude Code)", "").strip() if lines else "unknown"


def _spawn_env(claude_executable: str) -> dict[str, str]:
    from hermes_constants import apply_subprocess_home_env
    from tools.environments.local import hermes_subprocess_env

    # 鉴权走 Claude Code 自己的登录与 ~/.claude 配置：不继承 Hermes 的 provider 密钥
    # （ANTHROPIC_API_KEY 等会让 Claude Code 改走 API key 计费），也不把它们交给一个
    # 会执行任意命令的子进程。HOME 按 Hermes 子进程约定修正回真实用户目录。
    env = hermes_subprocess_env(inherit_credentials=False)
    apply_subprocess_home_env(env)
    env["CLAUDE_CODE_EXECUTABLE"] = claude_executable
    return env


# ----------------------------------------------------------- ACP 连接 ----
class _Pending:
    __slots__ = ("event", "result", "error")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.result: Any = None
        self.error: Optional[BaseException] = None

    @property
    def done(self) -> bool:
        return self.event.is_set()

    def resolve(self, message: dict) -> None:
        if "error" in message:
            self.error = ClaudeAcpError.from_payload(message["error"])
        else:
            self.result = message.get("result")
        self.event.set()

    def fail(self, exc: BaseException) -> None:
        self.error = exc
        self.event.set()

    def wait(self, timeout: Optional[float]) -> Any:
        if not self.event.wait(timeout):
            raise TimeoutError("ACP request timed out")
        if self.error is not None:
            raise self.error
        return self.result


class AcpConnection:
    """claude-agent-acp 子进程上的 ACP 连接。

    读线程按到达顺序处理：响应交给对应请求的等待者；通知与反向请求进 ``inbox``，
    由驱动回合的线程（agent 线程）消费，显示回调与审批回调因此都跑在 agent 线程上
    （与 codex 运行时一致）。同一读线程先入队通知、后唤醒响应，所以等待者被唤醒时，
    响应之前到达的通知都已在 inbox 里。
    """

    def __init__(self, argv: list[str], *, cwd: str, env: dict[str, str]) -> None:
        popen_kwargs: dict[str, Any] = {}
        if os.name == "posix":
            # npx → node → claude 及其后台任务整组回收
            popen_kwargs["start_new_session"] = True
        else:
            from hermes_cli._subprocess_compat import windows_hide_flags

            popen_kwargs["creationflags"] = windows_hide_flags()
        self._proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            env=env,
            **popen_kwargs,
        )
        self.inbox: queue.Queue = queue.Queue()
        self._pending: dict[int, _Pending] = {}
        self._pending_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._next_id = 0
        self._stderr: collections.deque[str] = collections.deque(maxlen=200)
        self._closed = False
        threading.Thread(target=self._read_stdout, name="claude-acp-stdout", daemon=True).start()
        threading.Thread(target=self._read_stderr, name="claude-acp-stderr", daemon=True).start()

    # ---------- io ----------
    def _read_stdout(self) -> None:
        try:
            for raw in iter(self._proc.stdout.readline, b""):
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except ValueError:
                    logger.debug("claude-acp: non-JSON stdout line: %.200s", line)
                    continue
                if not isinstance(message, dict):
                    continue
                if "method" in message:
                    self.inbox.put(message)
                elif "id" in message:
                    with self._pending_lock:
                        pending = self._pending.pop(message["id"], None)
                    if pending is not None:
                        pending.resolve(message)
        except (OSError, ValueError):
            pass
        finally:
            with self._pending_lock:
                waiting = list(self._pending.values())
                self._pending.clear()
            for pending in waiting:
                pending.fail(ClaudeAcpError("claude-agent-acp 进程已退出"))

    def _read_stderr(self) -> None:
        try:
            for raw in iter(self._proc.stderr.readline, b""):
                self._stderr.append(raw.decode("utf-8", errors="replace").rstrip())
        except (OSError, ValueError):
            pass

    def _send(self, message: dict) -> None:
        data = (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
        try:
            with self._write_lock:
                self._proc.stdin.write(data)
                self._proc.stdin.flush()
        except (OSError, ValueError) as exc:
            raise ClaudeAcpError(f"无法写入 claude-agent-acp：{exc}") from exc

    # ---------- json-rpc ----------
    def start_request(self, method: str, params: dict) -> _Pending:
        pending = _Pending()
        with self._pending_lock:
            self._next_id += 1
            request_id = self._next_id
            self._pending[request_id] = pending
        try:
            self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        except ClaudeAcpError as exc:
            with self._pending_lock:
                self._pending.pop(request_id, None)
            pending.fail(exc)
        return pending

    def request(self, method: str, params: dict, *, timeout: float) -> Any:
        return self.start_request(method, params).wait(timeout)

    def notify(self, method: str, params: dict) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def respond(self, request_id: Any, result: Any) -> None:
        self._send({"jsonrpc": "2.0", "id": request_id, "result": result})

    def respond_error(self, request_id: Any, code: int, message: str) -> None:
        self._send({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})

    # ---------- lifecycle ----------
    def is_alive(self) -> bool:
        return self._proc.poll() is None

    def stderr_tail(self, lines: int = 20) -> list[str]:
        return list(self._stderr)[-lines:]

    def close(self, timeout: float = 5.0) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._proc.stdin.close()  # 适配器在 stdin EOF 时自行收尾退出
        except (OSError, ValueError):
            pass
        try:
            self._proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._signal_tree(signal.SIGTERM)
            try:
                self._proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                self._signal_tree(signal.SIGKILL if os.name == "posix" else signal.SIGTERM)
        # 主进程退出后，组内残留（Claude Code 的后台命令等）一并结束
        self._signal_tree(signal.SIGTERM, group_only=True)

    def _signal_tree(self, sig: int, *, group_only: bool = False) -> None:
        if os.name == "posix":
            try:
                os.killpg(self._proc.pid, sig)
            except (ProcessLookupError, PermissionError, OSError):
                pass
        elif not group_only:
            try:
                self._proc.kill()
            except OSError:
                pass


# ------------------------------------------------------------ 内容转换 ----
def _content_text(content: Any) -> str:
    if isinstance(content, dict):
        if content.get("type") == "text":
            return str(content.get("text") or "")
        return ""
    if isinstance(content, list):
        return "".join(_content_text(part) for part in content)
    return ""


def _image_block(item: dict) -> Optional[dict]:
    url = item.get("image_url")
    if isinstance(url, dict):
        url = url.get("url")
    url = url or item.get("url") or ""
    if not isinstance(url, str) or not url.startswith("data:") or ";base64," not in url:
        return None
    header, data = url[5:].split(";base64,", 1)
    return {"type": "image", "mimeType": header or "image/png", "data": data}


def acp_prompt(user_input: Any) -> list[dict]:
    """Hermes 用户输入 → ACP prompt 内容块（文本；data URL 图片转成 image 块）。"""
    if isinstance(user_input, list):
        blocks: list[dict] = []
        for item in user_input:
            if isinstance(item, str):
                if item.strip():
                    blocks.append({"type": "text", "text": item})
            elif isinstance(item, dict):
                item_type = item.get("type")
                if item_type in {"text", "input_text"}:
                    text = item.get("text") or item.get("content") or ""
                    if text:
                        blocks.append({"type": "text", "text": str(text)})
                elif item_type in {"image", "image_url", "input_image"}:
                    blocks.append(_image_block(item) or {"type": "text", "text": "[image attached]"})
        if blocks:
            return blocks
    return [{"type": "text", "text": _coerce_turn_input_text(user_input)}]


def tool_name(update: dict) -> str:
    meta = (update.get("_meta") or {}).get("claudeCode") or {}
    return str(meta.get("toolName") or update.get("name") or update.get("kind") or "tool")


def call_id_for(tool_call_id: str) -> str:
    return f"claude_{tool_call_id}"


def tool_result_text(update: dict) -> str:
    raw = update.get("rawOutput")
    if isinstance(raw, str) and raw.strip():
        return raw
    parts: list[str] = []
    for block in update.get("content") or []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "content":
            text = _content_text(block.get("content"))
            if text:
                parts.append(text)
        elif block.get("type") == "diff":
            parts.append(f"[diff] {block.get('path') or ''}")
    if parts:
        return "\n".join(parts)
    if raw is not None:
        return json.dumps(raw, ensure_ascii=False)
    return ""


def _is_compaction(update: dict) -> bool:
    kind = update.get("sessionUpdate")
    if kind == "compaction_update":
        return update.get("status") == "completed"
    return (
        kind in {"tool_call", "tool_call_update"}
        and tool_name(update) == "compact"
        and update.get("status") == "completed"
    )


def _token_usage(usage: Any) -> Optional[dict]:
    """ACP 回合 usage → codex 记账口径（_record_codex_app_server_usage 读取的字段）。"""
    if not isinstance(usage, dict):
        return None

    def _n(key: str) -> int:
        value = usage.get(key)
        return value if isinstance(value, int) and value > 0 else 0

    input_tokens, output_tokens = _n("inputTokens"), _n("outputTokens")
    cache_read, cache_write = _n("cachedReadTokens"), _n("cachedWriteTokens")
    total = _n("totalTokens") or input_tokens + output_tokens + cache_read + cache_write
    if not total:
        return None
    # codex 口径没有缓存写入桶：缓存写入计入未命中缓存的输入
    return {
        "inputTokens": input_tokens + cache_write,
        "cachedInputTokens": cache_read,
        "outputTokens": output_tokens,
        "reasoningOutputTokens": 0,
        "totalTokens": total,
    }


# ------------------------------------------------------------ 历史投影 ----
class _TurnProjector:
    """把一回合的 ACP 更新投影成 Hermes 标准消息（assistant / tool_calls / tool）。

    文本按 messageId 分段；工具在完成时落成一对 (assistant tool_call, tool result)；
    思考内容挂到下一条 assistant 消息的 reasoning 上。与 CodexEventProjector 同一口径。
    """

    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.tool_iterations = 0
        self.final_text = ""
        self.compacted = False
        self._text_id: Optional[str] = None
        self._text_parts: list[str] = []
        self._reasoning: list[str] = []
        self._tools: dict[str, dict] = {}

    def feed(self, update: dict) -> None:
        kind = update.get("sessionUpdate")
        if _is_compaction(update):
            self.compacted = True
        if kind == "agent_message_chunk":
            text = _content_text(update.get("content"))
            if not text:
                return
            message_id = update.get("messageId")
            if self._text_parts and message_id and self._text_id and message_id != self._text_id:
                self._close_text()
            self._text_id = message_id or self._text_id
            self._text_parts.append(text)
        elif kind == "agent_thought_chunk":
            text = _content_text(update.get("content"))
            if text:
                self._reasoning.append(text)
        elif kind in {"tool_call", "tool_call_update"}:
            self._feed_tool(update)

    def _feed_tool(self, update: dict) -> None:
        tool_call_id = update.get("toolCallId")
        if not tool_call_id or tool_name(update) == "compact":
            return
        entry = self._tools.get(tool_call_id)
        if entry is None:
            self._close_text()  # 工具开始前的文本是一条独立的 assistant 消息
            entry = self._tools[tool_call_id] = {"name": tool_name(update), "args": {}, "done": False}
        raw_input = update.get("rawInput")
        if isinstance(raw_input, dict) and raw_input:
            entry["args"] = dict(raw_input)
        if (update.get("_meta") or {}).get("claudeCode", {}).get("toolName"):
            entry["name"] = tool_name(update)
        status = update.get("status")
        if status not in {"completed", "failed"} or entry["done"]:
            return
        entry["done"] = True
        self._close_text()
        call_id = call_id_for(tool_call_id)
        history_limit, args_limit = _bridge_limits()
        assistant: dict[str, Any] = {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": call_id,
                "type": "function",
                "function": {
                    "name": entry["name"],
                    "arguments": json.dumps(_clip_args(entry["args"], args_limit), ensure_ascii=False, sort_keys=True),
                },
            }],
        }
        self._attach_reasoning(assistant)
        result = tool_result_text(update)
        if status == "failed" and not result.startswith("[error]"):
            result = f"[error] {result}"
        if len(result) > history_limit:
            result = result[:history_limit] + f"\n…[truncated {len(result) - history_limit} chars]"
        self.messages += [assistant, {"role": "tool", "tool_call_id": call_id, "content": result}]
        self.tool_iterations += 1

    def add_user(self, text: str) -> None:
        """回合中 steer 注入的用户消息。"""
        self._close_text()
        self.messages.append({"role": "user", "content": text})

    def finish(self) -> None:
        self._close_text()

    def _attach_reasoning(self, message: dict) -> None:
        if self._reasoning:
            message["reasoning"] = "".join(self._reasoning)
            self._reasoning = []

    def _close_text(self) -> None:
        if not self._text_parts:
            return
        text = "".join(self._text_parts)
        self._text_parts, self._text_id = [], None
        if not text.strip():
            return
        message: dict[str, Any] = {"role": "assistant", "content": text}
        self._attach_reasoning(message)
        self.messages.append(message)
        self.final_text = text


# ------------------------------------------------------------ 显示桥接 ----
class ClaudeEventBridge:
    """ACP session/update → Hermes 显示回调，约定与 core 的 codex 事件桥一致。

    * agent_message_chunk → ``_fire_stream_delta``；agent_thought_chunk → ``_fire_reasoning_delta``
    * 消息之后又开始了工具或新消息 → 该消息是过程说明：``_emit_interim_assistant_message``
      并重置流分段；回合最后一条消息是最终回答，交给正常的最终回复投递
    * 工具拿到参数时 → ``tool_progress_callback("tool.started")`` + ``tool_start_callback``；
      完成/失败 → ``tool.completed`` + ``tool_complete_callback``（与投影同一个 call id）
    * 每个事件刷新 agent 活跃时间，长回合不会被 gateway 的空闲看门狗误判卡死

    回调异常只记 DEBUG，不影响回合。
    """

    def __init__(self, agent: Any) -> None:
        self._agent = agent
        self._message_id: Optional[str] = None
        self._message_text = ""
        self._tools: dict[str, dict] = {}

    def begin_turn(self) -> None:
        self._message_id, self._message_text = None, ""
        self._tools.clear()

    def _call(self, name: str, *args: Any, **kwargs: Any) -> None:
        fn = getattr(self._agent, name, None)
        if not callable(fn):
            return
        try:
            fn(*args, **kwargs)
        except Exception:
            logger.debug("claude bridge: %s raised", name, exc_info=True)

    def _flush_commentary(self) -> None:
        text = self._message_text
        self._message_id, self._message_text = None, ""
        if not text.strip():
            return
        # display.show_commentary=false 时过程说明不走可见的 interim 路径
        if getattr(self._agent, "show_commentary", True):
            self._call("_emit_interim_assistant_message", {"role": "assistant", "content": text})
        self._call("_reset_stream_delivery_tracking")
        self._agent._stream_needs_break = True

    def __call__(self, update: dict) -> None:
        kind = update.get("sessionUpdate")
        if kind == "agent_message_chunk":
            text = _content_text(update.get("content"))
            if not text:
                return
            message_id = update.get("messageId")
            if self._message_text and message_id and self._message_id and message_id != self._message_id:
                self._flush_commentary()
            self._message_id = message_id or self._message_id
            self._message_text += text
            self._call("_touch_activity", "claude: streaming response")
            self._call("_fire_stream_delta", text)
        elif kind == "agent_thought_chunk":
            text = _content_text(update.get("content"))
            if text:
                self._call("_touch_activity", "claude: thinking")
                self._call("_fire_reasoning_delta", text)
        elif kind in {"tool_call", "tool_call_update"}:
            self._on_tool(update)

    def _on_tool(self, update: dict) -> None:
        tool_call_id = update.get("toolCallId")
        if not tool_call_id:
            return
        entry = self._tools.get(tool_call_id)
        if entry is None:
            if self._message_text:
                self._flush_commentary()
            entry = self._tools[tool_call_id] = {
                "name": tool_name(update), "args": {}, "title": "",
                "announced": False, "started_at": time.monotonic(), "done": False,
            }
        raw_input = update.get("rawInput")
        if isinstance(raw_input, dict) and raw_input:
            entry["args"] = dict(raw_input)
        if update.get("title"):
            entry["title"] = str(update["title"])
        if (update.get("_meta") or {}).get("claudeCode", {}).get("toolName"):
            entry["name"] = tool_name(update)
        status = update.get("status")
        self._call("_touch_activity", f"claude: tool {entry['name']}")
        if not entry["announced"] and (entry["args"] or status in {"in_progress", "completed", "failed"}):
            self._announce(tool_call_id, entry)
        if status in {"completed", "failed"} and not entry["done"]:
            entry["done"] = True
            result = tool_result_text(update)[:_PROGRESS_RESULT_LIMIT]
            duration = time.monotonic() - entry["started_at"]
            cb = getattr(self._agent, "tool_progress_callback", None)
            if cb is not None:
                try:
                    cb("tool.completed", entry["name"], None, None,
                       duration=duration, is_error=status == "failed", result=result)
                except Exception:
                    logger.debug("tool_progress_callback raised on tool.completed", exc_info=True)
            self._call("tool_complete_callback", call_id_for(tool_call_id), entry["name"], entry["args"], result)

    def _announce(self, tool_call_id: str, entry: dict) -> None:
        entry["announced"] = True
        entry["started_at"] = time.monotonic()
        cb = getattr(self._agent, "tool_progress_callback", None)
        if cb is not None:
            try:
                cb("tool.started", entry["name"], entry["title"] or None, entry["args"])
            except Exception:
                logger.debug("tool_progress_callback raised on tool.started", exc_info=True)
        self._call("tool_start_callback", call_id_for(tool_call_id), entry["name"], entry["args"])


# -------------------------------------------------------------- 会话 ----
def _permission_prompt(tool_call: dict, cwd: str, always_option: Optional[dict]) -> tuple[str, str]:
    name = tool_name(tool_call)
    raw_input = tool_call.get("rawInput") if isinstance(tool_call.get("rawInput"), dict) else {}
    title = str(tool_call.get("title") or "").strip()
    if name == "Bash" and raw_input.get("command"):
        label = str(raw_input["command"])
    elif title.startswith(name):
        label = title
    else:
        label = f"{name}: {title}" if title else name
    description = f"Claude Code 请求授权 {name}（cwd：{cwd}）"
    if raw_input.get("description"):
        description += f" — {raw_input['description']}"
    if always_option is not None:
        description += f"；选择 always 即 Claude Code 的「{always_option.get('name')}」"
    return label, description


def _flat_choices(options: Any) -> list[dict]:
    """select 选项可能分组（{"options": [...]}）：摊平成 value/name 列表。"""
    flat: list[dict] = []
    for item in options or []:
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("options"), list):
            flat.extend(_flat_choices(item["options"]))
        elif item.get("value") is not None:
            flat.append(item)
    return flat


class ClaudeAcpSession:
    """一个 Hermes 会话对应的 Claude Code（ACP）会话，跨轮次复用。

    与 CodexAppServerSession 同样不是线程安全的回合驱动者：run_turn / compact_thread
    由 agent 线程调用；request_interrupt / request_steer 可来自其他线程。
    """

    def __init__(
        self,
        *,
        cwd: str,
        permission_mode: Optional[str] = None,
        resume_session_id: Optional[str] = None,
        preferences: Optional[dict[str, str]] = None,
        on_event: Optional[Callable[[dict], None]] = None,
        approval_callback: Optional[Callable[..., str]] = None,
        auto_approve: bool = False,
        connection_factory: Optional[Callable[..., AcpConnection]] = None,
    ) -> None:
        self._cwd = cwd
        # None = 沿用 Claude Code 配置的 permissions.defaultMode（新会话初始模式）
        self._permission_mode = permission_mode
        self._resume_session_id = resume_session_id
        # /claude model set 选定的 model / effort：新开或 resume 的会话握手后重新套用
        self._preferences: dict[str, str] = dict(preferences or {})
        self._on_event = on_event
        self._approval_callback = approval_callback
        self._auto_approve = auto_approve
        self._connection_factory = connection_factory or AcpConnection
        self._conn: Optional[AcpConnection] = None
        self._acp_session_id: Optional[str] = None
        self._closed = False
        self._start_lock = threading.Lock()
        self._active_lock = threading.Lock()
        self._active_turn: Optional[str] = None
        self._interrupt_event = threading.Event()
        self._steered: queue.Queue = queue.Queue()
        self._turns = 0
        # Claude Code 报告的状态：agent / mode / default_mode / model / model_name / account / steering ...
        self.info: dict[str, Any] = {}

    # ---------- properties ----------
    @property
    def cwd(self) -> str:
        return self._cwd

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def started(self) -> bool:
        return self._conn is not None and self._acp_session_id is not None and not self._closed

    @property
    def acp_session_id(self) -> Optional[str]:
        """Claude Code 会话 id；关闭后仍保留，供替换会话 session/resume 续接上下文。"""
        return self._acp_session_id

    @property
    def permission_mode(self) -> Optional[str]:
        return self._permission_mode

    @property
    def preferences(self) -> dict[str, str]:
        return dict(self._preferences)

    @property
    def turn_active(self) -> bool:
        with self._active_lock:
            return self._active_turn is not None

    def rebind(
        self,
        *,
        on_event: Optional[Callable[[dict], None]],
        approval_callback: Optional[Callable[..., str]],
        auto_approve: bool = False,
    ) -> None:
        """回合之间把显示 / 审批回调改绑到新的宿主 agent（gateway 重建 AIAgent 时）。"""
        self._on_event = on_event
        self._approval_callback = approval_callback
        self._auto_approve = auto_approve

    def set_permission_mode(self, mode: Optional[str]) -> None:
        """记下期望的权限模式；在 apply_permission_mode() 或下一回合开始前生效。"""
        self._permission_mode = mode

    # ---------- lifecycle ----------
    def ensure_started(self) -> str:
        with self._start_lock:
            if self._closed:
                raise ClaudeAcpError("Claude Code 会话已关闭")
            if self._conn is not None and self._acp_session_id:
                return self._acp_session_id
            executable = resolve_claude_executable()
            if not executable:
                raise ClaudeAcpError("未找到本机 claude CLI")
            conn = self._connection_factory(resolve_acp_command(), cwd=self._cwd, env=_spawn_env(executable))
            try:
                self._handshake(conn)
                if self._closed:  # 握手期间被 close()
                    raise ClaudeAcpError("Claude Code 会话已关闭")
            except BaseException:
                conn.close()
                raise
            self._conn = conn
            return self._acp_session_id

    def _handshake(self, conn: AcpConnection) -> None:
        # 首次经 npx 运行可能要先下载适配器
        init = conn.request("initialize", {
            "protocolVersion": ACP_PROTOCOL_VERSION,
            "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False},
            "clientInfo": {"name": "hermes", "title": "Hermes Agent", "version": _hermes_version()},
        }, timeout=180)
        agent_info = init.get("agentInfo") or {}
        self.info["agent"] = f"{agent_info.get('name') or 'claude-agent-acp'} {agent_info.get('version') or ''}".strip()
        self.info["steering"] = bool(((init.get("_meta") or {}).get("steering") or {}).get("supported"))

        params = {"cwd": self._cwd, "mcpServers": []}
        result = None
        if self._resume_session_id:
            try:
                result = conn.request("session/resume", {**params, "sessionId": self._resume_session_id}, timeout=120)
                result.setdefault("sessionId", self._resume_session_id)
                self.info["resumed"] = True
            except (ClaudeAcpError, TimeoutError) as exc:
                logger.warning("/claude: resuming Claude session %s failed (%s); starting a new one",
                               self._resume_session_id, exc)
        if result is None:
            result = conn.request("session/new", params, timeout=120)
        session_id = result.get("sessionId") if isinstance(result, dict) else None
        if not session_id:
            raise ClaudeAcpError("claude-agent-acp 未返回 sessionId")
        self._acp_session_id = session_id
        self._absorb_session_state(result)
        self.info.setdefault("default_mode", self.info.get("mode"))

        # 鉴权状态由适配器在后台探测后推送（_auth/status_update），通常在 session/new 期间到达
        deadline = time.monotonic() + 5.0
        while "account" not in self.info and time.monotonic() < deadline:
            try:
                self._dispatch(conn, conn.inbox.get(timeout=0.2), None, None)
            except queue.Empty:
                continue
        if (self.info.get("account") or {}).get("kind") == "none":
            raise ClaudeAcpError(_AUTH_HINT)
        self._set_mode(conn, self._permission_mode or self.info.get("default_mode"))
        # 先 model 再 effort：可选的思考深度取决于当前模型
        for config_id in ("model", "effort"):
            value = self._preferences.get(config_id)
            if value and value != self.info.get(config_id):
                try:
                    self._set_config(conn, config_id, value)
                except ClaudeAcpError as exc:
                    logger.warning("/claude: re-applying %s=%s failed: %s", config_id, value, exc)
                    self._preferences.pop(config_id, None)

    def set_config_option(self, config_id: str, value: str) -> None:
        """session/set_config_option：config_id 为 model 或 effort；回合进行中不接受。"""
        if self.turn_active:
            raise ClaudeAcpError("Claude Code 正在执行，请等本轮结束（或 /stop）后再设置")
        conn = self._conn
        if conn is None or not self._acp_session_id or self._closed:
            raise ClaudeAcpError("Claude Code 会话尚未启动")
        self._set_config(conn, config_id, value)
        self._preferences[config_id] = str(self.info.get(config_id) or value)
        if config_id == "model":
            # 换模型后原来的思考深度可能不再适用
            effort = self._preferences.get("effort")
            if effort and effort not in {o["value"] for o in self.info.get("effort_options") or []}:
                self._preferences.pop("effort", None)

    def _set_config(self, conn: AcpConnection, config_id: str, value: str) -> None:
        try:
            result = conn.request(
                "session/set_config_option",
                {"sessionId": self._acp_session_id, "configId": config_id, "value": value},
                timeout=60,
            )
        except TimeoutError as exc:
            raise ClaudeAcpError(f"设置 {config_id} 超时") from exc
        if isinstance(result, dict):
            self._absorb_config(result.get("configOptions"))

    def apply_permission_mode(self) -> None:
        """把期望的权限模式同步到已启动的 Claude Code 会话（未指定时恢复其初始模式）。"""
        conn = self._conn
        if conn is not None and self._acp_session_id:
            self._set_mode(conn, self._permission_mode or self.info.get("default_mode"))

    def _set_mode(self, conn: AcpConnection, mode: Optional[str]) -> None:
        if not mode or mode == self.info.get("mode"):
            return
        try:
            conn.request("session/set_mode", {"sessionId": self._acp_session_id, "modeId": mode}, timeout=30)
        except ClaudeAcpError as exc:
            raise ClaudeAcpError(f"Claude Code 不接受权限模式 `{mode}`：{exc}") from exc
        self.info["mode"] = mode

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        with self._active_lock:
            self._active_turn = None
        conn, self._conn = self._conn, None
        if conn is not None:
            conn.close()

    # ---------- interrupt / steer ----------
    def request_interrupt(self) -> None:
        self._interrupt_event.set()

    def request_steer(self, text: str) -> bool:
        cleaned = str(text or "").strip()
        with self._active_lock:
            active = self._active_turn
        conn = self._conn
        if not cleaned or not active or conn is None or not self.info.get("steering"):
            return False
        try:
            outcome = conn.request("_session/steering", {
                "sessionId": self._acp_session_id,
                "prompt": [{"type": "text", "text": cleaned}],
            }, timeout=15)
        except (ClaudeAcpError, TimeoutError):
            logger.debug("/claude: steering rejected", exc_info=True)
            return False
        if isinstance(outcome, dict) and outcome.get("outcome") == "injected":
            self._steered.put(cleaned)
            return True
        return False

    # ---------- turns ----------
    def run_turn(self, user_input: Any, *, idle_timeout: float = TURN_IDLE_TIMEOUT) -> TurnResult:
        return self._run(acp_prompt(user_input), idle_timeout=idle_timeout, display=True)

    def compact_thread(self, *, turn_timeout: float = 600.0) -> TurnResult:
        """core 的手动 /compress 入口（run_codex_app_server_turn 同形）：让 Claude Code 压缩自己的上下文。"""
        return self.compact(turn_timeout=turn_timeout)

    def compact(self, *, turn_timeout: float = 600.0) -> TurnResult:
        """让 Claude Code 压缩自己的会话上下文（/compact）；/compress 与 /claude compact 共用。"""
        result = self._run([{"type": "text", "text": "/compact"}], idle_timeout=turn_timeout, display=False)
        result.compacted = result.compacted or (result.error is None and not result.interrupted)
        result.projected_messages = []
        result.token_usage_last = None
        return result

    def _run(self, prompt: list[dict], *, idle_timeout: float, display: bool) -> TurnResult:
        result = TurnResult()
        try:
            self.ensure_started()
            self.apply_permission_mode()
        except Exception as exc:
            result.error = self._format_error("Claude Code 启动失败", exc)
            result.should_retire = True
            self._interrupt_event.clear()
            return result
        conn = self._conn
        result.thread_id = self._acp_session_id
        if conn is None:  # 启动后、发 prompt 前被 close()（如并发的 /claude off）
            result.error = "Claude Code 会话已关闭"
            result.should_retire = True
            self._interrupt_event.clear()
            return result
        # 回合之间到达的更新（例如后台任务结束后 Claude Code 自发的回合）只吸收状态，不投影
        self._drain(conn, None, None)
        if self._interrupt_event.is_set():
            result.interrupted = True
            self._interrupt_event.clear()
            return result

        self._turns += 1
        result.turn_id = f"{self._acp_session_id}:{self._turns}"
        on_event = self._on_event if display else None
        begin = getattr(on_event, "begin_turn", None)
        if callable(begin):
            begin()
        projector = _TurnProjector()
        pending = conn.start_request("session/prompt", {"sessionId": self._acp_session_id, "prompt": prompt})
        with self._active_lock:
            self._active_turn = result.turn_id
        try:
            self._pump(conn, pending, result, projector, on_event, idle_timeout)
        finally:
            with self._active_lock:
                self._active_turn = None
            self._interrupt_event.clear()
        projector.finish()
        result.projected_messages = projector.messages
        result.tool_iterations = projector.tool_iterations
        result.final_text = projector.final_text
        result.compacted = result.compacted or projector.compacted
        if pending.done:
            self._apply_outcome(result, pending)
        return result

    def _pump(self, conn, pending, result, projector, on_event, idle_timeout) -> None:
        last_signal = time.monotonic()
        cancel_deadline: Optional[float] = None
        while True:
            if self._interrupt_event.is_set() and cancel_deadline is None:
                self._cancel(conn)
                result.interrupted = True
                cancel_deadline = time.monotonic() + CANCEL_GRACE
            try:
                message = conn.inbox.get(timeout=0.2)
            except queue.Empty:
                message = None
            if message is not None:
                self._dispatch(conn, message, projector, on_event, result)
                last_signal = time.monotonic()
                continue
            self._drain_steers(projector)
            if pending.done:
                self._drain(conn, projector, on_event, result)
                return
            if not conn.is_alive():
                result.error = self._format_error("Claude Code（claude-agent-acp）进程意外退出")
                result.should_retire = True
                return
            now = time.monotonic()
            if cancel_deadline is not None and now > cancel_deadline:
                result.error = f"中断后 {CANCEL_GRACE:.0f}s 内 Claude Code 未结束回合，已退役会话"
                result.should_retire = True
                return
            if idle_timeout and now - last_signal > idle_timeout:
                self._cancel(conn)
                result.interrupted = True
                result.error = f"Claude Code {idle_timeout:.0f}s 内没有任何事件，已中断并退役会话"
                result.should_retire = True
                return

    def _cancel(self, conn: AcpConnection) -> None:
        try:
            conn.notify("session/cancel", {"sessionId": self._acp_session_id})
        except ClaudeAcpError:
            logger.debug("/claude: session/cancel failed", exc_info=True)

    def _drain_steers(self, projector: _TurnProjector) -> None:
        while True:
            try:
                projector.add_user(self._steered.get_nowait())
            except queue.Empty:
                return

    def _drain(self, conn, projector, on_event, result=None) -> None:
        while True:
            try:
                message = conn.inbox.get_nowait()
            except queue.Empty:
                break
            self._dispatch(conn, message, projector, on_event, result)
        if projector is not None:
            self._drain_steers(projector)

    def _apply_outcome(self, result: TurnResult, pending: _Pending) -> None:
        try:
            outcome = pending.wait(0) or {}
        except Exception as exc:
            if result.error is None:
                result.error = self._format_error("Claude Code 回合失败", exc)
            result.should_retire = True
            return
        result.token_usage_last = _token_usage(outcome.get("usage"))
        stop = outcome.get("stopReason")
        if stop == "cancelled":
            result.interrupted = True
        elif stop not in (None, "end_turn"):
            logger.info("/claude: turn ended with stopReason=%s", stop)
            if not result.final_text:
                result.final_text = f"（Claude Code 结束本回合：{_STOP_REASON_LABELS.get(stop, stop)}）"

    # ---------- inbound ----------
    def _dispatch(self, conn, message, projector, on_event, result=None) -> None:
        method = message.get("method")
        params = message.get("params") or {}
        if "id" in message:
            self._answer_request(conn, message, projector, on_event)
            return
        if method == "_auth/status_update":
            status = params.get("authStatus")
            if isinstance(status, dict):
                self.info["account"] = status
            return
        if method != "session/update" or params.get("sessionId") not in (None, self._acp_session_id):
            return
        update = params.get("update") or {}
        if not isinstance(update, dict):
            return
        self._absorb_update(update, result)
        if projector is None:
            return
        projector.feed(update)
        if on_event is not None:
            try:
                on_event(update)
            except Exception:
                logger.debug("/claude: on_event raised", exc_info=True)

    def _absorb_session_state(self, state: dict) -> None:
        modes = state.get("modes") or {}
        if modes.get("currentModeId"):
            self.info["mode"] = modes["currentModeId"]
            self.info["available_modes"] = [m.get("id") for m in modes.get("availableModes") or [] if isinstance(m, dict)]
        self._absorb_config(state.get("configOptions"))

    def _absorb_config(self, options: Any) -> None:
        seen = set()
        for option in options or []:
            if not isinstance(option, dict):
                continue
            config_id = option.get("id")
            value = option.get("currentValue")
            if config_id in ("model", "effort"):
                seen.add(config_id)
                choices = [
                    {"value": c.get("value"), "name": c.get("name") or c.get("value"), "description": c.get("description")}
                    for c in _flat_choices(option.get("options"))
                ]
                self.info[config_id] = value
                self.info[f"{config_id}_options"] = choices
                if config_id == "model":
                    for choice in choices:
                        if choice["value"] == value:
                            self.info["model_name"] = choice["description"] if value == "default" else choice["name"]
            elif config_id == "mode" and value:
                self.info["mode"] = value
        if options and "effort" not in seen:
            # 当前模型不支持思考深度：适配器不再给出 effort 选项
            for key in ("effort", "effort_options"):
                self.info.pop(key, None)

    def _absorb_update(self, update: dict, result: Optional[TurnResult]) -> None:
        kind = update.get("sessionUpdate")
        if kind == "current_mode_update" and update.get("currentModeId"):
            self.info["mode"] = update["currentModeId"]
        elif kind == "config_option_update":
            self._absorb_config(update.get("configOptions"))
        elif kind == "usage_update":
            size = update.get("size")
            if isinstance(size, int) and size > 0:
                self.info["context_window"] = size
                if result is not None:
                    result.model_context_window = size
            if isinstance(update.get("used"), int):
                self.info["context_used"] = update["used"]
            if result is not None and _is_compaction(update):
                result.compacted = True
        elif result is not None and _is_compaction(update):
            result.compacted = True

    def _answer_request(self, conn, message, projector, on_event) -> None:
        method = message.get("method")
        request_id = message.get("id")
        params = message.get("params") or {}
        try:
            if method == "session/request_permission":
                tool_call = params.get("toolCall") or {}
                if on_event is not None and tool_call.get("toolCallId"):
                    # 审批提示前先把工具卡片亮出来（参数此时已完整）
                    update = {"sessionUpdate": "tool_call_update", **tool_call}
                    update.pop("status", None)
                    if projector is not None:
                        projector.feed(update)
                    try:
                        on_event(update)
                    except Exception:
                        logger.debug("/claude: on_event raised", exc_info=True)
                conn.respond(request_id, {"outcome": self._decide_permission(params, active=projector is not None)})
            else:
                conn.respond_error(request_id, -32601, f"Hermes does not support {method}")
        except ClaudeAcpError:
            logger.debug("/claude: failed to answer %s", method, exc_info=True)

    def _decide_permission(self, params: dict, *, active: bool) -> dict:
        options = [o for o in params.get("options") or [] if isinstance(o, dict) and o.get("optionId")]

        def pick(*kinds: str) -> Optional[dict]:
            for kind in kinds:
                for option in options:
                    if option.get("kind") == kind:
                        return {"outcome": "selected", "optionId": option["optionId"]}
            return None

        cancelled = {"outcome": "cancelled"}
        reject = pick("reject_once", "reject_always") or cancelled
        if not active or self._interrupt_event.is_set():
            return reject
        if self._auto_approve:
            return pick("allow_once", "allow_always") or cancelled
        callback = self._approval_callback
        if callback is None:
            return reject  # 无审批界面：fail-closed
        always = next((o for o in options if o.get("kind") == "allow_always"), None)
        label, description = _permission_prompt(params.get("toolCall") or {}, self._cwd, always)
        try:
            choice = callback(label, description, allow_permanent=always is not None)
        except Exception:
            logger.exception("/claude: approval callback raised")
            return reject
        if choice in {"once", "session"}:
            # Hermes 的 session 无法精确映射：Claude 的 allow_always 可能写入项目级永久规则
            return pick("allow_once") or reject
        if choice == "always":
            return pick("allow_always", "allow_once") or reject
        return reject

    # ---------- diagnostics ----------
    def _format_error(self, prefix: str, exc: Any = None) -> str:
        detail = str(exc) if exc is not None else ""
        if isinstance(exc, ClaudeAcpError) and exc.code == -32000 and _AUTH_HINT not in detail:
            detail = f"{detail}（{_AUTH_HINT}）"
        text = f"{prefix}：{detail}" if detail else prefix
        conn = self._conn
        tail = conn.stderr_tail(15) if conn is not None else []
        tail = [line for line in tail if line.strip() and "Unexpected case" not in line]
        if tail:
            from agent.redact import redact_sensitive_text

            text += "\nclaude-agent-acp stderr:\n" + redact_sensitive_text("\n".join(tail), force=True)
        return text


def _hermes_version() -> str:
    try:
        from importlib.metadata import version

        return version("hermes-agent")
    except Exception:
        return "0"
