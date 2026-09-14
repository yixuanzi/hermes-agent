# Feishu 输出卡片化改造（CardKit 三元素卡片）
 
改造依据：`~/wiki/01-Raw/AIAgent/hermes-feishu-card-output.md`
落地日期：2026-09-10（含真机截图反馈后的富文本渲染修正、委派卡片粒度调整、流式并发竞态修复、瞬态通知绕过卡片）
　　　　　2026-09-11 追加：折叠区把主 agent 的 terminal 命令折回工具行（见「富文本渲染细节」）
　　　　　2026-09-13 追加：卡片模式下的附件投递，以及话题内附件投递修复（见「附件：文件永远不进卡片」）
首个提交：`335002eccf feishu card output 0.1`
 
> **当前为 opt-in**：`FEISHU_CARD_OUTPUT` 默认 **false**，不配置就完全走原来的 text/post 路径。要用卡片需显式置 true。
 
## 改动文件
 
| 文件 | 说明 |
|---|---|
| `plugins/platforms/feishu/feishu_cardkit.py` | **新增**（~1280 行）。CardKit 卡片引擎：SDK 懒加载、`FeishuCardSession`、`FeishuCardOutputManager`、`build_card_json`、`normalize_markdown`、`format_trace_lines`、`close_owner` |
| `plugins/platforms/feishu/adapter.py` | `send()` / `edit_message()` / `delete_message()` 卡片拦截；turn 括起（`on_processing_start` / `on_processing_complete`）；`handle_delegate_card_event`；`_card_bypass_requested`；`_env_boolean_default_false`；`card_output` 配置；卡片模式附件（`_split_card_mode_attachments` / `_dispatch_card_attachments` / `_send_media_once`）；话题内附件重锚（`_send_attachment_message`） |
| `plugins/platforms/feishu/plugin.yaml` | 5 个新 optional_env |
| `gateway/run.py` | progress metadata 打 `hermes_progress`；心跳 metadata 打 `hermes_card_bypass`（**均仅 Feishu 平台**） |
| `tools/a2a_delegate_tool.py` | `_A2ADelegateSession` 增加 `agent_name`（仅用于卡片标题展示） |
| `.env.example` | 四个 FEISHU_CARD_* 示例项 |
| `tests/gateway/test_feishu_card_output.py` | **新增** 91 个测试 |
| `tests/gateway/test_feishu.py` | 106 个测试（含话题内附件投递 8 个、卡片关闭时的向后兼容锚点 1 个） |
 
## 卡片结构（JSON 2.0，走 CardKit API）
 
- ① `header.title` — 主 agent（默认 `🤖 Hermes`，本机 `.env` 设为 `🤖 Aegis`）；委派 `🛰️ 委派 · {agent}`
- ② `collapsible_panel`（`element_id=hermes_trace_panel`）内含单个 `markdown`（`hermes_trace`，`text_size=notation`）——执行中展开，收尾折叠并把标题改为「🔧 执行过程 · N 步」
- ③ `markdown`（`hermes_body`）——富文本结果区，走 `content` 流式接口（打字机）
`config`：`streaming_mode` + `streaming_config`（`print_strategy=fast`）+ `summary` + `update_multi=true` + `width_mode=fill`。
 
更新路径：
- trace → `PUT /cards/:id/elements/:id`（全量替换元素，gateway 给的本来就是累计文本）
- body → `PUT /cards/:id/elements/:id/content`（传累计全量文本，前缀增长 → 打字机）
- 收尾 → `PATCH /elements/:id`（折叠面板）+ `PATCH /settings`（关流式、写 summary）
## 什么进卡片，什么不进
 
卡片正文只装**这一轮的答案**。凡是「关于会话的通知」而非「对话的一轮」，都以普通文本消息发送，不进卡片。
 
判定集中在 `FeishuAdapter._card_bypass_requested(metadata)` 一处（有决策表测试）：
 
| metadata | 去向 |
|---|---|
| 无 / 仅 `thread_id` | 卡片 body |
| `hermes_progress` | 卡片折叠区（执行过程） |
| `hermes_card_bypass` | **普通文本消息** |
| `non_conversational`（gateway 既有的 lifecycle 标记） | **普通文本消息** |
| `hermes_progress` + 任一 bypass 标记 | 卡片折叠区（progress 优先） |
 
已打 bypass 的发送点：
 
| 消息 | 位置 |
|---|---|
| `⏳ Working — N min — iteration i/N, <tool>` 心跳 | `gateway/run.py` `_notify_long_running`（仅 Feishu） |
| `✅ Delegate interaction resolved.` / `✅ Clarification response sent.` | `adapter._send_delegate_resolution` |
| `⌛ That approval had already expired …` | 审批按钮回调的纠正通知 |
 
这些都是按钮回调或存活信号，之前会被追加进正在流式输出的卡片正文，**在委派 agent 的回答中间插一条**。心跳自己会 `edit_message` 复用同一个气泡 —— 因为 bypass 后拿到的是真实 message_id，编辑走 im 接口，不会误认成卡片区块。
 
> 还有一类同源但本次未改的：restart / online / session-split / `⚡ Interrupting` 等 lifecycle 通知。它们走 `_non_conversational_metadata`，而该函数目前只为 Discord 打标记。如果要一并清出卡片，把 Feishu 加进那个函数即可 —— adapter 侧已经认 `non_conversational`。
 
## ⚠️ 已修正的四个坑（全部由真机截图暴露）
 
### 1. `content` 是纯文本，不是 `{"text": ...}`
 
方案文档里的示例 `.content(json.dumps({"text": acc}))` **是错的**。官方文档明确：`content` = 「新的全量文本内容。使用时请注意转义为字符串」，类型 `string`。SDK 模型 `ContentCardElementRequestBody.content: Optional[str]` 也是 str。
 
传 JSON wrapper 会返回 **code 0（成功）**，但卡片把 wrapper 连同转义后的 `\n` 一起当字面文本渲染出来。方案文档当时只核对了返回码，没看渲染结果。
 
**教训：CardKit 的 code 0 不代表渲染正确。**
 
### 2. 30KB 上限要按**字节**算，不是字符
 
卡片体积上限 30KB（超限报 200860）。中文 UTF-8 是 3 字节/字符，原先 `MAX_BODY_CHARS = 20000` 实际是 60KB —— 中文长回答会整条更新被拒。已改为 `MAX_BODY_BYTES` / `MAX_TRACE_BYTES`，截断按字节且不切断多字节字符（`decode(errors="ignore")`）。
 
### 3. `wide_screen_mode` 是 JSON 1.0 字段
 
2.0 用 `width_mode`（`default` / `compact` / `fill`）。宽度直接决定 markdown 表格和代码块是否会挤成一团，现设 `fill`。
 
### 4. 委派流式 delta 竞态 —— 答案开头「一字一段」
 
**现象**：委派卡片正文前半段每个 token 单独成段（`我先` / `加` / `载该` / `工具的` …），后半段正常。
 
**根因**：`_FeishuDelegateOutputAdapter.emit()` 把**每个事件都 `create_task` / `run_coroutine_threadsafe` 成独立的 fire-and-forget 任务**，彼此没有互斥。原逻辑先读「我有 body block 了吗」，再 `await` 建 block 的网络调用 —— 开头那一串 delta 全都在第一个任务还在 await 时看到「还没有 block」，于是各自建了一个 block。body block 之间用空行拼接，所以开头渲染成一段一个 token；等状态落定后的 delta 才正确累加，后半段就正常了。
 
**修法**（两条，缺一不可）：
1. **先同步累加，再 await**：`state["text"] += content` 在任何 await 之前完成，因此并发任务无法在这里交错；每次渲染写的都是完整累计文本 —— 片段不会丢、不会乱序，且每次更新都是上一次的前缀超集（这正是打字机动画的前提）。
2. **per-owner `asyncio.Lock`** 包住「建 block 还是改 block」的决策，以及会清掉该状态的段落边界事件（`tool_call` / `ai` / `error`）。锁内重读 `states.get(owner)`，排在建 block 之后的任务只会去扩展它。
顺带修掉一个相邻的重复渲染：`states[owner]` 表示**当前文本段**，`tool_call` 会清掉它，所以原先拿它判断「这个 delegate 流式过吗」，会让「流式 → 调工具 → 给答案」的交互看起来从未流式过，`ai` 事件把整份最终答案又追加了一遍。现在「是否流式过」用独立的 per-owner 标记表示，工具边界不清它。
 
**测试环境教训**：这个 bug 单元测试没抓到，因为 fixture 里 `_run_blocking` 是个不 await 任何东西的协程 —— 从不让出事件循环，把所有代码路径意外地串行化了，恰好掩盖了这类并发 bug。现在 fixture 会 `await asyncio.sleep(0)`，与真实的线程池跳转一致；改之后立刻复现出 `'先看\n\n一下。\n\n结果如下。'`。
 
## 富文本渲染细节
 
- **代码块围栏必须行首**：官方「若 content 中含有代码块，你需将代码块前后的空格去掉，否则可能导致代码渲染失败」。agent 常把围栏缩进在列表项里，`normalize_markdown()` 把围栏行 de-indent（代码内容本身的缩进保留）。
- **单个 `\n` 是软换行，渲染时可能被忽略**（官方原话）。所以 trace 区不能靠 `\n` 分行 —— `format_trace_lines()` 把每个步骤渲染成 `- ` 列表项，已经是 markdown 块的行（`-`/`>`/`#`/`|`/`1.`）原样透传。body 区**不做任何改写**：那是 agent 自己的 markdown，标题/列表/表格/代码全依赖原始行结构。
- **一条 progress 消息 = 一个步骤，即使它跨多行**。主 agent 的 terminal 调用在 gateway 侧渲染成「`💻 terminal` 标题行 + 围栏命令块」（`gateway/run.py` 的 `_code_block_short` / `_code_block_full`），委派侧则是单行 `` `tool` terminal: cmd ``。逐行处理会有两个后果：命令行被当普通行加上 `- ` 前缀**落进代码框里**（真机截图里的 `- mcporter call 'exa.…'`），以及围栏的每一行各算一步，让「执行过程 · N 步」把一次 shell 调用算成 3~4 步。`_split_trace_steps()` 因此做围栏感知的切分：
  - 单行命令 → 折叠到工具行做行内代码：``- 💻 terminal: `cmd` ``（与委派侧一致）；行内代码的反引号数按命令内最长反引号串加一，命令里含 `` ` `` 也不会破格；
  - 多行脚本（verbose 模式）→ 保留代码块，但挂在同一个步骤下，块内各行不再被加前缀；
  - 无标题的裸围栏（连续 terminal 调用时 gateway 会省掉重复标题）→ **不**折叠进上一条命令，自成一步，否则一步会显示成执行了两条命令。
  `_count_trace_steps()` 直接数切分后的步骤，所以标题里的步数与看到的条目数一致。
- markdown 元素支持完整 CommonMark（除 HTMLBlock）+ 部分 HTML；表格除表头外最多显示 5 行、单个元素最多 4 个表格。
- `element_id` 规则：仅字母数字下划线、字母开头、**≤20 字符**（现有三个 id 均合规，有测试守着）。
## 附件：文件永远不进卡片

**结论先行**：飞书的文件发送方法（`send_document` / `send_voice` / `send_video` / `send_image_file`）走的是 `im.v1.file.create` + `im.v1.message`，**根本不经过 `_send_via_card`**，所以附件天然是独立消息。网关主链路（`base.py` 的 `_process_message_background`）在卡片模式下也照常先抽取、再发正文（进卡片）、最后上传附件 —— 封卡的 `on_processing_complete` 在这之后才执行。也就是说：**卡片没有从架构上挡住文件发送**。

卡片真正引入的缺口是另一件事：**卡片正文是一个新的文本入口，而它不跑附件抽取**。中途旁白（`stream_consumer._send_commentary`）和委派正文都是直接调 `adapter.send()` 进卡片的，它们携带的文件引用以前只会变成卡片里的一行字。

`FeishuAdapter._split_card_mode_attachments()` 补上这个缺口，规则与非卡片模式**完全一致**（用的就是网关那套 helper，不是自己写的正则）：

| 门 | 作用 |
|---|---|
| `_card_media_route()` 返回 `None` | 卡片关闭 / 不在回合内 → 整个特性 no-op，legacy 路径逐字节不变 |
| `hermes_card_bypass` / `hermes_progress` | 生命周期通知和工具链路不是附件请求 |
| `extract_media` + `filter_media_delivery_paths` | 显式 `MEDIA:` 标记，含 `[[audio_as_voice]]` / `[[as_document]]` |
| `extract_local_files` + `filter_local_delivery_paths` | 裸路径（deliverable mode），与关闭卡片时一致 |

复用这些 helper 的附带好处：代码块、行内代码、JSON 里的路径已经被屏蔽，所以一段**讲解** MEDIA 语法的回答不会误发文件；以后网关规则变了这边自动跟随。

抽出来的文件经 `_dispatch_card_attachments()` 按类型分派（音频→voice、视频→media、图片→image、其余→file），**每个文件一条独立消息**；卡片正文换成清理后的文本 + 一行回执（`FEISHU_CARD_ATTACHMENT_NOTE`）。这行回执顺带保证正文非空 —— 纯 `MEDIA:` 的回复不会退化成空 block（`manager.deliver` 会拒绝空文本）。

### 为什么需要一个「本回合已投递」账本

同一个文件在一个回合里有**两个**入口：网关自己的抽取直接调发送器，安全网也调发送器。此外 `base._send_with_retry` 在瞬时失败后会用**完全相同的 content** 重调 `send()`。没有账本，这两种情况都会把同一个文件发两遍。

`_send_media_once()` 包住四个发送器：

- **上传前就占位**（否则重试窗口内会双发）；
- **失败即释放**，所以一次失败不会把这个文件在本回合内「烧掉」；
- 命中已投递时返回 `success=True` —— 用户已经有这个文件了，不该再收到一条失败告警；
- 只在卡片回合活跃时生效，`FEISHU_CARD_OUTPUT=false` 时账本完全不介入；
- **作用域只限一个回合**：下一轮用户再要同一个文件仍然能重发。

### 上传失败

失败时仍走 `_notify_media_delivery_failure`，告警经 `send()` 落在**卡片末尾**。这是现状、也是有意保留的：用户看得到失败，不需要额外一条独立消息。

### 与卡片无关的一条：话题（topic）里发不出附件

真机复现「卡片模式下文件发送失败」时，日志给出的其实是
`[Feishu] Failed to send media (.txt): [99992402] field validation failed` ——
文件已经上传成功（拿到了 `file_key`），失败的是**承载它的那条消息**。

原因在 `_send_raw_message`：话题里的消息以 `receive_id_type=thread_id` 下发，
飞书对**文本和卡片**接受这种键法，对**任何附件类型**（audio / file / media / image，
以及带附件的 post）一律回 99992402。所以同一个文件在主会话里发得出去，在话题里就丢了。
代码里原本只为 audio 打了一个补丁，file 没有。

`_send_attachment_message()` 把这个补丁推广到全部附件类型：99992402 的含义是
「换个锚点」而不是「放弃」—— 先用 reply API 挂在话题内某条消息上重发（文件仍落在话题里），
仍失败才退到主会话平铺发送（文件发错位置也好过发不出去）。其他错误码原样上报、不重试。

**这一条与 `FEISHU_CARD_OUTPUT` 无关**，关闭卡片同样会命中；只是本次排查时一起修了。

## 卡片边界规则
 
一张卡片只属于一个「说话人」（owner），**且只装一次交互**：
 
| 边界 | 触发 |
|---|---|
| owner 变化 | `main` → `delegate:<a2a context_id>` → `main` = 三张卡 |
| 同 turn 内两次委派 | 不同 context_id = 两张卡 |
| **委派循环内每一轮** | delegate 的 turn-final `ai` 事件 → 封卡；下一轮开新卡 |
| body 超 `MAX_BODY_BYTES` | 封卡开「（续）」卡 |
| turn 结束 | `on_processing_complete` 封卡 |
 
**为什么循环内需要单独的边界**：foreground loop 整个循环共用一个 A2A context_id，owner 标签全程不变，所以光靠 owner 变化封不了卡 —— 用户与远程 agent 的每一次追问都会堆进第一张卡。`manager.close_owner(owner=...)` 在 `ai` 事件到达时封掉当前卡（按 owner 限定，迟到事件不会误封已交给别人的卡）。
 
## 委派事件渲染策略
 
| 事件 | 卡片模式下的处理 |
|---|---|
| `ai_delta` | 同步累加 → 锁内建/改 body block（打字机） |
| `ai` | 仅当整段交互从未流式过才补最终文本 → **封卡** |
| `tool_call` | 写入折叠区；结束当前文本段（下批 delta 另起一段） |
| `status` | **丢弃**（见下） |
| `error` | 写入 body（用户必须看到） |
| `approval_request` / `clarify_request` | 仍走原有交互卡片，不进三元素卡；其「已处理」回执走 bypass 发普通消息 |
 
`status` 事件（`entered foreground loop` / `return to main` / `input timeout` / `interrupted`）是传输层管道信息，描述的是委派机制而非用户的任务，卡片模式下直接吞掉（debug 日志留痕）。agent 自己会叙述结果 —— a2a 工具把 `loop_exit_reason` 和 `final_response` 交回给它 —— 所以没必要让循环簿记出现在聊天里。**该抑制只作用于卡片模式**：`FEISHU_CARD_OUTPUT=false` 时 status 仍按原样发消息（有测试守着）。
 
## 关键实现要点
 
- **trace vs body vs 普通消息**：全部靠 metadata 键分流（见上方「什么进卡片」）。gateway 侧的两个标记都只在 Feishu 平台设置，其他平台的 metadata 形状完全不变。
- **合成 message_id**：卡片区块以 `hermes-card:` 前缀的句柄返回给 gateway，`edit_message` / `delete_message` 再映射回区块。真实卡片 message_id 不外泄，避免 gateway 用 im API 去改/删卡片。走 bypass 的消息拿到的是真实 message_id，编辑/删除照常走 im 接口。
- **区块模型**：gateway 流式输出 = 发消息 + 反复编辑 + 段落断点发新消息。每次「发」创建一个 block，「编辑」重写该 block，区域文本 = 该类 block join。主 agent 侧 stream consumer 与 progress 各自是单个顺序任务，所以只有委派侧需要额外加锁（见坑 4）。
- **删除语义**：stream consumer 的 fresh-final 会「重发完整答案 + 删旧预览」，静默标记会撤回预览。因此实现了 `delete_message` 撤回卡片区块，否则卡片里会出现两份答案。
- **失败重试**：更新被拒时把 dirty 标记重新竖起来（上限 3 次）。原先失败即静默丢弃，该区域会一直停在旧内容直到下次编辑到来。
- **流式模式自动关闭**：飞书在卡片安静一段时间后会自己关掉 streaming（200850 / 300309）。命中即调 `PATCH /settings` 重开并重试一次，否则慢工具之后的答案会全丢。200810（用户正在与卡片交互）走普通重试。
- **降级**：CardKit 不可用 / 建卡失败 → 该 route 进入 300s 冷却，回落到原 text/post 路径。turn 外的输出（slash 命令回复、cron 推送）不建卡。
- **限流**：`content` 接口 1000 次/分钟、50 次/秒（比方案文档写的 5 QPS 宽得多）。`flush_interval`（默认 0.4s，`HERMES_FEISHU_CARD_FLUSH_INTERVAL`）合并突发；所有变更在 session 锁下顺序取 `sequence`。
- **锁的层次**（避免死锁，获取顺序固定）：per-owner 委派锁 → per-route 建卡锁 → per-session 渲染锁。
- **有意没做**：收尾时用 `PUT /cards/:card_id` 全量替换以把 header 变绿。那会在打字机动画未播完时整卡替换，有闪烁/截断风险；完成信号已由 `summary`（✅ 已完成）+ 折叠面板承担。
## 配置
 
| 变量 | 默认 | 说明 |
|---|---|---|
| `FEISHU_CARD_OUTPUT` | **`false`** | opt-in。接受 `true/1/yes/on`（大小写与空白无关）；其他值一律 off 并 warn。yaml `feishu.card_output` 优先于环境变量 |
| `FEISHU_CARD_TITLE` | `🤖 Hermes` | 主卡标题（本机 `.env` 设为 `🤖 Aegis`） |
| `FEISHU_CARD_DELEGATE_TITLE` | `🛰️ 委派 · {agent}` | 委派卡标题（本机 `.env` 追加了 `\| 输入 /main 返回主会话`） |
| `FEISHU_CARD_TRACE_TITLE` | `🔧 执行过程` | 折叠区标题 |
| `FEISHU_CARD_ATTACHMENT_NOTE` | `📎 已发送文件：{names}` | 附件回执行（见「附件」一节） |
| `HERMES_FEISHU_CARD_FLUSH_INTERVAL` | `0.4` | 更新合并窗口（秒） |
 
> 注意：`_to_boolean()` 只认字面量 `"true"`，所以 `FEISHU_CARD_OUTPUT=1` 用它解析会被读成 off。为此加了 `_env_boolean_default_false()`，与既有的 `_env_boolean_default_true()` 对称，接受同一组拼写。
 
## 验证状态
 
- `tests/gateway/test_feishu_card_output.py` + `tests/gateway/test_feishu.py` 197 passed
- `tests/gateway -k "feishu or media or deliver"` 793 passed，2 failed
  （`test_feishu_channel_prompts.py::test_inbound_event_carries_channel_prompt`、
  `test_stream_consumer_thread_routing.py::TestFeishuFallbackThreadRouting::test_create_uses_thread_id_when_available`
  —— 已用改前基线复现，属临时验证环境未 bind lark SDK 全局的既有失败，非本次回归）
- `test_run_cleanup_progress` / `test_display_config` / `test_telegram_noise_filter` 155 passed（心跳与 progress 清理路径未受影响）
- ruff 全部通过
- 卡片 JSON 结构已逐字段对照官方文档核对（`card-json-v2-structure`、`content-components/rich-text`、`containers/collapsible-panel`、`cardkit-v1/card-element/content`）
### 三个刻意设计的「防回归陷阱」
 
1. 单元测试**不**对 `content` 做 JSON 解码 —— 一旦有人把 wrapper 加回来会直接测试失败，而不是静默地在群里渲染出 JSON。
2. fixture 的 `_run_blocking` 会 `await asyncio.sleep(0)` —— 与真实线程池跳转一致，不会再把并发路径意外串行化。
3. `test_bypass_decision_table` 直接锁死「进卡片 / 进折叠区 / 发普通消息」的判定表，调用方散落在 gateway 与 adapter 两侧，都依赖这四条答案。
## 参考文档
 
- 流式更新文本：https://open.feishu.cn/document/cardkit-v1/card-element/content
- 卡片 JSON 2.0 结构：https://open.feishu.cn/document/feishu-cards/card-json-v2-structure
- 富文本（Markdown）组件：https://open.feishu.cn/document/feishu-cards/card-json-v2-components/content-components/rich-text
- 折叠面板组件：https://open.feishu.cn/document/feishu-cards/card-json-v2-components/containers/collapsible-panel