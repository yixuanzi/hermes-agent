# Aegis A2A Work Agent 服务接入参考

本文面向需要接入 Aegis 的远程 Work Agent 开发者，说明 Aegis 当前使用的 A2A 通信方式、Agent Card 能力声明、认证以及两种服务端实现路径。Workagent 的 Hermes 审批/澄清交互扩展另见 [`aegis/docs/a2a-delegate-interaction.md`](../../docs/a2a-delegate-interaction.md)；服务端协议见 [`workagent/backend/docs/a2a-interaction-extension.md`](../../../workagent/backend/docs/a2a-interaction-extension.md)。

适用日期：2026-08-21。

## 1. 兼容性基线

| 项目 | Aegis 当前基线 |
| --- | --- |
| A2A SDK | `a2a-sdk[fastapi]==1.1.0` |
| A2A 协议版本 | `1.0` |
| 首选传输绑定 | HTTP 上的 JSON-RPC（`JSONRPC`） |
| 消息模式 | `text/plain` 输入与输出 |
| 任务模式 | `SendMessage` 后取得 Task；长任务通过 `GetTask` 轮询；支持 `CancelTask` |
| 流式能力 | 服务端可声明并启用；Aegis 委派端具备 streaming/polling 客户端配置，但当前常规委派以 Task 轮询完成结果为准 |

本项目使用的 SDK 与官方 A2A Protocol `1.0` 对齐；官方 Python SDK 同时提供 `0.3` 兼容模式。新建 Work Agent 应声明 `1.0`，不要将旧版 `0.3` 的字段结构当作默认实现。[A2A Protocol Specification](https://a2a-protocol.org/latest/specification/)、[官方 Python SDK 兼容性说明](https://github.com/a2aproject/a2a-python)

## 2. Aegis 如何发现和调用 Agent

```text
管理员在 Aegis 登记 Agent
  └─ <HERMES_HOME>/a2a.json
       └─ Aegis 读取 active Agent
            ├─ GET {登记 URL}/.well-known/agent-card.json
            ├─ 合并 Agent Card skills + Aegis extcapabilities
            └─ 以登记 URL 初始化 A2A SDK 客户端，由 SDK 按 Card 解析远端接口
                 ├─ SendMessage
                 ├─ GetTask（直到终态）
                 └─ CancelTask（用户停止时）
```

### 2.1 Aegis 注册表约定

在 Aegis 的 **Agent Orchestration（工作智能体编排）** 页面登记后，会写入 `<HERMES_HOME>/a2a.json`。等价的结构如下：

```json
{
  "a2a": {
    "incident-responder": {
      "url": "https://work-agent.example.com/a2a",
      "description": "处置安全事件并提供证据链。",
      "headers": {
        "Authorization": "Bearer <work-agent-token>"
      },
      "status": "active",
      "extcapabilities": [
        "支持隔离、封禁和工单编排"
      ]
    }
  },
  "global": []
}
```

约定与限制：

- 仅 `status: "active"` 的条目会被 Aegis 的 `/a2a` 上下文及 Agents 弹窗发现。
- `url` 是 Agent 的基础 A2A URL；建议登记实际 JSON-RPC 地址，如 `https://host.example/a2a`。
- Aegis 当前会请求 `{url}/.well-known/agent-card.json`。例如登记 `https://work-agent.example.com/a2a` 时，实际探测路径是 `https://work-agent.example.com/a2a/.well-known/agent-card.json`。AISOC 同时发布根路径和 `/a2a/.well-known/agent-card.json`，便于 Aegis 与标准客户端兼容。
- Aegis 以登记的 `url` 初始化官方 A2A SDK 客户端；Card 的首个 `supportedInterfaces[].url` 仍应是服务首选、可从 Aegis 网络访问的标准接口。
- `headers` 会原样传给 Agent Card 探测和后续 A2A SDK HTTP 请求。当前 `a2a.json` 为本地明文配置，不应提交到版本库；应限制 `<HERMES_HOME>` 的文件权限，并通过密钥管理系统分发生产 token。

## 3. 标准 Agent Card 与能力配置

A2A 服务必须提供 Agent Card。标准根发现 URI 为：

```text
GET https://<agent-host>/.well-known/agent-card.json
```

为兼容 Aegis 当前的 `{登记 URL}/.well-known/agent-card.json` 探测，应额外在 A2A RPC 路径下发布同一份 Card；例如同时提供：

```text
GET https://<agent-host>/.well-known/agent-card.json
GET https://<agent-host>/a2a/.well-known/agent-card.json
```

Card 是公开的能力描述，不应放入 token、内部主机名、系统提示词或其他敏感数据。A2A 规范要求在 Card 中声明支持的接口；第一个 `supportedInterfaces` 条目是首选接口。[Agent Discovery](https://a2a-protocol.org/latest/topics/agent-discovery/)、[A2A Agent Card 要求](https://a2a-protocol.org/latest/specification/#8-agent-discovery-the-agent-card)

以下是与 Aegis 兼容的 A2A `1.0` JSON-RPC Card 示例。字段采用规范的 JSON camelCase：

```json
{
  "name": "incident-responder",
  "description": "调查、遏制并记录安全事件的远程 Work Agent。",
  "version": "0.2.3",
  "capabilities": {
    "streaming": false,
    "pushNotifications": false
  },
  "defaultInputModes": ["text/plain"],
  "defaultOutputModes": ["text/plain"],
  "supportedInterfaces": [
    {
      "url": "https://work-agent.example.com/a2a",
      "protocolBinding": "JSONRPC",
      "protocolVersion": "1.0"
    }
  ],
  "skills": [
    {
      "id": "incident-investigation",
      "name": "事件调查",
      "description": "关联 IOC、告警、资产和认证日志，并输出证据化结论。",
      "tags": ["soc", "investigation", "ioc"],
      "examples": ["调查此高危登录告警", "检查这个域名是否与攻击活动相关"]
    },
    {
      "id": "containment",
      "name": "响应处置",
      "description": "按授权范围生成隔离、封禁或工单处置建议。",
      "tags": ["response", "containment"]
    }
  ]
}
```

### 3.1 Aegis 如何展示能力

Aegis 会对每一个 `skills[]` 条目抽取 `id`、`name`、`description`、`tags` 和 `examples`，并合并为一条可读能力描述；随后再与注册表的 `extcapabilities` 去重合并。它们会出现在：

- Chat `/a2a` 返回的 `<aegis_context>`；
- Chat 页面 **CENTRAL ARCHIVE → Agents** 悬浮窗；
- 模型选择合适远程 Agent 时可读取的上下文。

因此，应把用户可理解、可路由的业务能力写入 `skills`，把仅 Aegis 本地补充的能力写入 `extcapabilities`。避免把能力只放在 `description`，也不要用一个包含所有功能的泛化 Skill。

## 4. 认证与传输安全

A2A 使用常规企业 Web 认证机制；Agent Card 可声明标准安全方案，客户端据此在带外获取凭据。生产环境必须使用 HTTPS/TLS，客户端应校验服务器证书。[A2A Authentication and Authorization](https://a2a-protocol.org/latest/specification/#7-authentication-and-authorization)

### 4.1 当前 Aegis 互通做法：Bearer Token

当前 Aegis 对远程 Work Agent 支持最直接的方式是静态 HTTP Bearer：

```text
Authorization: Bearer <token>
```

在 Aegis 管理界面创建/编辑 Agent 时填写：

| Aegis 字段 | 示例 |
| --- | --- |
| 验证头字段 | `Authorization` |
| 验证字段值 | `Bearer <token>` |

这组 Header 会同时用于读取 Agent Card 和调用 JSON-RPC/任务接口。

### 4.2 Hermes AISOC A2A 服务端认证

AISOC 是仓库内可直接复用的服务端实现。默认不要求认证；启用后，Card 与健康检查继续公开，A2A RPC/Task 请求需要 Bearer Token：

```bash
export AISOC_A2A_AUTH=true
export A2A_SESSION_TOKEN='replace-with-a-long-random-secret'
hermes aisoc --module a2a --host 0.0.0.0 --port 9086 --insecure
```

随后在 Aegis 中登记：

```text
A2A 地址： https://work-agent.example.com/a2a
验证头字段： Authorization
验证字段值： Bearer replace-with-a-long-random-secret
```

注意：

- 若 `AISOC_A2A_AUTH=true` 但未设置 `A2A_SESSION_TOKEN`，AISOC 会在启动时生成进程内随机 token；重启后 token 可能变化，不适合需要 Aegis 长期自动调用的生产服务。
- `AISOC_SESSION_TOKEN` 是 AISOC Web Server 模块的 token，不适用于 `--module a2a`。
- `AISOC_A2A_ADMIN_TOKEN` 只保护 AISOC 的 `/man` 管理面，不能与 A2A 通信 token 复用，也不能把它登记到 Aegis。
- 服务暴露到非 loopback 地址时，AISOC 要求显式 `--insecure`；生产环境应在反向代理或入口网关后使用 HTTPS、访问控制、日志脱敏与 token 轮换。

### 4.3 Agent Card 的认证声明

当前 Aegis 会透传在注册表填写的 Header，但不会自动执行 OAuth/OIDC 授权码流或 mTLS 凭据协商。若 Work Agent 需要这些机制：

1. 仍应在标准 Card 的 `securitySchemes` 中正确声明；
2. 在 Aegis 当前版本中，通过入口网关将 Aegis 可配置的静态 Header 换取/校验为后端所需凭据，或扩展 Aegis 的认证提供器；
3. 不要在 Card 中写入任何 credential。

## 5. 实现路径 A：直接使用 Hermes AISOC

这是对 Hermes Work Agent 的推荐路径。AISOC 已经实现 Agent Card、JSON-RPC 路由、Task Store、Task 生命周期、取消、文本消息转换和 Hermes 会话桥接。

### 5.1 安装和启动

```bash
# 在 Hermes Agent 仓库环境中安装 A2A extra
pip install -e '.[a2a]'

# 本地开发：仅 loopback
hermes aisoc --module a2a --host 127.0.0.1 --port 9086 \
  --name incident-responder \
  --description '调查和处置安全事件的 Work Agent'

# 对外服务：必须有反向代理/TLS，并显式允许非 loopback 绑定
export AISOC_A2A_AUTH=true
export A2A_SESSION_TOKEN='replace-with-a-long-random-secret'
hermes aisoc --module a2a --host 0.0.0.0 --port 9086 --insecure \
  --name incident-responder \
  --description '调查和处置安全事件的 Work Agent'
```

常用参数：

- `--name`、`--description`：覆盖自动生成 Agent Card 的身份字段；
- `--streaming`：在 Card 中声明 streaming，并让 Hermes executor 发出 working 状态更新；
- `--card /absolute/path/agent-card.json`：完全使用指定的 Agent Card JSON；
- `A2A_BASE_PATH=/a2a`：改变 JSON-RPC 基础路径；默认就是 `/a2a`。

AISOC 会公开下列路径：

```text
GET  /health
GET  /.well-known/agent-card.json
GET  /a2a/.well-known/agent-card.json
POST /a2a                         # A2A JSON-RPC
```

### 5.2 使用自定义 Card 发布能力

将第 3 节示例保存为 `agent-card.json`，再启动：

```bash
export AISOC_A2A_AUTH=true
export A2A_SESSION_TOKEN='replace-with-a-long-random-secret'
hermes aisoc --module a2a --host 127.0.0.1 --port 9086 \
  --card ./agent-card.json
```

Card 中 `supportedInterfaces[0].url` 必须是外部调用者可访问的地址。若服务在反向代理后运行，请填写代理的 HTTPS 公网/内网地址，而不是容器内 `127.0.0.1`。

### 5.3 验证

```bash
# Card 必须能被 Aegis 读取（登记 URL 是 https://work-agent.example.com/a2a）
curl -fsS https://work-agent.example.com/a2a/.well-known/agent-card.json | jq .

# 同时保留标准根发现 URI，供其他 A2A 客户端使用
curl -fsS https://work-agent.example.com/.well-known/agent-card.json | jq .

# 启用认证时，RPC 应拒绝无 token 请求
curl -i -X POST https://work-agent.example.com/a2a \
  -H 'Content-Type: application/json' \
  --data '{"jsonrpc":"2.0","id":"probe","method":"SendMessage","params":{}}'
```

仓库也提供基于官方 Python SDK 的协议烟测脚本：

```bash
python scripts/a2a_smoke_test.py \
  --base-url https://work-agent.example.com \
  --auth-token "$A2A_SESSION_TOKEN"
```

## 6. 实现路径 B：使用标准 A2A Python SDK

不使用 Hermes 时，建议直接采用官方 `a2a-sdk[fastapi]`，而不是手写 JSON-RPC、Task 状态机和 Agent Card 路由。下面示例展示必须拼接的服务端组件；业务逻辑只需替换 `WorkExecutor`。

```python
from fastapi import FastAPI
from a2a.helpers.proto_helpers import new_task_from_user_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import add_a2a_routes_to_fastapi, create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, Part
from a2a.utils.constants import PROTOCOL_VERSION_CURRENT, TransportProtocol


class WorkExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            context.current_task = task
            await event_queue.enqueue_event(task)

        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.start_work()

        # 读取 context.message.parts，调用你的 Agent，并生成最终文本。
        final_text = "work agent result"
        reply = updater.new_agent_message([Part(text=final_text)])
        await updater.complete(reply)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        # 停止正在运行的业务任务，并报告标准取消状态。
        task = context.current_task
        if task is None:
            return
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.cancel(updater.new_agent_message([Part(text="Canceled")]))


app = FastAPI(title="Example Work Agent")
card = AgentCard(
    name="example-work-agent",
    description="Example A2A Work Agent",
    version="0.2.3",
    capabilities=AgentCapabilities(streaming=False, push_notifications=False),
    default_input_modes=["text/plain"],
    default_output_modes=["text/plain"],
    supported_interfaces=[
        AgentInterface(
            url="https://work-agent.example.com/a2a",
            protocol_binding=TransportProtocol.JSONRPC,
            protocol_version=PROTOCOL_VERSION_CURRENT,
        )
    ],
    skills=[],  # 按第 3 节补充可路由能力
)
handler = DefaultRequestHandler(
    agent_executor=WorkExecutor(),
    task_store=InMemoryTaskStore(),
    agent_card=card,
)
add_a2a_routes_to_fastapi(
    app,
    agent_card_routes=create_agent_card_routes(card),
    jsonrpc_routes=create_jsonrpc_routes(handler, rpc_url="/a2a"),
)
```

生产实现还必须：

1. 在 HTTP 中间件或网关中验证认证 Header，并让 Card 路径按你的公开/受保护策略可访问；
2. 以持久化 Task Store 替代示例中的 `InMemoryTaskStore`，避免重启丢失长任务；
3. 为业务执行设置超时、并发上限、取消传播、审计日志和幂等策略；
4. 正确维护 `TaskState`：至少处理 `working`、`completed`、`failed`、`canceled`、`input_required`、`rejected` 与 `auth_required`；
5. 使用 HTTPS，并让 Card 内接口 URL 与调用者实际可达的入口一致。

## 7. 接入检查清单

- [ ] 服务使用 A2A `1.0`，Card 的首个 `supportedInterfaces` 为 `JSONRPC` / `1.0`。
- [ ] 标准根 Card 与 `{登记 URL}/.well-known/agent-card.json` 都返回有效 JSON，且其中没有 secrets。
- [ ] Card 的首个接口 URL 可从 Aegis 后端网络访问。
- [ ] Agent 在 Aegis 中登记为 `active`，URL 与 Card 入口一致。
- [ ] 若启用认证，Aegis 的 `Authorization: Bearer …` Header 与服务端 token 一致。
- [ ] 服务端正确处理 Send、Task 查询和 Cancel；长任务最终进入明确终态。
- [ ] `skills` 描述了可被路由的细粒度能力；必要时补充 Aegis `extcapabilities`。
- [ ] 通过 `scripts/a2a_smoke_test.py` 和 Aegis Chat `/a2a` / Agents 悬浮窗验证发现、能力显示和委派。

## 8. 当前 Aegis 边界

- Aegis 当前支持的是**远程 Agent 通讯与委派**，不是将 Agent Card 中的 Skill 自动注册为本地 Hermes tool。
- Card 读取失败会使该 Agent 保留为不可用状态，并带错误信息；不会回退成匿名/无 Card 调用。
- Aegis 从 Card 中读取能力用于上下文和界面展示；实际准入仍受 Aegis Agent Policy 与远端服务自己的授权控制。
- 当前 Aegis 只直接配置静态 HTTP Header；OAuth、OIDC、mTLS 等高级认证应由入口网关或后续认证提供器集成。
- 当远端 Card 声明 `https://hermes.dev/extensions/interaction/v1` 时，Aegis Web UI 可显示 delegate 审批/澄清并通过已认证的 Aegis WebSocket 响应；pending 状态会区分 `main` 与 `delegate` 来源。
- 当前交互扩展的服务端实现归属 `workagent/`；本次更新不改变 `aisoc/` 下的 A2A executor/server，未声明扩展的服务仍保持普通 A2A 行为和原有 fail-closed 语义。
