# `a2a_delegate` 与 Slack 前台 I/O 适配器说明

本文档面向需要理解 Hermes 委托链路的开发者，重点说明两部分实现：

- [tools/a2a_delegate_tool.py](/Users/guisheng.guo/.hermes/hermes-agent/tools/a2a_delegate_tool.py) 中的 `a2a_delegate`
- [gateway/platforms/slack.py](/Users/guisheng.guo/.hermes/hermes-agent/gateway/platforms/slack.py) 中为委托前台交互实现的 input/output 适配器

它们共同解决的问题是：让 Hermes 在一次主会话里，把某个任务委托给本地子 Agent 或远端 A2A Agent，并且在需要时把“后续用户输入”和“委托输出流”接入同一个 Slack 线程，形成可持续的前台交互回路。

## 1. 整体定位

从职责上看，这一套实现可以分成三层：

1. `a2a_delegate` 负责“如何执行委托”
2. Slack input/output 适配器负责“如何把 Slack 线程接成委托会话的前台 I/O”
3. `run_agent.py` 与 `gateway/run.py` 负责“把运行时适配器注入到当前 Agent，再把工具调用分派过去”

简化后的调用链如下：

```text
Slack 用户消息
  -> SlackAdapter 识别线程 / 前台路由
  -> GatewayRunner 为当前 agent 注入委托 I/O 运行时
  -> AIAgent._dispatch_a2a_delegate(...)
  -> tools.a2a_delegate_tool.a2a_delegate(...)
  -> 本地子 Agent 或远端 A2A Agent
  -> 委托输出事件回到 Slack output adapter
  -> Slack 线程内持续展示 / 编辑流式结果
```

## 2. `a2a_delegate` 模块的作用

`tools/a2a_delegate_tool.py` 不是一个单一函数文件，而是一个“委托执行器模块”。它同时承担以下职责：

- 暴露两个工具：`a2a_list` 与 `a2a_delegate`
- 读取当前 profile 下的 A2A 注册表 `a2a.json`
- 拉取远端 Agent Card，生成可供模型理解的能力摘要
- 在 `local` 模式下启动本地子 Agent
- 在 `a2a` 模式下连接远端 A2A 服务并维持上下文
- 在 `is_loop=true` 时进入前台交互循环，持续接收用户后续输入
- 把委托过程中的状态、工具调用、流式文本输出，通过运行时 output adapter 发回宿主环境
- 处理停止、超时、取消、清理等边界行为

换句话说，`a2a_delegate` 是 Hermes 内部“委托协议”的真正执行面，而不是单纯的 schema 封装。

## 3. `a2a_delegate` 提供的两个工具

### 3.1 `a2a_list`

`a2a_list()` 的作用不是发起委托，而是把当前 profile 中可用的远端 A2A Agent 配置整理为模型可消费的上下文。

主要行为：

- 从 `get_hermes_home()/a2a.json` 读取 `a2a` 与 `global` 配置
- 仅纳入 `status == "active"` 的 Agent
- 访问 `<base_url>/.well-known/agent-card.json`
- 提取技能、输入输出模式、接口地址等轻量摘要
- 组合为 Aegis XML，上层可把它作为路由提示或选择依据

这个工具的核心价值是“发现与描述”，让模型在真正调用 `a2a_delegate` 前知道有哪些远端能力存在。

### 3.2 `a2a_delegate`

`a2a_delegate(...)` 是真正的委托入口，支持两种模式：

- `type="local"`：在本机启动一个受限子 Agent
- `type="a2a"`：连接远端 A2A Agent，并把当前任务作为一个远程会话推进

关键参数含义：

- `goal`：委托目标，必填
- `context`：补充上下文，仅本地委托更常用
- `agent_name`：远端 A2A Agent 名称，`type="a2a"` 时使用
- `toolsets`：仅本地子 Agent 使用
- `max_iterations`：仅本地子 Agent 使用
- `session_id`：委托子会话 ID；未提供时自动生成
- `is_delegate_output`：是否把输出按“委托输出”协议回送给宿主
- `is_loop`：是否进入前台循环模式
- `input` / `output`：由宿主运行时注入，不是模型直接关心的能力对象

## 4. 模块内部的关键组成

### 4.1 注册表与能力发现

这一层主要由以下辅助函数构成：

- `_a2a_registry_path()`
- `_load_a2a_registry()`
- `_normalize_a2a_registry_entry()`
- `_fetch_agent_card()`
- `_extract_a2a_capabilities()`
- `_summarize_agent_card()`
- `_build_aegis_context_xml()`

职责划分如下：

- 注册表读取：从磁盘装载 A2A Agent 定义
- 配置归一化：统一 URL、headers、status、额外能力字段
- Agent Card 拉取：通过 HTTP 获取远端自描述信息
- 能力摘要提取：把技能、输入/输出模式等压缩成简洁文本
- Aegis 上下文生成：给上层路由或模型选择使用

这里的设计重点是“把远端服务配置变成运行时可用的、本地缓存的能力索引”，而不是每次委托都从零推断。

### 4.2 本地委托执行器

`local` 模式的核心函数包括：

- `_build_local_child_agent()`
- `_run_local_delegate()`

它的作用是：

- 复用父 Agent 的模型、provider、SessionDB、认证池等运行时环境
- 只启用指定工具集，避免子 Agent 拥有不必要的能力
- 显式移除 `a2a_delegate`，防止递归委托
- 在 `is_loop=false` 时执行单轮委托
- 在 `is_loop=true` 时复用同一个子会话持续接收用户后续输入

本地委托更像“轻量级子 Agent 会话托管器”。

### 4.3 远端 A2A 会话执行器

远端模式的核心对象是 `_A2ADelegateSession`。

它封装了：

- 远端 URL 与 headers
- `context_id` / `task_id`
- A2A SDK client 与 HTTP client
- 已渲染的工具调用记录
- 已输出的 assistant 文本增量
- 所属事件循环与线程信息
- 停止请求状态

它的主要职责有：

- 建立和关闭远端 A2A client
- 向远端发送用户消息
- 轮询直到任务进入终态
- 识别远端消息中的工具调用与工具结果
- 将远端 assistant 输出拆成 delta 事件
- 在需要时取消远端任务

这使得 `a2a_delegate` 不需要直接操作底层 A2A SDK 细节，而是通过一个状态明确的 session 对象推进远端会话。

### 4.4 前台循环与输入协议

无论是 `local` 还是 `a2a`，只要 `is_loop=true`，都会进入“委托前台模式”。

这个模式下，工具会：

1. 调用 `input.enter_foreground()` 通知宿主切换路由
2. 先发送 `goal` 作为第一轮用户输入
3. 阻塞等待 `input.read_line(...)`
4. 把后续收到的文本继续送进同一个子会话
5. 遇到 `/main` 或 `/exit` 时退出前台并返回主会话
6. 在超时、输入关闭、异常时做清理并返回结果 JSON

也就是说，`a2a_delegate` 自己并不拥有终端或 Slack 输入源，它只是定义了一套最小输入协议：

- `enter_foreground()`
- `exit_foreground()`
- `read_line(timeout=...)`
- `close()`
- `last_read_timed_out()`

谁来实现这套协议，取决于宿主环境。Slack input adapter 正是这一层的一个实现。

### 4.5 输出事件协议

`a2a_delegate` 不直接假设输出一定是终端文本。它通过 `_emit_delegate_event(...)` 发出统一事件：

- `delegate.status`
- `delegate.ai_delta`
- `delegate.ai`
- `delegate.tool_call`
- `delegate.error`

这套事件协议的价值在于：

- 终端、Slack、其他 UI 可以各自决定如何渲染
- 子会话与主会话的输出能被区分
- 流式文本、最终文本、工具调用都能拆开处理

Slack output adapter 对这套协议做了平台化翻译，把这些事件重映射成 Slack 线程中的消息发送、编辑与分段刷新。

## 5. `a2a_delegate` 的典型运行流程

### 5.1 `local` 单轮模式

执行路径：

1. 校验 `goal`
2. 归一化 `toolsets` 与 `max_iterations`
3. 构造本地子 Agent
4. 继承父会话所需环境
5. 运行一轮 `run_conversation()`
6. 返回包含 `session_id`、`api_calls`、`duration_seconds`、`final_response` 的 JSON

适合一次性拆子任务，不需要持续接管用户输入。

### 5.2 `local` 前台循环模式

执行路径：

1. 进入 foreground
2. 发送初始 `goal`
3. 进入读取后续输入的 while 循环
4. 每条输入都复用同一个 child session
5. `/main` 或 `/exit` 结束循环
6. `finally` 中退出 foreground，并注销 active child

适合把一个复杂任务切出去，让子 Agent 连续对话。

### 5.3 `a2a` 远端模式

执行路径：

1. 用 `agent_name` 解析注册表项
2. 若需要则刷新 `A2A_REGISTRY`
3. 解析远端 URL 与 Agent Card
4. 构造 `_A2ADelegateSession`
5. 创建远端会话并发送用户首轮消息
6. 读取返回事件，提取 assistant delta、工具调用、最终状态
7. 根据终态构造工具返回值

如果开启 `is_loop=true`，后续用户输入会继续沿用同一个远端 `context_id`。

## 6. 停止、取消与超时机制

这一模块比普通工具多做了一层“活动会话句柄”管理，关键目的是让父 Agent 或宿主可以安全中断当前 A2A 委托。

关键部件：

- `_RemoteA2ADelegateCancelHandle`
- `_register_active_a2a_session()`
- `_clear_active_a2a_session()`

它们的作用是：

- 在父 Agent 上登记当前活动的远端委托
- 在外部请求停止时，把 cancel 安排到拥有该 session 的事件循环上
- 对常见的“event loop is closed / different loop”收尾异常做抑制

另外，前台循环还有单独的输入超时：

- `_DELEGATE_FOREGROUND_INPUT_TIMEOUT_SECONDS = 25 * 60`

超时后不会无限阻塞，而是返回 `input_timeout` 类型的退出结果。

## 7. 与 `run_agent.py` 的集成方式

[run_agent.py](/Users/guisheng.guo/.hermes/hermes-agent/run_agent.py) 中的 `AIAgent._dispatch_a2a_delegate()` 是所有 `a2a_delegate` 工具调用的统一入口。

它做了两件很重要的事：

- 从当前 Agent 上取出 `_delegate_ext_output_adapter`
- 如果 `is_loop=true`，再通过 `_delegate_ext_input_factory` 生成一个新的 input adapter

随后再调用 `tools.a2a_delegate_tool.a2a_delegate(...)`。

这意味着：

- `a2a_delegate` 不自行构造 Slack/CLI 适配器
- 适配器由宿主运行时按“当前会话、当前线程、当前用户”注入
- 同一个工具可以复用于 CLI、AISOC、Slack 等不同入口

## 8. Slack 前台 I/O 适配器的作用

Slack 侧的实现位于 [gateway/platforms/slack.py](/Users/guisheng.guo/.hermes/hermes-agent/gateway/platforms/slack.py)，主要目标是把 Slack thread 变成一个“委托子会话的前台交互通道”。

它要解决三个问题：

1. 如何把同一线程里的后续用户消息直接送给委托，而不是再触发主 Agent 新的一轮
2. 如何把委托的流式输出连续展示在同一线程内
3. 如何在委托退出时恢复普通的 Slack 消息处理路径

因此 Slack 侧并不负责委托逻辑本身，而是负责“线程级 I/O 路由”。

## 9. Slack 侧的关键数据结构

### 9.1 `_SlackDelegateRoute`

这是前台路由表里的单条记录，记录：

- `channel_id`
- `thread_ts`
- `user_id`
- `chat_type`
- `input_adapter`
- `session_id`

它表示“某个 Slack 线程当前是否被某个委托会话接管”。

### 9.2 `_SlackDelegateStreamState`

这是输出流状态机，记录：

- 已累积文本 `accumulated_text`
- 尚未落盘/发送的文本 `pending_text`
- 当前可编辑消息 `message_id`
- 分段消息列表 `message_ids`
- 当前分段 `current_groups`
- 上次 flush 时间
- 当前是否还能继续用 edit 模式更新消息
- 延迟 flush 的异步任务
- 异步锁

它的作用是把 `delegate.ai_delta` 这样的高频流式增量，转换成 Slack 友好的“先发消息，再编辑更新，必要时再拆段”的展示模式。

## 10. `_SlackDelegateInputAdapter` 的职责

`_SlackDelegateInputAdapter` 是 `a2a_delegate` 所需输入协议在 Slack 线程上的实现。

它基于 `threading.Condition` 维护一个线程安全的输入队列，主要能力包括：

- `enter_foreground()`：要求 SlackAdapter 把当前线程切到委托前台
- `exit_foreground()`：释放前台，让线程回到主会话处理
- `push_line(text)`：Slack 收到线程新消息时，把文本压入队列
- `read_line(timeout)`：委托循环阻塞等待下一条用户输入
- `close()`：关闭输入，唤醒等待中的读取方
- `is_waiting_for_input()` / `last_read_timed_out()`：暴露状态，便于控制与测试

它的本质不是“聊天消息对象”，而是一个把 Slack 线程翻译成阻塞式行输入流的桥接器。

### 为什么它必须存在

`a2a_delegate` 的前台循环是同步/阻塞读取风格；Slack 网关则是事件驱动、异步回调风格。两者的运行模型不一样。

`_SlackDelegateInputAdapter` 的作用，就是在这两种模型之间做缓冲和协议转换：

- Slack 收到消息时调用 `push_line`
- 委托循环等待时调用 `read_line`

这样工具层不需要知道 Slack SDK 的事件细节。

## 11. `_SlackDelegateOutputAdapter` 的职责

`_SlackDelegateOutputAdapter` 是输出协议的 Slack 实现。

它的核心职责包括：

- 保留当前委托对应的 `channel_id` / `thread_ts`
- 把所有输出调度回 SlackAdapter 所属主事件循环
- 识别不同委托事件类型，并映射到不同的 Slack 展示策略

具体行为：

- `delegate.ai_delta`
  - 交给 `handle_delegate_ai_delta(...)`
  - 用增量流方式持续编辑 / 追加消息
- `delegate.ai`
  - 在最终文本落地前，先调用 `handle_delegate_stream_segment_break(...)`
  - 确保流式段落先 flush，再结束本段
- `delegate.tool_call`
  - 先切断当前流段
  - 再把工具调用格式化成人类可读的文本，例如工具名加参数预览
- 其他事件
  - 退化为 `source.event_type: content` 形式直接发送

### 为什么它要显式绑定主事件循环

委托逻辑可能跑在：

- Agent 的 worker thread
- 临时 `asyncio.run(...)` 创建的 loop
- A2A 远端取消/关闭所在线程

但 Slack HTTP client 是绑定在 SlackAdapter 自己的主 loop 上的。若把发送任务发到错误的 loop，轻则消息丢失，重则出现“future attached to a different loop”之类的异常。

因此 `_SlackDelegateOutputAdapter.emit()` 会优先把发送调度回 `SlackAdapter._main_loop`，这是它最关键的稳定性职责之一。

## 12. Slack 的前台路由如何工作

SlackAdapter 内部维护：

- `_delegate_foreground_routes`
- `_delegate_stream_states`
- `_delegate_foreground_lock`

配套方法包括：

- `_delegate_route_key(...)`
- `_get_delegate_route(...)`
- `_activate_delegate_foreground(...)`
- `_release_delegate_foreground(...)`
- `_clear_delegate_route(...)`
- `_maybe_route_delegate_foreground_message(...)`
- `build_delegate_foreground_runtime(...)`

其运行机制如下。

### 12.1 路由键生成

`_delegate_route_key(...)` 会尽量复用 `gateway.session.build_session_key(...)` 的规则，把：

- platform
- channel_id
- chat_type
- user_id
- thread_ts

压成一个与 SessionStore 语义一致的 key。

这样做的价值是：Slack 的“委托前台路由”尽量与既有 session 归属逻辑一致，减少线程、DM、多用户场景下的偏差。

### 12.2 进入前台

当 `a2a_delegate(is_loop=true)` 调用 `input.enter_foreground()` 时，最终会落到 `_activate_delegate_foreground(...)`。

这个方法会：

- 根据当前线程与用户信息生成 route key
- 检查该 key 是否已有别的 input adapter 占用
- 若无冲突，则把当前 adapter 记入 `_delegate_foreground_routes`

一旦登记成功，这个 Slack 线程在路由意义上就进入了“delegate foreground”状态。

### 12.3 路由后续用户消息

Slack 收到后续消息时，会先看当前线程是否已有活动委托路由。

如果有，`_maybe_route_delegate_foreground_message(...)` 会：

- 直接把文本 `push_line(...)` 到活动 input adapter
- 不再走常规主 Agent dispatch
- 在需要时调用 `handle_delegate_stream_segment_break(...)`
  - 先把之前尚未 flush 的流式输出段落落地
  - 避免“用户新输入”和“上一段未发完的 delta”在 Slack 视觉上交错

如果推送失败，则会清空该路由，恢复普通主会话逻辑，避免消息被吞掉。

### 12.4 退出前台

当委托循环结束时，`input.exit_foreground()` 最终会触发 `_release_delegate_foreground(...)`。

它会：

- 从 `_delegate_foreground_routes` 删除当前 route
- 清理对应的 `_delegate_stream_states`

此后同一 Slack 线程就恢复为普通消息线程，重新走 Hermes 原有的主会话处理流程。

## 13. Slack 输出流的展示策略

相比简单 `send()`，委托输出流多了一层“边流式、边编辑、必要时分段”的逻辑。

关键方法：

- `handle_delegate_ai_delta(...)`
- `_flush_delegate_stream_locked(...)`
- `_flush_delegate_stream_after_delay(...)`
- `handle_delegate_stream_segment_break(...)`
- `_split_delegate_stream_content(...)`

设计目的有三点：

1. 降低 Slack 消息条数，避免每个 delta 都发一条
2. 尽量通过编辑已有消息，模拟连续生成效果
3. 在超过消息长度上限时自动拆段，保证最终内容完整可见

### 13.1 `delegate.ai_delta`

每次收到文本增量：

- 先累积到 `pending_text`
- 若当前允许编辑，也同步累积到 `accumulated_text`
- 达到时间阈值或缓冲阈值时 flush
- 否则启动一个延迟 flush task

因此它兼顾了响应速度与 API 调用频率。

### 13.2 段落切换

当发生以下事件时，Slack 会显式执行 segment break：

- 收到 `delegate.ai` 最终文本
- 收到 `delegate.tool_call`
- 路由中收到新的用户输入

目的都是一致的：把当前流式文本段落先结算，再开始新的语义片段，保证 Slack 线程里的显示层次更清楚。

### 13.3 退化策略

如果消息编辑失败且属于不可恢复错误，Slack 会把该 route 的流状态切换到“不可编辑”模式，并回退到直接发送文本的方式。

这样即使 Slack 编辑能力不可用，委托输出也不会彻底中断。

## 14. 与 `gateway/run.py` 的协作

[gateway/run.py](/Users/guisheng.guo/.hermes/hermes-agent/gateway/run.py) 中的 `_bind_delegate_foreground_runtime_for_turn(...)` 是 Slack 与 `a2a_delegate` 之间的连接点。

每轮消息处理前，它会：

1. 清理 Agent 上旧的委托运行时绑定
2. 确认当前来源平台是 Slack
3. 调用 `SlackAdapter.build_delegate_foreground_runtime(...)`
4. 将返回的
   - `_delegate_ext_output_adapter`
   - `_delegate_ext_input_factory`
   注入当前 Agent

这样一来，工具层拿到的是“当前线程专属”的 input/output 适配器，而不是全局单例。

这对 Slack 很关键，因为：

- 不同线程需要不同的 `thread_ts`
- 不同 DM / 群组需要不同的 session key
- 同一 bot 可能并发服务多个用户和多个线程

## 15. 这套设计解决了什么问题

### 对 `a2a_delegate` 而言

它解决了：

- 本地委托与远端 A2A 委托复用同一工具入口
- 委托过程支持一次性调用，也支持持续前台循环
- 输出与输入协议与宿主 UI 解耦
- 父 Agent 能够追踪和取消活动远端委托

### 对 Slack 适配器而言

它解决了：

- 同一 Slack 线程内的后续消息重定向问题
- 流式委托输出在 Slack 中的连续展示问题
- 主会话与子委托前台之间的切换问题
- 线程 / DM / 用户维度下的委托路由隔离问题

## 16. 适用边界与限制

当前实现有几个明确边界：

- 前台路由状态是内存态，不跨 gateway 重启持久化
- Slack 适配器只负责 I/O 与展示，不负责远端任务业务语义
- `a2a_delegate` 的循环协议是单前台委托模型，不是多会话调度器
- 本地委托显式移除了 `a2a_delegate`，避免无限递归嵌套
- 远端 A2A 的可用性依赖 `a2a.json` 配置和 Agent Card 可访问性

这些限制是有意设计的，目的是优先保证委托链路简单、稳定、可控。

## 17. 开发和排查时最值得关注的入口

若需要继续扩展或排查问题，优先从下面几个入口看起：

- 工具入口：
  - [tools/a2a_delegate_tool.py](/Users/guisheng.guo/.hermes/hermes-agent/tools/a2a_delegate_tool.py)
- Agent 分发入口：
  - [run_agent.py](/Users/guisheng.guo/.hermes/hermes-agent/run_agent.py)
- Slack 路由与适配器：
  - [gateway/platforms/slack.py](/Users/guisheng.guo/.hermes/hermes-agent/gateway/platforms/slack.py)
- 运行时注入入口：
  - [gateway/run.py](/Users/guisheng.guo/.hermes/hermes-agent/gateway/run.py)

重点看以下函数最容易建立全貌：

- `a2a_delegate(...)`
- `_run_local_delegate(...)`
- `_run_a2a_delegate(...)`
- `_A2ADelegateSession.send_turn(...)`
- `SlackAdapter.build_delegate_foreground_runtime(...)`
- `SlackAdapter._maybe_route_delegate_foreground_message(...)`
- `SlackAdapter.handle_delegate_ai_delta(...)`
- `GatewayRunner._bind_delegate_foreground_runtime_for_turn(...)`

## 18. 一句话总结

`a2a_delegate` 负责“把任务委托出去并维持委托会话”，Slack input/output 适配器负责“把 Slack 线程临时变成这个委托会话的前台交互通道”。二者配合后，Hermes 才能在 Slack 中实现近似终端前台切换的连续委托体验。
