# Hermes A2A 协议支持 — WORKAGENT 模块化设计方案

## 1. 概述

Google A2A (Agent-to-Agent) 协议定义了 AI Agent 之间进行标准化通信的规范。本文将说明如何在 Hermes Agent 中基于现有 `hermes workagent` 入口实现 A2A 支持，而不是新增独立 `hermes a2a` 命令。

### 1.1 关键发现

| 项目 | 状态 |
|------|------|
| 独立 `a2a` 包 | 通过 `pip install google-adk[a2a]` 安装 — 包含完整的协议类型系统、客户端、服务端 |
| 协议类型 | `a2a.types` — AgentCard, Task, Message, Part, TaskState, SendMessageRequest/Response 等 |
| HTTP 客户端 | `a2a.client.A2AClient` — 支持 send_message, send_message_streaming, get_task, cancel_task |
| HTTP 服务端 | `a2a.server.A2AStarletteApplication` + `DefaultRequestHandler` + `InMemoryTaskStore` |
| 注 | `google.adk.a2a` 是 ADK→A2A 集成层，我们不需要它 — 直接使用底层 `a2a` 包 |

### 1.2 与 Hermes 架构的对比

| 方面 | ACP (已有) | A2A (目标) |
|------|-----------|-----------|
| 用途 | IDE 集成 (Agent→Human) | 跨 Agent 通信 (Agent→Agent) |
| 传输 | stdio JSON-RPC | HTTP JSON-RPC (Starlette) |
| 会话管理 | ACP SessionManager | A2A TaskStore |
| 消息格式 | ACP schema | A2A Message / Part |
| 消息循环 | `AIAgent.run_conversation()` | 复用 `run_conversation()` + 异步桥接 |

### 1.3 集成结论

本方案采用“单入口、多模块”设计：

- 命令入口保持为 `hermes workagent`
- 新增 `--module {server,a2a}` 参数，默认 `server`
- `server` 模块保持当前 WORKAGENT Web Console 行为不变
- `a2a` 模块作为同一命令下的第二种服务启动模式
- `cmd_workagent()` 负责按模块分发，并仅在 `--module a2a` 时懒加载 A2A 依赖

---

## 2. Hermes Loop 复用可行性分析

### 2.1 核心结论：✅ 完全可以复用

```python
# Hermes 现有循环 (run_agent.py)
class AIAgent:
    def run_conversation(self, user_message, system_message=None,
                         conversation_history=None, task_id=None) -> dict:
        # 1. Build OpenAI-format messages
        # 2. Loop: call LLM → tool dispatch → append results
        # 3. Return final response + full message history
```

**桥接逻辑**：A2A Message → OpenAI-format messages → `run_conversation()` → final response → A2A Message

### 2.2 差异与解法

| 差异 | A2A 侧 | Hermes 侧 | 解决办法 |
|------|--------|-----------|---------|
| 消息格式 | `a2a.types.Message` (role, parts, task_id) | OpenAI `{"role":"user/assistant","content":str}` | 双向转换器 |
| Part 类型 | TextPart, FilePart, DataPart | `content: str` + 多模态 | TextPart ↔ 字符串；FilePart/DataPart ↔ 附件 |
| 任务模型 | 持久化 Task 对象 (状态机) | stateless 会话 | A2A TaskStore 保存状态，Hermes session_id 映射 |
| 执行模型 | async (Starlette/uvicorn) | 同步 (threading) | `asyncio.to_thread()` 桥接 |
| 流式 | `SendStreamingMessageRequest` SSE | 不支持流式 | 初始版本不支持流式，用 polling 替代 |
| 并发 | 多 task 并行处理 | 单线程循环 | 每个 A2A Task 启动独立 Hermes 子会话 |

### 2.3 核心桥接示意图

```
┌─────────────────────────────────────────────────┐
│           A2A Agent Server (Starlette)           │
│                                                  │
│  ┌─────────────┐    ┌────────────────────────┐  │
│  │ HTTP Handler │───▶│ DefaultRequestHandler   │  │
│  │ (JSON-RPC)   │    │                        │  │
│  └──────┬───────┘    │  ┌──────────────────┐  │  │
│         │            │  │ A2A2HermesAdapter │  │  │
│         │            │  │                  │  │  │
│         │            │  │ ┌──────────────┐ │  │  │
│         │            │  │ │ AIAgent      │ │  │  │
│         │            │  │ │ .run_        │ │  │  │
│         │            │  │ │ conversation │ │  │  │
│         │            │  │ └──────────────┘ │  │  │
│         │            │  └──────────────────┘  │  │
│         │            └────────────────────────┘  │
│         │                                        │
│  ┌──────▼──────┐                                 │
│  │ TaskStore   │ (SQLite/InMemory)               │
│  └─────────────┘                                 │
└─────────────────────────────────────────────────┘
```

---

## 3. WORKAGENT 命令集成设计

### 3.1 命令层次

```bash
hermes workagent [options]

核心参数:
  --module {server,a2a}   选择启动模块，默认 server
  --host HOST             通用监听地址
  --port PORT             通用监听端口
  --insecure              允许非 localhost 绑定

server 模块专属:
  --tui
  --skip-build
  --no-open

a2a 模块专属:
  --name NAME
  --description DESC
  --card FILE
  --db PATH
  --streaming
  --workers N
```

### 3.2 `server` 模块

```bash
hermes workagent --module server [--host HOST] [--port PORT]
             [--tui] [--skip-build] [--no-open] [--insecure]
```

- 启动当前 WORKAGENT FastAPI Web Console
- 默认模块，未显式传 `--module` 时保持现有行为
- 继续负责前端构建、浏览器打开、嵌入式 TUI 等能力

### 3.3 `a2a` 模块

```bash
hermes workagent --module a2a [--host HOST] [--port PORT]
             [--name NAME] [--description DESC]
             [--card FILE]
             [--db PATH]
             [--streaming]
             [--workers N]
             [--insecure]
```

- 启动一个 Starlette/uvicorn A2A 服务
- 自动生成或加载 AgentCard
- 将 Hermes Agent 暴露为 A2A 协议远程 Agent
- 每个 A2A Task 创建独立 Hermes 会话
- 复用 `workagent` 现有的服务进程管理方式（`--status`、`--stop`）

### 3.4 参数兼容与校验

- `--host`、`--port`、`--insecure` 为通用参数，两种模块都可使用
- `--tui`、`--skip-build`、`--no-open` 仅 `server` 模块有效
- `--name`、`--description`、`--card`、`--db`、`--streaming`、`--workers` 仅 `a2a` 模块有效
- 当参数与当前模块不兼容时，CLI 应显式报错，而不是静默忽略

---

## 4. 代码架构设计

### 4.1 包结构

```
hermes-agent/
├── workagent/
│   └── backend/
│       ├── server.py               # 现有: WORKAGENT Web Console server 模块
│       ├── a2a_server.py           # 新增: A2A server 模块入口
│       └── a2a/
│           ├── __init__.py
│           ├── handler.py          # A2A 请求处理器
│           ├── client.py           # A2A HTTP 客户端工具（可选）
│           ├── converter.py        # A2A Message ↔ OpenAI 消息 双向转换
│           ├── card.py             # AgentCard 生成
│           ├── task_manager.py     # Hermes A2A task 会话管理
│           └── config.py           # A2A 配置
├── hermes_cli/
│   └── main.py                     # 扩展 workagent parser + cmd_workagent 分发
```

### 4.2 核心模块设计

#### workagent/backend/a2a/converter.py — 消息转换器

这是最关键的模块，负责 A2A ↔ Hermes 消息格式转换：

```python
from a2a.types import Message as A2AMessage, TextPart, Role
from typing import List, Dict

def a2a_to_hermes(a2a_msg: A2AMessage, history: List[A2AMessage] = None) -> str:
    """
    将 A2A Message 转换为 Hermes (OpenAI-format) 用户消息。
    - 合并所有 TextPart 为字符串
    - FilePart → 附件引用
    - 如果提供 history，构建完整对话上下文
    """
    parts_text = []
    for part in a2a_msg.parts:
        if isinstance(part, TextPart):
            parts_text.append(part.text)
        # FilePart/DataPart 处理...
    return "\n".join(parts_text)

def hermes_to_a2a_response(
    hermes_response: str,
    task_id: str,
    session_state: TaskState = TaskState.COMPLETED
) -> A2AMessage:
    """
    将 Hermes 最终响应转换为 A2A Message。
    """
    return A2AMessage(
        role=Role.AGENT,
        parts=[TextPart(text=hermes_response)],
        task_id=task_id
    )

def hermes_history_to_a2a(
    hermes_messages: List[Dict],
    task_id: str
) -> List[A2AMessage]:
    """将 Hermes 完整对话历史转换为 A2A Message 列表（用于 Task.history）。"""
    # ...
```

#### workagent/backend/a2a/task_manager.py — A2A Task 会话管理

```python
class HermesA2ATaskManager:
    """
    管理 A2A Task 与 Hermes 会话的映射。
    
    核心数据结构:
    - task_id ↔ Hermes session_id 映射
    - Task 状态机 (submitted → working → completed/failed)
    - 消息历史缓存
    """

    def __init__(self, task_store: TaskStore | None = None):
        self.task_store = task_store or InMemoryTaskStore()
        self._hermes_agents: Dict[str, AIAgent] = {}  # task_id → agent

    async def create_task(
        self, task_id: str, initial_message: str
    ) -> Task:
        """创建新的 A2A Task，初始化 Hermes Agent。"""
        agent = AIAgent(
            model=...,
            max_iterations=90,
            enabled_toolsets=_TOOLSETS,
            quiet_mode=True,
            platform="a2a",
        )
        self._hermes_agents[task_id] = agent
        task = Task(
            id=task_id,
            status=TaskStatus(state=TaskState.WORKING),
            kind="task",
        )
        await self.task_store.upsert_task(task)
        return task

    async def process_message(
        self, task_id: str, message: str
    ) -> A2AMessage:
        """
        将 A2A 消息送入 Hermes Agent 处理。
        使用 asyncio.to_thread() 桥接同步 → 异步。
        """
        agent = self._hermes_agents[task_id]
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, agent.run_conversation, message
        )
        return hermes_to_a2a_response(
            result["final_response"], task_id
        )

    async def get_task_status(self, task_id: str) -> Task:
        """获取 task 当前状态和历史。"""
        return await self.task_store.get_task(task_id)

    async def cancel_task(self, task_id: str) -> None:
        """取消 A2A Task，中断 Hermes Agent。"""
        agent = self._hermes_agents.get(task_id)
        if agent:
            agent._interrupt_requested = True
        task = await self.task_store.get_task(task_id)
        task.status.state = TaskState.CANCELED
        await self.task_store.upsert_task(task)
```

#### workagent/backend/a2a_server.py — A2A Server

```python
import asyncio, uvicorn
from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from starlette.applications import Starlette

class HermesA2AServer:
    """
    将 Hermes Agent 暴露为 A2A 协议的 HTTP Server。
    
    启动流程:
    1. 构建 AgentCard (从 Hermes 配置/能力自动生成)
    2. 创建 TaskStore (内存 or SQLite)
    3. 创建 HermesA2ATaskManager 作为请求处理器
    4. 挂载 A2A JSON-RPC 路由到 Starlette
    5. 启动 uvicorn
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 8080,
        task_store: TaskStore | None = None,
        agent_card: AgentCard | None = None,
    ):
        self.host = host
        self.port = port
        self.task_store = task_store or InMemoryTaskStore()
        self.task_manager = HermesA2ATaskManager(task_store)
        self.agent_card = agent_card or self._build_default_card()

    def _build_default_card(self) -> AgentCard:
        """从 Hermes 配置构建 AgentCard。"""
        from .card import build_agent_card
        return build_agent_card(
            name="Hermes Agent",
            url=f"http://{self.host}:{self.port}/",
        )

    def _make_handler(self) -> DefaultRequestHandler:
        """创建 A2A 请求处理器。"""
        from .handler import HermesRequestHandler
        return HermesRequestHandler(
            agent_executor=self.task_manager,
            task_store=self.task_store,
        )

    def build_app(self) -> Starlette:
        """构建 Starlette 应用。"""
        handler = self._make_handler()
        app = A2AStarletteApplication(
            agent_card=self.agent_card,
            http_handler=handler,
        )
        return app

    def run(self):
        """启动 uvicorn 服务器。"""
        app = self.build_app()
        uvicorn.run(
            app,
            host=self.host,
            port=self.port,
            log_level="info",
        )
```

#### workagent/backend/a2a/handler.py — 自定义请求处理器

```python
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.types import (
    SendMessageRequest, SendMessageResponse, SendMessageSuccessResponse,
    GetTaskRequest, GetTaskResponse, GetTaskSuccessResponse,
    CancelTaskRequest, CancelTaskResponse, CancelTaskSuccessResponse,
    Task, TaskState, TaskStatus,
)

class HermesRequestHandler(DefaultRequestHandler):
    """
    继承 A2A DefaultRequestHandler，用 Hermes Agent 替代默认的 ADK executor。
    
    A2A 协议核心方法:
    - message/send    — 发送消息，执行任务
    - tasks/get       — 查询任务状态
    - tasks/cancel    — 取消任务
    - tasks/pushNotification/get/set/delete  — 推送通知配置
    """

    async def handle_message_send(
        self, request: SendMessageRequest
    ) -> SendMessageResponse:
        """处理 A2A message/send 请求。"""
        params = request.params
        task_id = params.id  # 客户端提供的 task_id 或自动生成
        message = params.message

        # 1. 转换 A2A Message → Hermes 消息字符串
        user_input = a2a_to_hermes(message)

        # 2. 通过 Hermes Agent 处理
        a2a_response = await self.task_manager.process_message(
            task_id, user_input
        )

        # 3. 更新 Task 状态
        task = await self.task_store.get_task(task_id)
        task.status.state = TaskState.COMPLETED
        task.history = [message, a2a_response]
        await self.task_store.upsert_task(task)

        # 4. 返回 A2A 响应
        return SendMessageResponse(
            result=SendMessageSuccessResponse(
                task=task,
                message=a2a_response,
            )
        )

    async def handle_get_task(
        self, request: GetTaskRequest
    ) -> GetTaskResponse:
        """处理 tasks/get 请求 — 查询任务状态和历史。"""
        task = await self.task_store.get_task(request.params.id)
        return GetTaskResponse(
            result=GetTaskSuccessResponse(task=task)
        )
```

#### workagent/backend/a2a/client.py — A2A 客户端

```python
from a2a.client import A2AClient
from a2a.types import (
    SendMessageRequest, SendMessageSuccessResponse,
    Message, MessageSendParams, TextPart, Role, TaskIdParams,
    AgentCard,
)
import httpx

class HermesA2AClient:
    """
    封装 A2A 客户端，提供 Hermes CLI 友好的接口。
    """

    def __init__(self, url: str):
        self.url = url.rstrip("/") + "/"
        self.httpx_client = httpx.AsyncClient(base_url=self.url)
        self.a2a_client: A2AClient | None = None
        self._resolved_card: AgentCard | None = None

    async def resolve_card(self) -> AgentCard:
        """获取远程 Agent 的 AgentCard。"""
        if not self._resolved_card:
            from a2a.client import A2ACardResolver
            resolver = A2ACardResolver(self.httpx_client)
            self._resolved_card = await resolver.resolve_card()
            self.a2a_client = A2AClient(
                httpx_client=self.httpx_client,
                agent_card=self._resolved_card,
            )
        return self._resolved_card

    async def send_message(
        self,
        text: str,
        task_id: str | None = None,
        session_id: str | None = None,
    ) -> SendMessageSuccessResponse:
        """发送消息到远程 A2A Agent。"""
        await self.resolve_card()

        import uuid
        params = MessageSendParams(
            id=task_id or str(uuid.uuid4()),
            message=Message(
                role=Role.USER,
                parts=[TextPart(text=text)],
            ),
            session_id=session_id,
        )
        request = SendMessageRequest(params=params)
        response = await self.a2a_client.send_message(request)
        return response.result

    async def close(self):
        await self.httpx_client.aclose()
```

---

## 5. CLI 入口点集成

### 5.1 在 `hermes_cli/main.py` 中扩展 `workagent` parser

```python
# 在现有 workagent_parser 上追加
workagent_parser.add_argument(
    "--module",
    choices=["server", "a2a"],
    default="server",
    help="Select workagent service module (default: server)",
)

# a2a 模块参数
workagent_parser.add_argument("--name", help="Agent name for AgentCard")
workagent_parser.add_argument("--description", help="Agent description for AgentCard")
workagent_parser.add_argument("--card", help="Path to AgentCard JSON file")
workagent_parser.add_argument("--db", help="SQLite path for task persistence")
workagent_parser.add_argument("--streaming", action="store_true")
workagent_parser.add_argument("--workers", type=int, default=4)
```

### 5.2 `cmd_workagent()` 分发逻辑

```python
def cmd_workagent(args):
    module = getattr(args, "module", "server")

    if module == "server":
        _validate_server_args(args)
        _run_workagent_server(args)
    elif module == "a2a":
        _validate_a2a_args(args)
        from workagent.backend.a2a_server import start_a2a_server
        start_a2a_server(
            host=args.host,
            port=args.port,
            allow_public=getattr(args, "insecure", False),
            name=args.name,
            description=args.description,
            card_path=args.card,
            db_path=args.db,
            streaming=args.streaming,
            workers=args.workers,
        )
    else:
        print("usage: hermes workagent --module {server,a2a}")
```

### 5.3 兼容性要求

- `hermes workagent` 不带 `--module` 时必须保持当前行为
- 现有 `--status`、`--stop` 仍由 `cmd_workagent()` 顶层处理
- A2A 相关依赖仅在 `--module a2a` 时导入
- `server` 和 `a2a` 的 PID/状态展示沿用 `hermes workagent` 统一入口

---

## 6. 任务/会话状态管理

### 6.1 状态映射

| A2A TaskState | Hermes 对应状态 | 含义 |
|---------------|----------------|------|
| `submitted` | — | 任务已创建，待处理 |
| `working` | Agent 运行中 | `run_conversation()` 正在执行 |
| `completed` | 响应已返回 | 任务完成，有最终响应 |
| `failed` | 异常/错误 | Agent 执行出错 |
| `canceled` | `_interrupt_requested = True` | 用户取消 |
| `input-required` | — | Agent 需要更多输入 (迭代间暂停) |
| `unknown` | — | 异常状态 |

### 6.2 持久化方案

```python
from a2a.server.tasks import DatabaseTaskStore
from sqlalchemy.ext.asyncio import create_async_engine

# 默认: 内存 store (开发/轻量)
# 可选: SQLite (生产)
engine = create_async_engine("sqlite+aiosqlite:///path/to/a2a_tasks.db")
task_store = DatabaseTaskStore(engine=engine)
```

---

## 7. 安装依赖

在 Hermes 的 `pyproject.toml` 或 setup.cfg 中添加可选依赖组:

```toml
[project.optional-dependencies]
a2a = [
    "google-adk>=2.1.0",
    "uvicorn>=0.20.0",
    "starlette>=0.30.0",
    "httpx>=0.25.0",
]
```

安装方式: `pip install "hermes-agent[a2a]"` 或 `uv pip install "hermes-agent[a2a]"`

运行时懒加载: 只有执行 `hermes workagent --module a2a` 时才会导入，不影响正常使用。

---

## 8. 路线图

### Phase 1 — 基础版 (MVP)

| 功能 | 描述 | 优先级 |
|------|------|--------|
| `hermes workagent --module a2a` | 启动 A2A server, 单次消息/单任务 | P0 |
| `cmd_workagent` 模块分发 | `--module` 参数解析与校验 | P0 |
| AgentCard 生成/加载 | 默认生成与外部 card 文件覆盖 | P0 |
| Message ↔ Hermes 转换 | TextPart 双向转换 | P0 |
| Task 状态管理 | submitted→working→completed | P0 |

### Phase 2 — 增强

| 功能 | 描述 | 优先级 |
|------|------|--------|
| 会话保持 (context_id) | 多轮对话上下文 | P1 |
| Task 持久化 (SQLite) | 重启后恢复任务 | P1 |
| AgentCard 自定义 | 从配置/技能自动生成 | P1 |
| 多任务并发 | 支持多个 Task 同时处理 | P1 |
| A2A client 工具 | 获取远程 AgentCard / 调试联通性 | P1 |

### Phase 3 — 进阶

| 功能 | 描述 | 优先级 |
|------|------|--------|
| 流式响应 (SSE) | `send_message_streaming` | P2 |
| 推送通知 | `pushNotification/set` | P2 |
| 文件传输 | FilePart 支持 (FileWithUri / FileWithBytes) | P2 |
| DataPart 支持 | 结构化数据传输 | P2 |
| 安全认证 | OAuth2/Bearer token/APIKey 支持 | P2 |
| A2A Registry | 服务发现/注册 | P3 |

---

## 9. 潜在风险与注意事项

| 风险 | 说明 | 缓解措施 |
|------|------|---------|
| `run_conversation()` 同步阻塞 | A2A server 是 async，blocking 会阻塞整个事件循环 | `asyncio.to_thread()` + 独立线程池 |
| max_iterations 耗尽 | 任务可能卡住 | 设置超时，TaskState.failed 回退 |
| 上下文切换开销 | 每次 A2A send 都新建 Agent 实例 | 复用 task_id → AIAgent 缓存 |
| Agent 状态共享 | 工具调用可能产生副作用 | 每个 task 独立 Hermes 会话 |
| 依赖体积 | google-adk[a2a] 可能较大 | 作为 optional dependency |
| 版本兼容 | a2a 包协议版本更新 | 使用 a2a.types 的 protocol_version 字段协商 |

---

## 10. 通过 run_conversation() 操作 Loop 中的消息和工具调用

这是方案最关键的设计问题。`run_conversation()` 提供了 **多个注入点（hook points）**，可以精细控制 loop 中的消息流和工具调用结果。

### 10.1 参数层面的控制点

```
run_conversation(agent, user_message, system_message, conversation_history, task_id, ...)
```

| 参数 | A2A 场景用法 |
|------|------------|
| `conversation_history` | **关键！** 将 A2A Task.history 中的过往 Message 转换为 OpenAI 格式后注入。每轮 A2A send 都追加之前的历史，实现连续对话 |
| `system_message` | 注入 A2A 协议上下文提示（"You are an A2A agent responding to a remote agent..."），或控制 Agent 的行为边界 |
| `user_message` | 将 A2A TextPart.text 提取为字符串传入 |
| `task_id` | 用 A2A task ID 作为隔离键，确保不同 A2A Task 工具调用互不干扰 |
| `stream_callback` | 将流式响应转发到 A2A SendStreamingMessage SSE 通道 |

### 10.2 Loop 内部的 7 个操作点

以下是 `run_conversation()` 内部的顺序化操作点（代码行号对应 conversation_loop.py）：

```
   run_conversation() entry
         │
         ▼
  ┌──────────────────────────────────────┐
  │① conversation_history → messages     │  ← 注入 A2A 历史消息 (L496)
  │   messages = list(conversation_history)│
  └──────────────────────────────────────┘
         │
         ▼
  ┌──────────────────────────────────────┐
  │② step_callback — 每轮迭代触发        │  ← 检查工具结果，决定是否 (L824)
  │   agent.step_callback(iter, tools)   │     继续或终止 loop
  └──────────────────────────────────────┘
         │
         ▼
  ┌──────────────────────────────────────┐
  │③ /steer 注入 — 在工具结果后写入       │  ← 在 tool message 中注入额外 (L857-905)
  │   追加到最后一个 tool 消息的 content   │     文本（类似 A2A input-required）
  └──────────────────────────────────────┘
         │
         ▼
  ┌──────────────────────────────────────┐
  │④ prefill_messages 注入               │  ← 合成消息预填 (L1008)
  │   预置在 messages 列表开头的消息       │
  └──────────────────────────────────────┘
         │
         ▼
  ┌──────────────────────────────────────┐
  │⑤ pre_api_request 插件钩子             │  ← 每次 API 调用前可修改 (L1235)
  │   暴露 request_messages 给插件系统     │     请求消息体
  └──────────────────────────────────────┘
         │
         ▼
  ┌──────────────────────────────────────┐
  │⑥ tool call 结果注入                   │  ← 每个工具结果 append 到 (L3730-3756)
  │   messages.append({role:"tool",...})  │     messages 前可拦截
  └──────────────────────────────────────┘
         │
         ▼
  ┌──────────────────────────────────────┐
  │⑦ 响应 final_response + messages      │  ← 转换为 A2A Message (L4268)
  └──────────────────────────────────────┘
```

### 10.3 两种操作策略

#### 策略 A：注入 System Prompt + Conversation History（零侵入，推荐 Phase 1）

原理：完全通过 `run_conversation()` 的参数控制，不修改 Hermes 内部代码。

```python
A2A_SYSTEM_CONTEXT = """
You are running inside an A2A (Agent-to-Agent) protocol session.
- The remote agent may use structured data.
- Tool execution results are visible to the calling agent.
- Keep responses concise and machine-readable where appropriate.
"""

def prepare_a2a_turn(
    a2a_msg: A2AMessage,
    task_history: list[A2AMessage],
    remote_card: AgentCard | None = None,
) -> tuple[str, list[dict], str]:
    """
    将 A2A Message 转换为 run_conversation() 的三个核心参数。
    无需修改 Hermes 内部代码。
    """
    # 系统提示
    system = A2A_SYSTEM_CONTEXT
    if remote_card:
        system += (
            f"\nRemote Agent Card:\n"
            f"  Name: {remote_card.name}\n"
            f"  Description: {remote_card.description}\n"
            f"  Skills: {', '.join(s.name for s in remote_card.skills)}\n"
        )

    # 历史消息转换
    history = []
    for msg in task_history:
        role = "user" if msg.role == Role.USER else "assistant"
        text = " ".join(
            p.text for p in msg.parts if isinstance(p, TextPart)
        )
        history.append({"role": role, "content": text})

    # 当前消息
    user_text = " ".join(
        p.text for p in a2a_msg.parts if isinstance(p, TextPart)
    )

    return system, history, user_text


# 使用方式 — 一行调用 run_conversation()
system, history, user_text = prepare_a2a_turn(
    incoming_msg, a2a_task.history, remote_card
)
result = agent.run_conversation(
    user_message=user_text,
    system_message=system,
    conversation_history=history,
    task_id=a2a_task_id,
)
```

#### 策略 B：包裹 handle_function_call（Flexible，Phase 2 升级）

原理：在 tool dispatch 点包裹一层，拦截/修改工具调用结果，实现 A2A 协议感知。

```python
class A2AToolDispatcher:
    """
    封装 handle_function_call，在 tool dispatch 点插入 A2A 逻辑。
    
    拦截点（对应 conversation_loop.py 第 3730-3756 行的工具结果注入路径）：
    - 调用前: 检查是否是 A2A 协议元调用
    - 调用后: 将工具结果写入 A2A Task artifacts
    - 错误时: 优雅降级，不中断 loop
    """

    def __init__(self, task_id: str, task_store):
        self.task_id = task_id
        self.task_store = task_store

    def dispatch(
        self,
        function_name: str,
        function_args: dict,
        tool_call_id: str,
        enabled_tools: list[str] | None = None,
    ) -> str:
        """包裹 handle_function_call 的核心分发方法。"""

        # ── A2A 协议元调用 ──
        if function_name == "_a2a_status":
            return json.dumps({
                "task_id": self.task_id,
                "status": "working",
            })
        elif function_name == "_a2a_card":
            return json.dumps({
                "name": "Hermes Agent",
                "skills": [s.name for s in agent_card.skills],
            })

        # ── 正常工具调用 ──
        try:
            result = handle_function_call(
                function_name=function_name,
                function_args=function_args,
                task_id=self.task_id,
                tool_call_id=tool_call_id,
                enabled_tools=enabled_tools,
            )
            # 后台记录工具结果到 A2A Task artifacts
            asyncio.create_task(
                self._record_artifact(function_name, result)
            )
            return result
        except Exception as e:
            # 返回错误但保持 loop 继续
            return json.dumps({
                "error": str(e),
                "a2a_task": self.task_id,
            })

    async def _record_artifact(self, name: str, result: str):
        """将工具结果记录为 A2A Artifact。"""
        try:
            task = await self.task_store.get_task(self.task_id)
            task.artifacts = task.artifacts or []
            task.artifacts.append(Artifact(
                name=f"tool:{name}",
                parts=[TextPart(text=result[:4096])],
            ))
            await self.task_store.upsert_task(task)
        except Exception:
            pass
```

### 10.4 策略 A vs 策略 B 对比

| 对比项 | 策略 A (参数注入) | 策略 B (Tool 包裹) |
|--------|-----------------|-------------------|
| 代码侵入 | **零侵入** — 仅使用 `run_conversation()` 参数 | 需要包裹 `handle_function_call` |
| 灵活性 | 受限于模型的遵循度 | 完全可控工具结果 |
| 可靠性 | 模型可能忽略指令 | 确定性行为 |
| 实现复杂度 | 极低（~50 行转换代码） | 较高（需理解 tool dispatch 链） |
| 工具结果监控 | ❌ 依赖模型自行报告 | ✅ 可以记录 artifacts |
| 特殊协议命令 | ❌ 不可行 | ✅ 支持 `_a2a_status` 等 |
| Phase 适用 | **Phase 1 MVP** | **Phase 2 增强** |

### 10.5 推荐：Phase 1 用策略 A → Phase 2 升级到策略 B

**Phase 1 (2 天)** — 策略 A，纯参数注入：
- 通过 `system_message` 告知模型在 A2A 上下文工作
- 通过 `conversation_history` 传递对话历史
- 通过 `task_id` 隔离不同 A2A Task
- 零侵入，快速验证端到端通信

**Phase 2 (3 天)** — 策略 B，tool dispatch 包裹：
- 在 `A2ARequestHandler.process_message()` 中实例化 `A2AToolDispatcher`
- 替换 loop 内的 `handle_function_call()` 调用为 `dispatcher.dispatch()`
- 工具结果自动记录到 A2A Task artifacts
- 支持 A2A 协议元调用 (get_card, ping 等)
- 多轮迭代间的任务状态同步

### 10.6 角色交替约束

`run_conversation()` 强制 OpenAI-format 的角色交替约束：

```
user → assistant → tool → assistant → tool → ... → assistant  # 不允许 user→user 或 assistant→assistant
```

对 A2A 的影响：
- `Role.USER` → `"user"`, `Role.AGENT` → `"assistant"`
- 工具结果用 `"role": "tool"` — 这是 **策略 B 注入自定义结果的合法通道**
- A2A 的 `input-required` 场景：可以通过 `_interrupt_requested` 暂停 loop，下一轮 A2A `send` 通过 `conversation_history` 恢复

---

## 11. 总结

**Hermes 的 `AIAgent.run_conversation()` 完全可复用**作为 A2A 协议的执行引擎。核心工作是基于其已有的 **7 个内部操作点**，通过参数注入和可选的 tool dispatch 包裹来实现灵活处理：

- **Phase 1 (纯参数注入)**：利用 `conversation_history`、`system_message`、`task_id` 三个参数，零代码侵入实现 A2A 消息循环
- **Phase 2 (Tool 包裹)**：在 `handle_function_call` 外层包裹 `A2AToolDispatcher`，实现对工具结果的完全控制，包括 artifacts 记录、元调用处理、优雅降级

**整体工作量：**
- 新增 ~800 行核心代码 (`a2a_adapter/`)
- 修改 ~50 行 CLI 代码 (`hermes_cli/main.py`)
- 依赖: `pip install google-adk[a2a]`

后续如需支持更高级功能 (streaming、FilePart、认证)，可在 Phase 2/3 中增量扩展。建议先实现 MVP 版本 (Phase 1)，验证端到端通信能力后再逐步增强。
