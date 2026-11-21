# lark_user：用你本人的 Lark 账号自动回复

`lark_user` 是 Hermes Gateway 的一个平台插件。它通过本机的 `lark-cli`，以**你本人的用户身份**定期读取两类消息：

- 别人发给你的**私聊**；
- 群里 **@你** 的消息。

每个会话会启动一个 Hermes Session，agent 写好答案后，以**你本人的名义**回复。为表明这是 AI 代答，每条回复都带固定的前缀。默认先审核：每条回复先以**审核卡片**发给你，你可以**通过**（正式发出）、**修订**（在输入框写修改意见，agent 改好后再次提交审核）或**拒绝**（不回复）。

> 想让大家和一个**机器人**对话，请用 `feishu` 平台；想让助理替**你本人**起草或回复别人发给你的消息，用 `lark_user`。

---

## 目录

1. [工作原理](#1-工作原理)
2. [前置条件](#2-前置条件)
3. [快速开始](#3-快速开始)
4. [审核模式怎么用](#4-审核模式怎么用)
5. [配置项详解](#5-配置项详解)
6. [常用配置示例](#6-常用配置示例)
7. [安全设计](#7-安全设计)
8. [排错](#8-排错)
9. [已知限制](#9-已知限制)

---

## 1. 工作原理

```
          每 interval 秒一次（每个来源各自一个循环）
lark-cli 搜索 ──► 过滤（自己/bot/@所有人/白名单/过期/限流…）──► 去重（按 message_id 持久化）
                                                                  │
                                                                  ▼
                                      按会话隔离策略构造 Session ──► Hermes agent 运行
                                                                  │
                                       只取最终答案 ◄──────────────┘
                                          │
                     ┌────────────────────┴────────────────────┐
               delivery: review                           delivery: auto
       审核 bot 给你发审核卡片                         直接以你的名义回复对方
   ✅ 通过 → 以你的名义回复对方
   ✏️ 修订 → 意见交回 agent 重写 → 新的审核卡片
   🚫 拒绝 → 不回复
```

- **只发最终答案**：agent 执行过程中的工具进度、中间说明、心跳、出错提示都不会发给对方，只有这一轮的最终答案会发出（审核模式下也只生成一条草稿）。
- **消息来源**：
  - 私聊：`lark-cli im +messages-search --chat-type p2p`
  - @我：`lark-cli im +messages-search --is-at-me --chat-type group`，再在本地按消息的 @ 列表确认确实 @了你本人，因为这个接口会把 @所有人 的消息也一起返回。
- **首次启动**：已有的消息一律视为历史，只回复之后新来的消息。
- **状态文件**：`<HERMES_HOME>/lark_user/<你的open_id>.json`，保存已处理的消息、轮询进度和待审核草稿。重启后不会重复回复，也不会丢失草稿。
- **延迟**：Lark 搜索索引约有 15 秒延迟，按默认 15 秒的轮询周期，对方发消息后大约 15–30 秒开始处理。

---

## 2. 前置条件

1. 安装 `lark-cli` 并确保它在 `PATH` 中（也可以用 `cli_bin` 指定绝对路径）。
2. **为 Hermes 登录 lark-cli**（务必带上 `HERMES_HOME`，原因见下方说明）：

   ```bash
   HERMES_HOME=~/.hermes lark-cli auth login
   HERMES_HOME=~/.hermes lark-cli whoami --as user    # 确认应用（appId）和 onBehalfOf 是你本人
   HERMES_HOME=~/.hermes lark-cli config show          # 确认 "workspace": "hermes"
   ```

   > **为什么要带 `HERMES_HOME`：lark-cli 的工作区机制**
   >
   > lark-cli 会识别自己是否运行在 AI agent 里：环境变量中只要有**非空的** `HERMES_HOME`，它就使用一套独立的 **`hermes` 工作区**（`~/.lark-cli/hermes/config.json`），否则使用默认的 **`local` 工作区**（`~/.lark-cli/config.json`）。两个工作区各自有各自的应用、profile 和登录状态，值是什么无所谓，有就切换。
   >
   > Hermes gateway 进程里一定有 `HERMES_HOME`，所以插件调用的 lark-cli **永远使用 `hermes` 工作区**；而你在普通终端里直接运行 `lark-cli`，用的是 `local` 工作区。后果是：
   > - 在普通终端执行 `lark-cli auth login`，登录的是 `local` 工作区，**插件用不上**；
   > - 两个工作区可能绑定不同的应用，你会在终端和 Hermes 里看到不同的 appId 和 open_id（同一个人在不同应用下的 open_id 不同）。
   >
   > 所以本文所有 lark-cli 命令都写成 `HERMES_HOME=~/.hermes lark-cli …`，这样看到的就是插件实际使用的身份。插件启动时也会在日志里记录所用的工作区和应用：`lark_user: lark-cli workspace=hermes profile=… app=cli_…`。

3. 用户 token 需要以下 scope：

   | scope | 用途 |
   |---|---|
   | `search:message` | 搜索私聊和 @我 的消息 |
   | `im:message.p2p_msg:get_as_user` | 读取私聊 |
   | `im:message.group_msg:get_as_user` | 读取群消息（包括群上下文） |
   | `im:message.send_as_user`、`im:message` | 以你的身份发送和回复 |

   可以用 `HERMES_HOME=~/.hermes lark-cli auth check --scope "search:message im:message.send_as_user"` 检查。

4. **审核卡片需要一个专用的 Lark 应用（审核应用）**。卡片按钮的点击回调只会推送给应用，用户身份收不到；而且同一个应用有多个长连接时，每次点击只会随机推给其中一个。所以这个应用必须**专门用于审核卡片**，不能和 Hermes 的 `feishu` 平台或 lark-cli（`hermes` 工作区）登录用的应用是同一个。没有配置审核应用时，自动退回文本命令审核（见第 4 节）。

   在飞书 / Lark 开发者后台：
   1. 创建一个企业自建应用，开启**机器人**能力。
   2. 开通权限 `im:message:send_as_bot`（发卡片）和 `im:message`（更新卡片）。
   3. 在“事件与回调 → 回调配置”中选择**使用长连接接收回调**，并添加回调 `card.action.trigger`（卡片回传交互）。
   4. 把应用的**可用范围**设为包含你本人，然后发布版本。
   5. 把凭证写进 `~/.hermes/.env`：

      ```bash
      LARK_USER_INTERACTION_APP_ID=cli_xxxxxxxx
      LARK_USER_INTERACTION_APP_SECRET=xxxxxxxx
      ```

   插件只把凭证放进 lark-cli 子进程的环境变量，不会写入你的 lark-cli 配置，也不会出现在命令行参数里。审核 bot 通过 union_id 找到你，卡片发在审核 bot 和你的私聊里。

5. 用户 token 约一周内不刷新就会过期。过期后插件会停止轮询，在 `hermes gateway status` 中显示 `auth_required`。此时 token 失效，也没法给你发提醒，需要你重新执行 `HERMES_HOME=~/.hermes lark-cli auth login`（不带 `HERMES_HOME` 会登录到另一个工作区，无效），然后重启 gateway。

---

## 3. 快速开始

在 `~/.hermes/config.yaml`（多 profile 时是对应 profile 的 `config.yaml`）中加入：

```yaml
platforms:
  lark_user:
    enabled: true
    allow_from: [ou_aaaa, ou_bbbb]     # 哪些人私聊你时会触发回复（open_id）
    group_allow_from: [oc_cccc]         # 哪些群里 @你 时会触发回复（chat_id）
```

然后重启 gateway：

```bash
hermes gateway restart        # 或 hermes gateway run
hermes gateway status         # 确认 lark_user 已连接
```

默认行为如下：
- 审核模式：草稿发到你和自己的会话。
- 回复格式为 Markdown 富文本（post），引用原消息回复。
- 回复前缀为 `[🤖 AI 代答] `。
- 不响应 @所有人。
- agent 不能使用 terminal。

> **怎么找 open_id / chat_id**（open_id 按应用区分，必须在 `hermes` 工作区下查，否则白名单对不上）
> - 某人的 open_id：`HERMES_HOME=~/.hermes lark-cli contact +search-user --query "姓名" --as user`；也可以在日志里找（DEBUG 级别）。
> - 群的 chat_id：`HERMES_HOME=~/.hermes lark-cli im +chat-search --query "群名" --as user`。

---

## 4. 审核模式怎么用

`reply.delivery: review`（默认）时，每个待发出的回复都先交给你审核。审核有两种呈现方式，由 `review.interactive` 决定；不配置时，有审核应用就用卡片，否则用文本命令。

### 4.1 审核卡片（推荐）

审核 bot 会在它和你的私聊里发来一张卡片：

```
┌ 📝 待审核回复 #3 ───────────────────────────┐
│ 来自：Alice（群：项目组）                    │
│ 原消息：周五能发版吗？                       │
│ （第 1 次修订，意见：……  ← 仅修订稿显示）    │
│ ─────────────────────────────────────────── │
│ （agent 起草的回复）                          │
│ ─────────────────────────────────────────── │
│ [ 修改意见（点“修订”时必填）              ]   │
│ [✅ 通过]  [✏️ 修订]  [🚫 拒绝]                │
└─────────────────────────────────────────────┘
```

| 操作 | 效果 | 卡片变为 |
|---|---|---|
| ✅ 通过 | 以你的名义把这条回复发给对方（自动加前缀和后缀） | 绿色“✅ 已发送 #n”，按钮消失 |
| ✏️ 修订 | 把输入框里的修改意见连同上一版草稿交回**原会话**，agent 按意见重写；改好后发来**新的审核卡片**（标注“第 k 次修订”），可以反复修订 | 蓝色“✏️ 修订中 #n”，按钮消失 |
| 🚫 拒绝 | 这条消息不回复 | 灰色“🚫 已拒绝，不回复 #n”，按钮消失 |

- 修订时输入框不能为空，否则卡片会提示“请先填写修改意见”，草稿保持待审核。
- 发送失败时，卡片变为红色“❌ 发送失败，可重试”并保留按钮，可以再点“通过”。
- 修改意见只在对方的会话里作为“来自你本人的审核意见”交给 agent，不会发给对方。
- 只有发在审核 bot 与你私聊里的、对应草稿自己的那张卡片上的点击才有效；已经处理过的卡片再点不会重复执行。
- 超过 `review.ttl`（默认 24 小时）未处理的草稿会自动作废，卡片变为“⌛ 已过期，未回复”。

### 4.2 文本命令（无审核应用时的后备）

`review.interactive: text`（或没有配置审核应用）时，草稿以消息形式发到**你和自己的会话**，你在同一个会话里回复命令：

| 命令 | 效果 |
|---|---|
| `/send [n]` | 通过：发出草稿 n（不写 n 时为最新一条） |
| `/drop [n]` | 拒绝：不回复 |
| `/revise n 修改意见` | 修订：agent 按意见重写后发来新的待审核草稿 |
| `/edit n 新内容` | 不经 agent，直接用“新内容”代替草稿 n 发出（同样会加前缀和后缀） |
| `/list` | 列出所有待审核草稿 |

命令只认你本人在你和自己的会话里发的；每条命令只执行一次。

### 4.3 不审核

设为 `reply.delivery: auto` 后直接发送，不经审核。可以全局设置，也可以只对某个会话设置（见 5.9）。

---

## 5. 配置项详解

所有配置都在 `config.yaml` 的 `platforms.lark_user` 下。时长可以写成数字（秒）或带单位的字符串：`15`、`"15s"`、`"10m"`、`"24h"`、`"1d"`。

### 5.1 基础

| 配置项 | 类型 / 默认值 | 效果 |
|---|---|---|
| `enabled` | bool / `false` | 是否启用本平台。 |
| `cli_bin` | str / `lark-cli` | lark-cli 可执行文件，可以写 PATH 中的名字或绝对路径。 |
| `lark_cli_profile` | str / `""` | 传给 `lark-cli --profile`，选的是 **`hermes` 工作区里**的 profile（空表示该工作区当前生效的 profile）。不同 Hermes profile 可以绑定不同的 Lark 账号；同一个 Lark 账号同一时间只允许一个 Hermes profile 轮询，重复启动会被拒绝。 |
| `cli_timeout` | 时长 / `60s` | 单次 lark-cli 调用的超时时间。 |

### 5.2 谁能触发回复（访问控制）

| 配置项 | 类型 / 默认值 | 效果 |
|---|---|---|
| `dm_policy` | `allowlist` \| `open` \| `disabled` / `allowlist` | 私聊策略。`allowlist`：只回复 `allow_from` 中的人；`open`：谁私聊你都回复；`disabled`：不回复任何私聊。 |
| `allow_from` | open_id 列表 / `[]` | 私聊白名单。可以写成 YAML 列表，也可以写成逗号分隔的字符串。 |
| `group_policy` | `allowlist` \| `open` \| `disabled` / `allowlist` | 群策略。`allowlist`：只在 `group_allow_from` 中的群里响应 @你；`open`：任何群都响应；`disabled`：不响应任何群。 |
| `group_allow_from` | chat_id 列表 / `[]` | 群白名单。 |
| `allow_bots` | bool / `false` | 是否回复 bot 发来的消息。默认不回复，避免和机器人互相刷屏。 |
| `allow_external` | bool / `false` | 是否回复外部租户（其他公司）的人。 |

- 默认两个策略都是 `allowlist` 且白名单为空，也就是**不回复任何人**，必须你主动配置。
- 设为 `open` 意味着任何能给你发消息的人都能让助理以你的名义回话。因此必须在 `.env` 里同时设置 `LARK_USER_ALLOW_ALL_USERS=true`（或 `GATEWAY_ALLOW_ALL_USERS=true`），否则 gateway 拒绝启动。
- 不在白名单里的消息在插件入口就被丢弃，不会进入 gateway，也不会以你的名义给陌生人发配对码。

### 5.3 轮询（`poll`）

| 配置项 | 类型 / 默认值 | 效果 |
|---|---|---|
| `poll.interval` | 时长 / `15s`，最小 `5s` | 基础轮询周期。每次轮询约触发 3 次 Lark API 调用，耗时 4–7 秒。 |
| `poll.idle_interval` | 时长 / `60s`（不会小于 `interval`） | 连续 `idle_after` 次没有新消息后改用这个周期；一有新消息立即恢复 `interval`。 |
| `poll.idle_after` | int / `10` | 连续多少次空轮询后放慢。 |
| `poll.jitter` | 0–0.5 / `0.2` | 周期随机抖动比例（±20%），避免多个来源同时打接口。 |
| `poll.active_hours` | `"HH:MM-HH:MM"` / `""` | 只在这个时段内轮询，空表示全天。支持跨午夜，例如 `"19:00-09:00"` 表示只在下班时间代答。按本机时区计算。 |
| `poll.max_backoff` | 时长 / `10m` | 出错时指数退避的上限；遇到限流退避得更快。 |
| `poll.lookback` | 时长 / 自动 | 每次查询往回多看的时间窗。实际取值至少是 `max(2m, 2×interval)`，因为 Lark 返回的时间只精确到分钟、索引又有延迟。一般不用配置。 |
| `poll.sources.dm.enabled` | bool / `true` | 是否轮询私聊。注意：只要启用了审核模式，私聊搜索就会继续运行（你的审核命令要靠它读取），但不会再回复任何私聊。 |
| `poll.sources.dm.interval` | 时长 / 同 `interval` | 私聊来源单独的周期。 |
| `poll.sources.mention.enabled` | bool / `true` | 是否轮询群 @我。 |
| `poll.sources.mention.interval` | 时长 / 同 `interval` | @我 来源单独的周期。 |
| `poll.sources.mention.include_at_all` | bool / `false` | 是否响应 @所有人。默认不响应。 |

### 5.4 消息处理

| 配置项 | 类型 / 默认值 | 效果 |
|---|---|---|
| `max_message_age` | 时长 / `10m` | 早于这个时间的消息不再回复。例如 gateway 停了一晚，重启后不会去回复昨天的消息。 |
| `text_batch_delay_seconds` | 秒 / `2`（上限 2） | 同一会话短时间内连发的多条消息合并成一轮处理，避免对方连发三句、你回三次。设为 `0` 关闭合并。 |
| `rate_limit.per_chat` | int / `5` | 每个会话每分钟最多处理几条消息，超出的静默跳过；`0` 表示不限制。 |

### 5.5 会话隔离（Session Key）

| 配置项 | 可选值 / 默认值 | 效果 |
|---|---|---|
| `session.dm_scope`（也可以直接写 `dm_scope`） | `per_chat` \| `per_message` / `per_chat` | `per_chat`：和每个人的私聊各一个会话，有上下文记忆；`per_message`：每条消息一个独立会话，没有记忆。 |
| `session.group_scope`（也可以直接写 `group_scope`） | `per_chat` \| `per_chat_user` \| `per_thread` \| `per_message` / `per_chat_user` | 见下表。 |

| `group_scope` | 一个会话对应 | 适合 |
|---|---|---|
| `per_chat` | 整个群（所有 @你 的人共享上下文） | 小团队群，大家围绕同一件事讨论 |
| `per_chat_user` | 群里的每个人 | 默认值，不同人的问题互不干扰（依赖 gateway 的 `group_sessions_per_user`，默认开启） |
| `per_thread` | 每个话题；不在话题里的 @ 各自独立 | 话题群 |
| `per_message` | 每次 @ | 一问一答、不需要记忆 |

会话 key 统一由 gateway 生成，因此多 profile 的命名空间、`/new` 和会话过期策略都照常生效。

### 5.6 回复（`reply`）

| 配置项 | 类型 / 默认值 | 效果 |
|---|---|---|
| `reply.delivery` | `review` \| `auto` / `review` | `review`：先发草稿给你审核；`auto`：直接发出。 |
| `reply.format` | `text` \| `post` \| `card` / `post` | `text`：纯文本；`post`：Markdown 富文本（加粗、列表、代码、链接）；`card`：卡片，前缀放在卡片标题里，后缀放在底部备注里。 |
| `reply.target` | `reply` \| `thread` \| `send` / `reply` | `reply`：引用原消息回复；`thread`：回复到话题里；`send`：在会话里新发一条，不引用原消息。 |
| `reply.at_sender` | bool / `false` | 在群里回复时 @提问的人（引用回复本身已经会通知对方）。 |
| `reply.prefix` | str / `"[🤖 AI 代答] "` | **必填，不能为空。** 加在每条回复最前面，用来表明是 AI 代答。设为空字符串会导致 gateway 拒绝启动本平台。 |
| `reply.suffix` | str / `""` | 加在回复最后，例如 `"\n—— 如需本人回复请留言「转人工」"`。 |
| `reply.card.title` | str / `"🤖 {user_name} 的助理"` | `format: card` 时的卡片标题，前缀会自动加在它前面。 |

`prefix`、`suffix`、`card.title` 支持以下模板变量：

| 变量 | 含义 |
|---|---|
| `{user_name}` | 你的 Lark 名字 |
| `{sender_name}` | 提问人的名字 |
| `{time}` | 发送时间 `HH:MM` |
| `{date}` | 发送日期 `YYYY-MM-DD` |

长回复按大约 8000 字分片，前缀只出现在第一片，后缀只出现在最后一片。

### 5.7 审核（`review`）

| 配置项 | 类型 / 默认值 | 效果 |
|---|---|---|
| `review.interactive` | `card` \| `text` / 自动 | `card`：审核 bot 发审核卡片（需要审核应用，见第 2 节第 4 步）；`text`：草稿发到你的自聊，用文本命令处理。不填时，配置了审核应用就用 `card`，否则用 `text`。显式设为 `card` 却没有配置审核应用时，gateway 拒绝启动本平台。 |
| `review.chat` | chat_id / `""` | 仅 `text` 模式：草稿发到哪个会话。空表示你和自己的会话（第一次发草稿时自动识别）。 |
| `review.ttl` | 时长 / `24h` | 草稿超过这个时间未处理就自动作废（卡片标为已过期）。 |

### 5.8 群上下文（`context`）

开启后，群里有人 @你 时，会把这个群最近的消息（以及该话题里的消息）一并交给 agent，帮助它理解前文。私聊不受影响。

| 配置项 | 类型 / 默认值 | 效果 |
|---|---|---|
| `context.enabled` | bool / `false` | 是否开启群上下文。默认关闭。 |
| `context.scope` | `recent` \| `thread` / `thread` | `recent`：只带群里最近的消息；`thread`：如果 @ 发生在话题里，再额外带上该话题的消息。 |
| `context.limit` | int / `20`（1–50） | 最多带多少条群消息。 |
| `context.hours` | 小时数 / `24` | 只带最近多少小时内的群消息。 |
| `context.thread_limit` | int / `20`（0–50） | 最多带多少条话题消息，`0` 表示不带。 |
| `context.msg_max_chars` | int / `1000` | 每条消息最多截取多少字，`0` 表示不截断。 |
| `context.when` | `new_session` \| `always` / `new_session` | `new_session`：只在会话第一条消息时带上（之后会话本身已有记忆，节省 token）；`always`：每次 @ 都带上最新上下文。 |

上下文拼在消息前面，agent 看到的格式如下：

```
<group_messages>
[11-20 14:19] Alice: 周五能发版吗？
[11-20 14:21] [bot] CI: 构建成功
</group_messages>

<thread_messages>
[11-20 14:25] Bob: 话题里的补充
</thread_messages>

[New message]
（触发这次回复的那条消息）
```

上下文是以你的身份读取的，只要是你所在的群都能读，不需要 bot 在群里。每次读取要额外调用 1–2 次 lark-cli（约 4 秒）。

### 5.9 按会话覆盖（`chats`）

`chats.<chat_id>` 下可以覆盖以下配置，没写的项沿用全局配置：

| 可覆盖项 | 说明 |
|---|---|
| `enabled` | 设为 `false` 时完全忽略这个会话（相当于黑名单） |
| `dm_scope` / `group_scope`（或 `session.*`） | 这个会话的隔离策略 |
| `reply.*` | 这个会话的回复方式（`delivery`、`format`、`target`、`prefix`……） |
| `context.*` | 这个群的上下文开关和参数 |

```yaml
chats:
  oc_team:                     # 团队群：按话题隔离，直接发送，带话题上下文
    group_scope: per_thread
    reply: { delivery: auto, format: card }
    context: { enabled: true, scope: thread }
  oc_noisy: { enabled: false } # 这个群一律不理
  oc_boss_dm:                  # 和某人的私聊：一定要审核
    reply: { delivery: review }
```

### 5.10 工具集

本平台的默认工具集是 `hermes-lark_user`：在 gateway 默认工具集的基础上**去掉了 `terminal` 和进程管理**。原因是任何能私聊你的人都能接触到这个 agent，而 agent 运行在持有你 Lark 用户 token 的机器上。

如果确实需要，可以在 `config.yaml` 中显式改回：

```yaml
platform_toolsets:
  lark_user: [hermes-lark_user, terminal]
```

### 5.11 环境变量

非敏感配置都放在 `config.yaml`，`~/.hermes/.env` 里只放以下几项：

| 变量 | 效果 |
|---|---|
| `LARK_USER_INTERACTION_APP_ID` | 审核应用的 App ID。配置后默认启用审核卡片。 |
| `LARK_USER_INTERACTION_APP_SECRET` | 审核应用的 App Secret。 |
| `LARK_USER_ALLOW_ALL_USERS=true` | 允许 `dm_policy` / `group_policy` 设为 `open`。不设置时，配置为 `open` 会导致 gateway 拒绝启动。 |

---

## 6. 常用配置示例

### 6.1 稳妥起步：只给几个同事做审核代答

```yaml
platforms:
  lark_user:
    enabled: true
    allow_from: [ou_alice, ou_bob]
    group_policy: disabled
```

### 6.2 下班时间自动回复所有私聊

```yaml
platforms:
  lark_user:
    enabled: true
    dm_policy: open                    # .env 需要 LARK_USER_ALLOW_ALL_USERS=true
    group_policy: disabled
    poll:
      active_hours: "19:00-09:00"
    reply:
      delivery: auto
      format: text
      prefix: "[🤖 {user_name} 的助理] "
      suffix: "\n（{user_name} 目前不在线，上班后会再确认）"
```

### 6.3 团队群里 @我 时带上下文、按话题回复

```yaml
platforms:
  lark_user:
    enabled: true
    dm_policy: disabled
    group_allow_from: [oc_team]
    session:
      group_scope: per_thread
    context:
      enabled: true
      scope: thread
      limit: 30
      hours: 12
    reply:
      delivery: review
      target: thread
```

### 6.4 一问一答、不保留记忆

```yaml
platforms:
  lark_user:
    enabled: true
    allow_from: [ou_alice]
    session: { dm_scope: per_message, group_scope: per_message }
```

---

## 7. 安全设计

| 风险 | 处理 |
|---|---|
| 助理回复自己，形成死循环 | 你本人发出的消息永远不会触发处理；以前缀开头的消息也会被忽略。 |
| 陌生人触发代答 | 默认白名单为空；`open` 必须配合 `LARK_USER_ALLOW_ALL_USERS` 才能启动；未授权的消息在入口就丢弃，也不会发配对码。 |
| 对方通过消息控制 gateway | 对方发的 `/new`、`/model`、`/stop` 等只当普通文本处理。 |
| agent 被诱导 @所有人 | agent 回复中的 `<at …>` 标签都会被破坏掉，只有 `at_sender` 加的那个 @ 是有效的。 |
| 群消息伪造上下文 | 上下文中每条消息都被压成一行，`<`、`>` 被替换，无法闭合或伪造上下文块。 |
| 执行过程泄露 | 只发最终答案，工具调用、中间说明、错误详情都不会发给对方。 |
| 陌生人诱导执行命令 | 默认工具集没有 terminal。 |
| 冒充本人 | 前缀不能为空，每条回复都表明是 AI 代答；`platform_hint` 也要求 agent 不替你做承诺、不泄露隐私。 |
| 消息体泄露 | 回复正文通过 stdin 传给 lark-cli，不出现在进程参数里；INFO 级别日志不记录消息正文。 |
| 伪造审核点击 | 只有审核 bot 与你私聊里、对应草稿自己那张卡片上的点击才生效；已处理的草稿不会被再次执行；审核应用的凭证只进子进程环境变量，不写入 lark-cli 配置、不进命令行参数。 |
| 审核点击被别的连接抢走 | 审核应用和 `feishu` 平台或 lark-cli 登录用的是同一个应用时，拒绝启动卡片审核；检测到该应用还有其他长连接时，在日志里告警。 |

---

## 8. 排错

| 现象 | 排查 |
|---|---|
| `hermes gateway status` 显示 `auth_required` | 用户 token 过期或未登录，执行 `HERMES_HOME=~/.hermes lark-cli auth login` 后重启 gateway。只执行 `lark-cli auth login` 无效：它登录的是 `local` 工作区。 |
| 终端里 `lark-cli whoami` 和 Hermes 里看到的应用 / 用户不一样 | 正常现象：Hermes 里有 `HERMES_HOME`，lark-cli 使用 `hermes` 工作区；终端里没有，使用 `local` 工作区。用 `HERMES_HOME=~/.hermes lark-cli whoami --as user` 查看插件实际使用的身份，或看 gateway 日志中的 `lark-cli workspace=…` 那一行。 |
| 白名单里填了 open_id 却不触发 | 那个 open_id 可能是在 `local` 工作区（另一个应用）下查到的；在 `hermes` 工作区下重新查。 |
| 显示 `config_invalid` | 配置有误，例如 `reply.prefix` 为空、枚举值拼错；具体原因见 gateway 日志。 |
| 显示 `cli_missing` | 找不到 lark-cli，检查 `PATH` 或配置 `cli_bin`。 |
| 启动报 "open policy without allow-all opt-in" | 设置了 `open` 策略但没有设置 `LARK_USER_ALLOW_ALL_USERS=true`。 |
| 有人发消息但没有回复 | 打开 DEBUG 日志后，每条被跳过的消息都会记录原因：`self` / `bot` / `not_mentioned` / `at_all` / `dm_policy` / `group_policy` / `external` / `stale` / `echo` / `chat_disabled` / `rate_limited`。 |
| 回复慢 | 正常延迟是 15–30 秒（索引延迟 + 轮询周期）；群上下文还会再加约 4 秒。 |
| 显示 `review_app_error` | 审核应用凭证错误或机器人未发布 / 不可用；或者没有拿到你的 union_id。检查第 2 节第 4 步。 |
| 收到审核卡片，点击后没有反应 | 检查审核应用是否已在“回调配置”中选择长连接并添加 `card.action.trigger`；gateway 日志中会出现 `review card listener` 相关报错；若日志提示 “already has N other event connection(s)”，说明还有别的进程连着这个应用。 |
| 点“修订”后一直没有新卡片 | agent 改写失败（例如模型调用出错）时不会产生新卡片，原草稿已作废、对方不会收到回复。查看 gateway 日志；对方再发消息时会重新起草。 |
| 想重新开始（忽略积压消息） | 停止 gateway，删除 `<HERMES_HOME>/lark_user/<open_id>.json`，再启动。注意：待审核草稿会一并丢失。 |

---

## 9. 已知限制

- 消息靠轮询获取，不是实时推送。
- 只读取消息的文本；图片、文件不会下载给 agent。
- 不展示执行过程，只在一轮处理结束后发出最终答案。不要为本平台打开 `display.platforms.lark_user.streaming`：流式输出时最终答案会被当作中间消息丢掉。
- 用户身份无法编辑已发出的文本消息，所以回复发出后无法修改，只能在审核阶段修订（卡片的“修订”或文本命令 `/revise`、`/edit`）。
- 审核卡片的回调监听每 90 分钟换新 token 重连一次，重连的几秒内点击可能失败，稍后再点即可。
