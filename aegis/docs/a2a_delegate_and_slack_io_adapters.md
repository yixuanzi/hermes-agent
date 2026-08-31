# `a2a_delegate` 与 Slack / Feishu 前台 I/O 适配器

本文档描述当前 Hermes 主线中 Aegis 二次开发的远端 A2A 委托能力，以及 Slack 和 Feishu 的前台输入/输出适配。它面向需要维护、移植或重新实现该能力的开发者和 AI。

当前模块归属如下：

- Aegis 后端/前端的 pending 状态、WebSocket 事件和用户响应见 [`a2a-delegate-interaction.md`](a2a-delegate-interaction.md)。
- Workagent A2A 服务端的协议、任务元数据、阻塞等待和 Bearer 响应接口见 [`workagent/backend/docs/a2a-interaction-extension.md`](../../workagent/backend/docs/a2a-interaction-extension.md)。
- 本文保留跨模块调用链以及 Slack/Feishu adapter 的实现约束；本次交互扩展只接入 Workagent A2A 服务端，`aisoc/` 下的 A2A executor/server 不在本次改动范围内。

本文只描述当前实现。旧版本的本地 A2A 子 Agent 模式、`type`、`toolsets`、`max_iterations` 参数，以及旧路径 `gateway/platforms/slack.py` 均不属于当前设计。需要本地子 Agent 时使用既有的 `delegate_task`。

## 1. 目标与边界

这套能力让主 Agent 可以将任务委托给配置好的远端 A2A Agent，并在 Slack 或 Feishu 的同一会话/话题中接收远端流式输出和用户后续输入。

设计约束：

- `a2a_delegate` 是 remote A2A-only 工具，位于 opt-in 的 `a2a` toolset，不能加入 `_HERMES_CORE_TOOLS`；避免所有会话为此支付工具 schema 成本。
- 远端委托与 Slack UI 解耦。工具只依赖最小 input/output adapter contract，Slack 只负责路由和渲染。
- `is_loop=true` 必须复用同一个远端会话；退出控制命令不能被误送到远端。
- 前台路由和 stream state 是 gateway 进程内存态，gateway 重启后不会恢复。

当前调用链：

```text
Slack / Feishu message
  -> platform Adapter foreground route (if active)
  -> GatewayRunner binds runtime for this turn
  -> AIAgent._dispatch_a2a_delegate(...)
  -> tools.a2a_delegate_tool.a2a_delegate(...)
  -> _A2ADelegateSession.send_turn(...)
  -> A2A remote agent
  -> delegate events
  -> platform output adapter sends/edits the original thread/topic
```

普通 Slack / Feishu 消息只有在没有命中活动 foreground route 时，才继续进入主 Agent 的常规 `MessageEvent` 派发。

### 1.1 Slack / Feishu 来源信封

Slack 与 Feishu 的来源信息有两个彼此独立的注入点，二者都使用 `<source>JSON</source>`，但用途和字段不同：

| 位置 | 触发条件 | 内容 | 接收方 |
| --- | --- | --- | --- |
| `GatewayRunner._prepare_inbound_message_text(...)` | Slack 或 Feishu 消息已通过 gateway 命令处理、会进入主 Agent，且有 `source.user_id` | `{"platform":"slack"\|"feishu","channel":"…","uid":"…","uname":"…"}`；Feishu 的 `chat_id` 映射到 `channel`，`channel` 与 `uname` 仅在值存在时写入 | 主 Agent 的当前 user turn |
| `tools.a2a_delegate_tool._decorate_a2a_user_message(...)` | 调用 `a2a_delegate` 时父 Agent 有用户 ID 或用户名 | `{"platform":"slack"\|"feishu","uid":"…","uname":"…"}`；由父 Agent 的 `platform`、`_user_id`、`_user_name` 重建 | 每一轮发给远端 A2A Agent 的 user message |

Gateway 信封在所有入站补充信息（如 thread/reply context、附件提示和 `@` 引用展开）处理完后统一置于首行，随后以空行接正文。因此它不会影响 Slack 或 Feishu command 的解析，也不为 history backfill 单独增加来源标签。该信封适用于 DM、channel 和 Feishu 群聊；其他平台仍沿用既有的共享会话 `[user]` 前缀逻辑。

两个信封**不会直接透传或合并**：远端 A2A 的信封不携带 `channel`，也不会从主 Agent user turn 中复制该字段。远端需要稳定的渠道标识时，必须通过 A2A 消息契约显式新增并测试，而不能假设它从 gateway 信封自动获得。

## 2. 工具与配置

主要文件：

- `tools/a2a_delegate_tool.py`: 注册表发现、远端会话、循环、取消和工具 schema。
- `toolsets.py`: `a2a` toolset，包含 `a2a_list` 和 `a2a_delegate`。
- `model_tools.py`、`agent/tool_executor.py`、`agent/agent_runtime_helpers.py`、`run_agent.py`: 将工具作为 agent-owned tool 分发，保留父 Agent 的运行时状态。

### 2.1 `a2a_list()`

`a2a_list()` 是发现工具，不发起委托。它从当前 profile 的 `$HERMES_HOME/a2a.json` 读取注册表，仅保留 `status: active` 的条目；对每项拉取 `<base_url>/.well-known/agent-card.json`，提取技能、接口和描述，并返回：

- `success` / `error`
- Aegis XML 格式的 `context`
- 不含认证 headers 的 `agents` 摘要

Agent Card 请求使用 5 秒超时。注册表或单个 Card 不可用时，返回可诊断的错误而非静默选择一个 Agent。

### 2.2 `a2a_delegate(...)`

公开接口如下：

```python
a2a_delegate(
    goal,
    context=None,
    agent_name=None,
    session_id=None,
    is_delegate_output=True,
    is_loop=False,
)
```

参数语义：

| 参数 | 含义 |
| --- | --- |
| `goal` | 必填的首轮委托目标。 |
| `context` | 可选补充上下文，首轮会与目标合并发送。 |
| `agent_name` | `a2a.json` 中的目标 Agent 名称。 |
| `session_id` | 可选远端 context id；未提供时生成 `delegate_{profile}_a2a_YYYYmmdd_HHMMSS_{01-99}`。 |
| `is_delegate_output` | 非 loop 模式下是否通过 output adapter 发送 delegate 事件；设置为 `false` 时不发送任何 adapter 事件。默认值为 `true`。 |
| `is_loop` | 是否在首轮完成后保留前台输入循环；loop 模式会强制通过 output adapter 发送事件，不受 `is_delegate_output` 影响。 |

`is_delegate_output` 在 Aegis 审计中记录调用方传入的原始值。因此，`is_loop=true` 且 `is_delegate_output=false` 时，实际会发送 adapter 事件，但审计字段仍为 `false`。

调用必须经 `AIAgent._dispatch_a2a_delegate()`，该入口向工具传入：

- 当前父 Agent；工具由此获取平台、用户和停止状态。
- `_delegate_ext_output_adapter`。
- `is_loop=true` 时由 `_delegate_ext_input_factory` 创建的全新 input adapter。

不要在模型参数或普通 registry handler 中传递旧的 local-only 字段；它们不能重新启用 local delegate。

### 2.3 远端 SDK 与请求身份

远端路径懒加载 `a2a` SDK；依赖未安装时返回清晰 tool error，不进入 foreground loop。实际懒依赖由 `tools/lazy_deps.py` 的 `a2a-sdk==1.1.0` 声明。

首轮消息按 `<source>…</source>`、可选 `<context>…</context>`、`goal` 的顺序拼接，段落之间以空行分隔；后续 loop 输入同样会先重新生成 `<source>…</source>`。来源字段来自父 Agent：有 ID 时写入 `uid`，有用户名时写入 `uname`，并写入其 `platform`。因此由 Slack 或 Feishu 发起的远端请求通常形如 `<source>{"platform":"slack"|"feishu","uid":"U…","uname":"…"}</source>`，但不含 `channel`；父 Agent 同时缺少 ID 和用户名时不添加该头。`context` 只在首轮发送。会话对象保存并在每一轮更新：

- `context_id`: 远端会话上下文，也是输出事件的 `session_id`。
- `task_id`: 当前远端 task，用于轮询和取消。

同一 `is_loop` 调用的每一条后续输入必须复用同一个 `_A2ADelegateSession`，从而复用 `context_id` / `task_id`。不得每轮重新创建 session。

## 3. 远端会话、输出和停止

`_A2ADelegateSession` 封装 SDK client、HTTP client、远端 ids、已渲染工具调用和已输出文本。`send_turn()` 先使用 streaming `send_message()`，随后在未终态时调用 `get_task()` 轮询。polling 补偿的默认间隔为 1 秒，客户端 task polling deadline 默认 120 秒；两者可在进程启动前通过 `A2A_POLL_INTERVAL` 和 `A2A_POLL_TIMEOUT` 覆盖，例如：

```bash
A2A_POLL_TIMEOUT=300 A2A_POLL_INTERVAL=2 hermes -p aisoc --yolo aisoc --module a2a
```

环境变量在创建 A2A delegate session 时读取；显式传入的 session 参数优先于环境变量。`A2A_POLL_TIMEOUT` 只控制客户端在非终态 task 上的等待 deadline，不改变 HTTP 建连/读写超时，也不改变远端 approval/clarify 的服务端等待超时。approval/clarify pending 期间客户端 deadline 会暂停，收到对应 resolved 事件后恢复剩余时间。

### 3.1 输出事件协议

工具不直接输出 Slack 文本，而是调用：

```python
output.emit(source, event_type, content, session_id=None)
```

当前 source 为 `delegate`，event type 为：

| 事件 | 用途 |
| --- | --- |
| `delegate.status` | 进入前台、返回主会话、输入超时、中断等状态。 |
| `delegate.ai_delta` | 从远端 task 文本的前缀增量计算出的流式片段。 |
| `delegate.ai` | 一轮远端 task 的最终文本。 |
| `delegate.tool_call` | 从远端 assistant message 提取的工具调用；同一 call 去重。 |
| `delegate.error` | 配置、SDK、网络、输入或远端执行错误。 |

工具 result 同时包含可供模型继续判断的结构化 JSON。远端 tool result message 不作为用户可见 tool call 重复展示。

### 3.2 `hermes.interaction.v1` 审批/澄清交互

Workagent 远端 Agent 需要用户授权或澄清时，不结束当前 task，也不把请求伪装成普通 AI 文本，而是在同一个 `TaskState.WORKING` 更新中携带 `hermes` metadata。服务端 Agent Card 通过一个非强制扩展声明是否支持该响应通道：

```json
{
  "uri": "https://hermes.dev/extensions/interaction/v1",
  "required": false,
  "params": {"response_path": "/a2a/hermes/interaction/respond"}
}
```

审批请求的 metadata 形态：

```json
{
  "hermes": {
    "kind": "approval_request",
    "interaction_id": "approval_…",
    "task_id": "…",
    "context_id": "…",
    "command": "chmod 777 …",
    "description": "world/other-writable permissions",
    "choices": ["once", "session", "always", "deny"],
    "allow_session": true,
    "allow_permanent": true
  }
}
```

澄清请求使用 `kind: "clarify_request"`，携带 `question`、可选 `choices`、`multi_select` 和同一组三个远端 ID。调用方通过受相同 A2A Bearer 认证保护的接口响应：

```text
POST {A2A_RPC_PATH}/hermes/interaction/respond
```

```json
{"task_id":"…","context_id":"…","interaction_id":"…","kind":"approval","choice":"once"}
```

或：

```json
{"task_id":"…","context_id":"…","interaction_id":"…","kind":"clarify","answer":["option-a","option-b"]}
```

服务端 `InteractionRegistry` 校验 task/context/interaction/kind 的匹配关系，并限制审批值为 `once/session/always/deny`。澄清拒绝空答案，多选答案以 JSON 数组保留。重复点击、未知 ID、过期等待和底层等待已结束分别返回明确的 `409`/`404`/`422` 错误；超时、取消和 executor cleanup 都会释放底层 approval/clarify wait，默认 fail-closed。

调用方的完整交互链路是：

```text
remote tools.approval / clarify_gateway
  -> Workagent register interaction + block current agent thread
  -> TaskState.WORKING metadata
  -> a2a_delegate streaming event 或 get_task() polling
  -> 按 interaction_id 去重并发出 approval_request / clarify_request
  -> Slack / Feishu / Aegis 展示交互控件
  -> UI callback 在 A2A session owner event loop 调度 responder
  -> POST /hermes/interaction/respond（Bearer + task/context/interaction）
  -> Workagent registry resolver
  -> 唤醒原 approval/clarify wait，继续同一 context_id 的 agent turn
  -> approval_resolved / clarify_resolved metadata（用于确认；调用方可先清理已成功提交的本地 pending）
```

`a2a_delegate` 只有在 Agent Card 声明该扩展时才显示可响应控件；远端未声明扩展时会保持旧的安全行为，不人为制造一个无法解除阻塞的按钮，也不会自动批准危险操作。解析 streaming 与 polling 的实现都经过同一个 session 事件入口，因此轮询不会重复显示同一个 interaction。

### 3.3 取消与异常收尾

活动远端 session 会登记到父 Agent 的 `_active_a2a_delegate_session`。停止请求通过 `_RemoteA2ADelegateCancelHandle` 将 `cancel_task` 调度到 session 所属 event loop；若 task 尚未创建，取消是 no-op。停止过程对 event loop 已关闭、跨 loop 和被停止的 coroutine 等常见收尾异常做受控抑制，避免中断路径二次失败。

远端 task 的 completed、canceled、input-required、error 等状态都会转换为明确结果。不要把远端失败伪装成成功的空回复。

## 4. `is_loop` 前台输入协议

只有循环委托才使用 input adapter。最小接口是：

```python
enter_foreground() -> bool
exit_foreground() -> None
read_line(timeout=...) -> str | None
close() -> None
last_read_timed_out() -> bool
```

执行顺序：

1. 校验 input adapter 存在；缺失时立即返回 tool error，不能在首轮后静默结束。
2. 创建一次远端 session，发送首轮 `goal`。
3. 调用 `enter_foreground()`，令宿主接管同一 Slack route 的后续输入。
4. 阻塞读取输入；每次读取前触发父 Agent activity touch，避免 gateway 将等待误判为空闲。
5. 普通文本调用同一 session 的 `send_turn()`。
6. 精确的 `/main` 或 `/exit` 退出循环，调用 `exit_foreground()` 并返回主会话。
7. 输入超时（25 分钟）、input 关闭、远端异常或用户中断均返回带退出原因的 JSON，并在 `finally` 中释放路由、关闭 session、清除 active handle。

控制命令必须精确匹配。`/exit now` 以及包含 `/exit` 的自然语言仍是远端输入。

## 5. Slack / Feishu 前台 I/O

当前实现位于 `plugins/platforms/slack/adapter.py` 和 `plugins/platforms/feishu/adapter.py`，不是旧的 gateway platform 模块。

### 5.1 每轮 runtime 绑定

`GatewayRunner._bind_delegate_foreground_runtime_for_turn(...)` 在每次 gateway turn 运行前：

1. 清理 cached Agent 上一轮遗留的 adapter binding。
2. 调用当前 adapter 的 `build_delegate_foreground_runtime(...)`。
3. 将 output adapter 和 input factory 绑定到本轮 Agent。

这个刷新动作对 cached Agent 至关重要：一个 Agent 实例不能保留前一 Slack / Feishu thread、channel、topic 或用户的 I/O。

`build_delegate_foreground_runtime(...)` 每次生成新的 input adapter factory；不能复用 queue/Condition 或把多个前台 loop 绑定到同一个 input 实例。Feishu route 以 `build_session_key(...)` 的 chat、thread/topic 与 user 维度隔离；即使普通群话题 transcript 配置为共享，前台阻塞 queue 仍按用户隔离。

### 5.2 路由与输入

两个 adapter 都维护前台 route 表和 stream state 表。route key 使用 `gateway.session.build_session_key(...)`，结合 channel/chat、thread/topic、chat type 和用户语义进行隔离。一个活动 route 记录 input adapter、目标 session 和平台上下文。

`_SlackDelegateInputAdapter` 使用 `threading.Condition` 在异步 Slack event handler 与同步工具 loop 之间桥接：

- `enter_foreground()` 注册 route。
- `_maybe_route_delegate_foreground_message(...)` 命中 route 后把文本 `push_line(...)`。
- `read_line()` 在工具线程中阻塞等待；`close()` 会唤醒等待者。
- `exit_foreground()` 释放 route 和关联 stream state。

在 `_handle_slack_message` 与 Feishu `_process_inbound_message` 中，foreground route 检查发生在普通 `MessageEvent` 派发、媒体批处理和 per-chat guard 之前。命中 route 后，消息不会启动主会话的新一轮 Agent。

为确保退出命令可靠，adapter 同时维护两份文本：

- `delegate_command_text`: 来自 `original_text`，移除当前 workspace 的 bot mention 后 `strip()`；仅用于精确识别 `/main` / `/exit`。
- `delegate_input_text`: 当前主线已经补全 reply/thread context 的普通正文；只用于非控制输入。该正文不会再走主 Agent 的 Slack gateway 来源信封注入；远端 A2A 请求由工具按父 Agent 身份单独添加其不含 `channel` 的来源信封。

route 命中时先 flush 上一段 delegate stream，再压入用户文本，避免用户下一轮输入与未刷新的远端 delta 混在同一 Slack 消息段。

### 5.3 输出渲染和节流

`_SlackDelegateOutputAdapter` 与 `_FeishuDelegateOutputAdapter` 将事件调度回各自 adapter 的主 event loop，再按 route 更新 stream state：

- `ai_delta`: 缓冲增量，首段可立即发送；对同一可编辑 Slack 消息最多每 3 秒更新一次。延迟 task 负责到期 flush。
- `ai`: 执行 segment break / flush，不重复发送已由 delta 渲染的最终全文。
- `tool_call`: 先 flush 当前文本段，再发送单独的工具调用预览。
- `status` 与 `error`: 发送独立状态消息。
- 长内容: 按各平台可渲染长度拆分为多个 segment。

若 edit 失败且不可恢复，stream state 降级为追加消息；委托输出不能因一条消息不可编辑而丢失。退出、adapter 关闭或 route 失效时必须取消延迟 flush 并清理 state。

### 5.4 Approval / Clarify 卡片与 delegate 交互

普通本地 gateway 审批/澄清和远程 delegate 交互状态必须分开保存。delegate 卡片的按钮携带远端 `interaction_id`，但不调用本地 `resolve_gateway_approval` 或本地 clarify resolver；它们通过 `_A2ADelegateSession.schedule_interaction_response(...)` 回到远端服务。响应调度使用 session 所属 event loop，避免在 Slack/Feishu/Aegis 的前端事件循环中同步等待 HTTP。

Slack delegate 使用独立的 Block Kit state，保留 Allow Once、Session、Always、Deny、clarify 选项、Other 和多选提交；回调同时校验 interaction、channel/thread、操作者授权和重复点击。Other 后的下一条文本优先路由到对应远端 interaction。

Slack adapter 也覆写 `send_clarify(...)`：多选 clarify 使用 section + actions buttons，每个按钮只保存 `clarify_id` 和选项 index；真实选项文本留在 adapter 内存映射，避免写进 interaction payload。

`^hermes_clarify_` action handler 在 `ack()` 后必须调用 `_is_interactive_user_authorized(...)`。只有授权用户可以：

- 选择具体 option，调用 gateway clarify resolve primitive，并将卡片更新为选择结果。
- 选择 `Other (type answer)`，调用 `mark_awaiting_text(...)` 并将卡片更新为等待文本回答。

未授权或 malformed action 仅记录告警，不 resolve、不更新卡片。开放式 clarify 沿用现有文本捕获 fallback，不创建 modal。

Feishu adapter 以 interactive card 实现相同语义：问题、每个选项按钮和 `Other (type answer)` 按钮。delegate action value 保存 `interaction_id` 和选项 token，真实选项和预期 chat/thread 存在 adapter 内存。同步 card callback 会校验 pending state、callback chat、现有群组准入规则和 interactive 操作者授权；同时兼容 Feishu 回调中的 tenant-scoped `operator.user_id` 与 app-scoped `operator.open_id`，避免把入站 `<source>` 的用户 ID 只与 `open_id` 比较而误拒绝合法点击。通过校验后才调用远端 responder；选中或等待输入后返回原地更新卡片。未知、重复、跨 chat、未授权或身份不匹配的点击不 resolve 也不更新卡片。发送卡片及 delegate 输出时保留 `thread_id` metadata，因此 Feishu 话题会被正确路由。

普通（非 delegate）Feishu 卡片仍使用原有的 `resolve_gateway_approval(...)`、`resolve_gateway_clarify(...)` 和本地 pending state，不能因为 delegate 兼容逻辑而改变其 resolver 或授权边界。

## 6. 并发、隔离与排查

- 运行时 binding 必须每 turn 刷新，input factory 必须每次创建新 adapter，避免 cached agent 跨 Slack / Feishu thread、topic 或用户复用 I/O。
- route 释放、input close 和 stream-state cleanup 必须在循环退出路径中执行；否则后续正常消息可能被错误吞入旧委托。
- A2A 默认 session id 在时间戳后增加两位随机后缀，降低同秒并发创建的 context id 冲突概率。
- `_active_a2a_delegate_session` 是父 Agent 上的活动会话取消句柄；新增并行策略时必须审查其单句柄语义，不能假设它能独立取消任意并发 remote delegate。
- 交互卡片显示但点击无效时，先检查 adapter 保存的 interaction 是否仍 pending，再检查 Feishu callback 的 `operator.user_id` / `operator.open_id`、chat/thread 是否与卡片状态匹配，最后确认 owner event loop 仍运行且远端 Agent Card 声明了 interaction extension。
- Workagent interaction response 的 HTTP 401/403 表示 A2A Bearer 认证或入口配置问题；404/409/422 分别优先检查 interaction 生命周期、task/context 匹配和 choice/answer 格式，不要通过重发或自动批准绕过安全策略。

排查优先级：先确认 `a2a.json` 和 Agent Card，再确认 toolset 是否显式启用；对于来源信息，分别检查主 Agent user turn 的 Slack / Feishu 信封（含 `channel`）和远端 A2A payload 的工具信封（不含 `channel`），随后检查 gateway 是否为当前 turn 绑定 runtime、平台 route 是否命中、`context_id` 是否复用，以及 output event 是否已调度到 adapter 主 loop。

## 7. 验证

从仓库根目录执行：

```bash
venv/bin/python -m pytest tests/tools/test_a2a_delegate_tool.py -q
venv/bin/python -m pytest tests/gateway/test_slack_delegate_runtime_wiring.py -q
venv/bin/python -m pytest tests/gateway/test_slack_clarify_buttons.py -q
venv/bin/python -m pytest tests/gateway/test_feishu_clarify_and_delegate.py -q
venv/bin/python -m pytest tests/workagent/backend/test_a2a_interactions.py tests/workagent/backend/test_a2a_executor.py -q
venv/bin/python -m pytest tests/aegis/backend/test_chat_ws.py -q
venv/bin/python -m pytest tests/gateway/test_shared_group_sender_prefix.py -q
venv/bin/python -m pytest tests/run_agent/test_run_agent.py tests/test_model_tools.py -q
venv/bin/python -m py_compile tools/a2a_delegate_tool.py plugins/platforms/slack/adapter.py plugins/platforms/feishu/adapter.py gateway/run.py run_agent.py
git diff --check
```

回归至少应覆盖：remote-only schema、SDK 缺失、loop 无 input adapter、同一 remote session 多轮复用、每轮远端来源信封、`/main`/`/exit`、Slack / Feishu route hit/miss、主 Agent Slack / Feishu 信封在 reply/thread context 之前且含 channel、cached agent binding 刷新、delta 编辑节流、final text 不重复、长文本拆段、tool call segment break、授权/未授权 clarify card/button，以及 Workagent interaction 的 streaming/polling 去重、task/context/interaction 校验、响应后恢复、超时/取消 fail-closed 和 Feishu tenant/app user ID 兼容。
