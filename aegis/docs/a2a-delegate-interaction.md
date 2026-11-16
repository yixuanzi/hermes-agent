# Aegis A2A Delegate 审批与澄清交互

本文描述 Aegis 后端和前端如何承接 Workagent 的 `hermes.interaction.v1`，把远端 Agent 的审批/澄清请求展示给用户，并将用户响应安全地送回远端 A2A task。

Workagent 服务端协议见 [`workagent/backend/docs/a2a-interaction-extension.md`](../../workagent/backend/docs/a2a-interaction-extension.md)。Slack/Feishu gateway adapter 的卡片实现仍位于 `plugins/platforms/`，Aegis 文档只描述 Aegis WebSocket/UI 这一层。

## 1. 输入事件

`a2a_delegate_tool` 解析远端 task 的 `hermes` metadata 后，通过 delegate output adapter 交给 Aegis `ChatService.handle_delegate_output(...)`。支持四类事件：

| delegate event | Aegis WebSocket event | 作用 |
| --- | --- | --- |
| `approval_request` | `approval.request` | 创建远端审批 pending state。 |
| `clarify_request` | `clarify.request` | 创建远端澄清 pending state。 |
| `approval_resolved` | `approval.resolved` | 清理匹配的审批状态。 |
| `clarify_resolved` | `clarify.resolved` | 清理匹配的澄清状态。 |

请求 metadata 必须至少包含 `interaction_id`、`task_id`、`context_id` 和对应请求字段。Aegis 会把 `interaction_id` 保存为 `remote_interaction_id`，但为 WebSocket/UI 另外生成本地 `approval_id` 或 `clarify_id`，这样 stale response 校验不会依赖远端 ID 的格式。

## 2. Backend pending state

`ApprovalRequestState` 和 `ClarifyRequestState` 增加以下 delegate-only 信息：

```text
source: "main" | "delegate"
remote_interaction_id: str | None
responder: callable | None
```

澄清状态另外保存 `multi_select`。`source="delegate"` 时，服务把 `aegis_a2a_delegate` 当前 session 的 `schedule_interaction_response` 绑定为 responder；它只负责把 HTTP response 安全调度到 session owner event loop，不能在 WebSocket handler 中同步等待远端网络请求。

状态变化如下：

```text
delegate approval_request / clarify_request
  -> handle_delegate_output
  -> pending state(source=delegate, remote_interaction_id=…)
  -> approval.request / clarify.request
  -> run_state = waiting_for_approval / waiting_for_clarify
  -> browser response
  -> remote responder(task/context/interaction)
  -> clear matching pending state + emit local resolved event
  -> run_state = running
```

普通主 Agent 请求仍保留原有本地 resolver。分支条件是 pending state 的 `source`，不是当前页面、按钮样式或 event channel；因此 delegate 响应不会错误地调用 `resolve_gateway_approval()` 或本地 clarify event。

远端随后发送的 `approval_resolved` / `clarify_resolved` 是同一 interaction 的确认消息。由于 Aegis 在远端 responder 已成功排队后可以先清理本地 pending，确认事件必须按幂等消息处理，不能因为本地 state 已为空而重新创建或重复提示。

## 3. WebSocket 响应协议

前端发送：

```json
{
  "type": "approval.respond",
  "choice": "once",
  "approval_id": "approval_…"
}
```

```json
{
  "type": "clarify.respond",
  "answer": "eu",
  "clarify_id": "clarify_…"
}
```

`approval_id` / `clarify_id` 是可选兼容字段，但只要前端有 pending state 就应携带。后端会拒绝与当前 pending 不匹配的 ID，并发送 `approval_stale` 或 `clarify_stale`，防止断线后旧卡片覆盖新请求。

审批只接受 `once`、`session`、`always`、`deny`。delegate pending 时，后端调用远端 responder；responder 不存在、远端 Card 未声明扩展、owner loop 已关闭或 HTTP response 失败时，发送明确错误并保持安全阻断，不回退到本地审批。

澄清拒绝空文本。多选值在前端先编码为 JSON 数组字符串，后端解析为数组后交给远端；如果内容不是有效非空数组，则按普通文本保留并由远端校验，不能用逗号分隔格式冒充数组。`Other` 会进入 `awaiting_text`，下一条文本优先作为当前 delegate interaction 的答案。

## 4. 前端 pending 与交互

`Conversation.pendingApproval` 和 `Conversation.pendingClarify` 保存：

- 来源 `main`/`delegate`，delegate 请求显示远端 agent 来源标签；
- 本地 `approvalId`/`clarifyId`，以及远端 interaction ID；
- 审批 command、description、允许的 Session/Always 选项；
- 澄清 choices、`awaitingText` 和 `multiSelect`。

界面行为：

- delegate approval 保留 Allow Once、Session、Always、Deny；
- delegate clarify 支持单选、开放文本、Other；
- `multiSelect=true` 时允许先勾选多个选项，再将 JSON 数组提交；
- pending 状态会参与未读提醒、会话缓存和断线恢复；
- `session.resume` 通过 `resume_state_events()` 重新发送当前 pending request，恢复后仍带本地 ID 和 delegate 来源；
- 远端响应成功排队后即可清理 pending 并恢复运行状态；收到对应 resolved event 时按幂等确认处理，重复响应不会创建第二个请求。

前端不直接拼接 A2A URL、不持有 A2A token，也不调用远端 HTTP。所有响应通过受 Aegis WebSocket 认证保护的 `approval.respond` / `clarify.respond` 进入后端，由当前 `DelegateForegroundState` 中的 responder 完成远端调用。

## 5. 与 Slack / Feishu 的边界

Slack 和 Feishu 是 gateway 层的另一组 output adapter：它们直接把 delegate request 渲染成 Block Kit/card，并在 callback 中调用各自保存的远端 responder。Aegis WebSocket 的 pending state 不应被复用到 gateway 进程，也不应让 Aegis 的本地 resolver 参与 Slack/Feishu delegate card。

Feishu callback 还要兼容两种操作者身份：入站来源可能保存 tenant-scoped `operator.user_id`，卡片回调同时提供 app-scoped `operator.open_id`。合法点击应接受这两种表示之一，同时继续校验 chat/thread、授权用户和重复状态；普通非 delegate Feishu card 保持原本地 resolver 路径。

## 6. 安全与排查

- 远端没有声明 `hermes.interaction.v1`：不显示可解除等待的远端控件；危险操作保持 fail-closed。
- `remote_interaction_id` 不匹配：视为 stale，不清理当前 pending。
- responder 不可用或 response endpoint 返回冲突：保持 pending/阻断状态，不能自动选择 `once`。
- 断线恢复只恢复当前 pending 元数据，不重放用户已经提交的响应。
- UI 不显示环境变量值、A2A token 或远端内部 secret；command/description 仅按既有审批展示策略脱敏/截断。

常见链路检查：

```text
Agent Card extension
  -> a2a_delegate session interaction_supported
  -> delegate output adapter bind
  -> ChatService.handle_delegate_output
  -> pending source=delegate + responder
  -> WebSocket approval.request / clarify.request
  -> approval_id / clarify_id response
  -> owner-loop schedule_interaction_response
```

重点确认 pending state 的 `source`、`remote_interaction_id` 和 responder 同时存在；三者任一缺失都会表现为“卡片显示但点击后不能继续”。

## 7. 验证

```bash
venv/bin/python -m pytest tests/aegis/backend/test_chat_ws.py -q
venv/bin/python -m pytest tests/tools/test_a2a_delegate_tool.py tests/gateway/test_feishu_clarify_and_delegate.py -q
cd aegis/frontend && npm test
```

至少覆盖：delegate/main resolver 分支、错误或过期 approval/clarify ID、单选/开放文本/多选、Other 文本、resolved event、未读与 resume、远端 responder 不可用，以及普通本地审批/澄清回归。
