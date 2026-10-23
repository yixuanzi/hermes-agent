# Workagent A2A `hermes.interaction.v1`

本文是 Workagent（AISOC 升级目录）A2A 服务端的模块说明，记录远端 Agent 在审批或澄清时如何向调用方发布交互请求，以及调用方如何在保持同一 task 的情况下解除等待。

本实现只覆盖 `workagent/backend/`。`aisoc/` 下现有 A2A executor/server 在本次更新中保持不变；未声明本扩展的远端客户端继续使用原有 A2A 行为。

## 1. 模块边界

| 文件 | 责任 |
| --- | --- |
| `a2a_server.py` | 在 Agent Card 增加非强制扩展声明，注册受 A2A Bearer 认证保护的响应 endpoint。 |
| `a2a_service/executor.py` | 将 Hermes approval/clarify callback 接入 A2A task，发布 `WORKING` metadata，并在响应后继续原 agent thread。 |
| `a2a_service/interactions.py` | 维护进程内 pending/resolved/cancelled registry，校验远端响应并解除底层 wait。 |
| `tools.approval` / `tools.clarify_gateway` | 提供既有审批策略、deny/hardline 规则、超时和澄清等待语义；本扩展不替换这些安全策略。 |

调用方侧的 `tools/a2a_delegate_tool.py`、Slack/Feishu adapter 和 Aegis UI 说明分别见 [`aegis/docs/a2a_delegate_and_slack_io_adapters.md`](../../../aegis/docs/a2a_delegate_and_slack_io_adapters.md)、[`aegis/docs/a2a-delegate-interaction.md`](../../../aegis/docs/a2a-delegate-interaction.md) 和对应平台 adapter 代码。

## 2. Agent Card 声明

Workagent 的 Card JSON 在标准 A2A 能力字段之外增加：

```json
{
  "extensions": [
    {
      "uri": "https://hermes.dev/extensions/interaction/v1",
      "required": false,
      "params": {
        "response_path": "/a2a/hermes/interaction/respond",
        "steer_path": "/a2a/hermes/steer"
      }
    }
  ]
}
```

`required: false` 表示普通 A2A client 可以忽略该扩展。只有声明并实际能访问 `response_path` 的调用方才应向用户展示可点击的审批/澄清控件；客户端无法建立响应通道时必须保持 fail-closed，不得自动批准危险操作。

根 Card 和 A2A 路径 Card 都包含该声明：

```text
GET  /.well-known/agent-card.json
GET  /a2a/.well-known/agent-card.json
POST /a2a/hermes/interaction/respond
POST /a2a/hermes/steer
```

当 `A2A_BASE_PATH` 改变时，RPC path、响应 path 和 steer path 由服务配置生成，调用方应优先使用 Card 的 `params.response_path` / `params.steer_path`，不要硬编码服务端前缀。

## 3. 请求格式与 task 生命周期

审批或澄清发生时，executor 不调用 `requires_input` 结束 task，而是在当前 `TaskState.WORKING` 消息中发布 `hermes` metadata。

审批请求：

```json
{
  "hermes": {
    "kind": "approval_request",
    "interaction_id": "approval_…",
    "task_id": "…",
    "context_id": "…",
    "command": "chmod 777 ./build",
    "description": "world/other-writable permissions",
    "choices": ["once", "session", "always", "deny"],
    "allow_session": true,
    "allow_permanent": true
  }
}
```

澄清请求：

```json
{
  "hermes": {
    "kind": "clarify_request",
    "interaction_id": "clarify_…",
    "task_id": "…",
    "context_id": "…",
    "question": "请选择部署区域",
    "choices": ["cn", "us", "eu"],
    "multi_select": false
  }
}
```

同一 task 可以继续发送普通 working/tool/delta 事件，但一个 pending interaction 的 `interaction_id` 只对应一个等待。解析成功后，服务端再次发送 `TaskState.WORKING` metadata：

```json
{
  "hermes": {
    "kind": "approval_resolved",
    "interaction_id": "approval_…",
    "task_id": "…",
    "context_id": "…",
    "resolved_value": "once"
  }
}
```

随后原始 approval/clarify wait 返回，agent loop 继续执行；最终仍由同一个 A2A task 进入 `completed`、`failed` 或 `canceled`。

## 4. 响应 endpoint 与校验

调用方通过与 A2A JSON-RPC 相同的 Bearer token 调用：

```text
POST /a2a/hermes/interaction/respond
Authorization: Bearer <A2A_SESSION_TOKEN>
Content-Type: application/json
```

审批：

```json
{
  "task_id": "…",
  "context_id": "…",
  "interaction_id": "approval_…",
  "kind": "approval",
  "choice": "once"
}
```

澄清：

```json
{
  "task_id": "…",
  "context_id": "…",
  "interaction_id": "clarify_…",
  "kind": "clarify",
  "answer": "eu"
}
```

多选澄清保留数组，不应先拼成逗号分隔字符串：

```json
{"answer": ["cn", "eu"]}
```

`InteractionRegistry.resolve()` 的校验顺序和结果：

- 缺少 `task_id`、`context_id`、`interaction_id` 或 `kind`：`422`；
- 未知 interaction：`404`；
- task/context/kind 不匹配：`409`；
- 重复点击、已经 resolved、已取消或底层 wait 已结束：`409`；
- 审批 choice 不是 `once/session/always/deny`：`422`；
- 澄清答案为空，或多选数组为空/含空元素：`422`。

resolver 先进入 `resolving` 状态，再调用既有 gateway resolver；resolver 失败会恢复为 pending，底层 wait 不会被错误标记为已完成。成功后保留有限的 terminal record，使重复点击得到冲突响应而不是误报“未知 interaction”。

## 5. Executor 绑定与清理

每个 A2A `context_id` 对应一个可复用的 Hermes agent session。executor 在进入 `run_conversation()` 前：

1. 解析消息首行 `<source>`，恢复原始 `platform`、`uid` 和 `uname`；
2. 以 `context_id` 作为稳定 session key；
3. 注册 `register_gateway_notify(session_key, approval_callback)`；
4. 注入 A2A 专用 `clarify_callback`；
5. 在 callback 中登记 interaction，并把 metadata 调度回 async event loop；
6. 在当前同步 agent 线程中等待既有 approval/clarify primitive。

`_run_agent_conversation()` 使用临时身份字段：

```text
agent.platform           = "{platform}_a2a"
agent._user_env_platform = "{platform}"
agent._user_id           = uid
```

`platform` 的 `_a2a` 后缀只标识 A2A 执行 surface；`_user_env_platform` 才是 userenv binding 优先使用的原始平台。因此审批通过后继续执行 terminal，仍访问 `platform.uid` 的用户环境，不会生成 `platform_a2a.uid` 新分区。回合结束时这些临时属性和 callback 都会恢复。

以下路径都会解除等待并清理 registry：

- 用户响应：resolver 唤醒原线程，发布 `*_resolved` metadata；
- approval/clarify 超时：底层 primitive 返回拒绝/超时结果，不执行危险命令；
- A2A cancel：设置 interrupt，取消 task，清理 interaction；
- executor `finally`：注销 gateway notify、清理 clarify session、取消该 session 的 interaction，并清除 task event queue；
- 服务级清理：清空 registry，所有遗留 pending interaction 不得继续授权。

## 6. 调用方必须遵守的语义

`a2a_delegate` 从 Agent Card 读取扩展声明，并在 streaming event 和 `get_task()` polling 两条路径解析 `hermes` metadata。它按 `(interaction_id, event kind)` 去重，避免轮询期间重复发卡片；响应使用保存的 task/context/interaction ID 和 Card 中的 `response_path`。

远端未声明扩展、Card 缺少 response path、A2A Bearer 无效、响应 endpoint 不可达或 response 返回冲突时，调用方只报告交互不可用/已过期，不得改走本地 resolver，也不得把审批转换成自动执行。

Slack、Feishu 和 Aegis 的交互状态必须与普通主 Agent approval/clarify 隔离。用户点击后只发送远端 response；`approval_resolved` / `clarify_resolved` 到达后再清理 UI pending state。这样可避免“卡片已经显示但远端线程仍然等待”或重复点击触发第二次授权。

## 7. 中途引导（steer）

A2A 原生 `message/send` 不能把消息送进正在执行的 task：SDK 对同一 task 的请求串行执行，第二条消息只会在当前 `execute()` 结束后再跑一轮。因此中途引导走单独的 HTTP 端点，与交互响应使用同一套 A2A Bearer 鉴权：

```http
POST /a2a/hermes/steer
{"task_id": "<task id>", "context_id": "<context id>", "text": "只关注 X"}
```

- Executor 用 `_task_agent_keys[task_id]` 找到正在运行的 agent，校验 context 一致后调用 `agent.redirect(text)`：模型请求进行中则取消该请求并带上纠正重试；工具执行中退化为 `steer()`，等当前工具完成后附在工具结果上。agent 不支持 redirect 时直接调用 `steer()`。两者都不新开 A2A 请求。
- 成功返回 `{"ok": true, "task_id": ...}`；字段缺失 422；task 不在运行、已取消、context 不匹配或 agent 拒绝时 409。调用方收到非 2xx 必须把文本退回为下一轮输入，不得丢弃。
- 落在最后一次工具批次之后的 steer 会作为 `run_conversation` 结果的 `pending_steer` 返回。Executor 在同一个 `execute()` 内把它作为下一条用户消息继续运行（最多 3 次），task 的最终回复是最后一次运行的回复，调用方不需要新开一轮。结束前 executor 在 steer 端点使用的同一把锁下检查 agent 上是否还有未消费的 steer 并关闭该 task 的 steer 窗口：与结束竞争的 steer 要么被取出继续运行，要么收到 409 由调用方排队，不会滞留到缓存 agent 的下一轮。超过 3 次的遗留 steer 记录 warning 后丢弃。

调用方（`a2a_delegate`）只在 Card 声明了 `steer_path`、且本轮 `send_turn` 正在进行并已拿到本轮 task ID 时才发送 steer；上一轮循环的 task ID 不会被复用。飞书循环模式下，远端任务进行中的非命令输入走 steer 并回复确认；`/main`、`/exit` 等以 `/` 开头的文本、远端未声明、或 steer 被拒绝时，保持原有的下一轮排队。

## 8. 验证

```bash
venv/bin/python -m pytest tests/workagent/backend/test_a2a_interactions.py tests/workagent/backend/test_a2a_executor.py tests/workagent/backend/test_a2a_steer.py -q
venv/bin/python -m pytest tests/tools/test_a2a_delegate_tool.py tests/gateway/test_feishu_clarify_and_delegate.py tests/gateway/test_slack_delegate_runtime_wiring.py -q
venv/bin/python -m pytest tests/aegis/backend/test_chat_ws.py -q
git diff --check
```

验证重点：未认证请求被 A2A middleware 拒绝；错误 task/context/interaction 被拒绝；四种审批 choice 都能解除对应等待；澄清单选、开放文本、多选可恢复；重复/过期/取消 fail-closed；以及审批恢复后的 terminal 仍使用原始平台 userenv。
