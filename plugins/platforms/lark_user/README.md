# lark_user：用你本人的 Lark 账号自动回复

`lark_user` 是 Hermes Gateway 的一个平台插件。它通过本机的 `lark-cli`，以**你本人的用户身份**定期读取两类消息：

- 别人发给你的**私聊**；
- 群里 **@你** 的消息。

每个会话会启动一个 Hermes Session，agent 写好答案后，以**你本人的名义**回复。为表明这是 AI 代答，每条回复都带固定的前缀。默认先把草稿发到你和自己的会话里，你确认后才真正发出（审核模式）。

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
         草稿发到你的自聊，等你 /send                   直接以你的名义回复对方
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
2. 以你本人身份登录：

   ```bash
   lark-cli auth login
   lark-cli auth status      # identities.user.available 应为 true
   ```

3. 用户 token 需要以下 scope：

   | scope | 用途 |
   |---|---|
   | `search:message` | 搜索私聊和 @我 的消息 |
   | `im:message.p2p_msg:get_as_user` | 读取私聊 |
   | `im:message.group_msg:get_as_user` | 读取群消息（包括群上下文） |
   | `im:message.send_as_user`、`im:message` | 以你的身份发送和回复 |

   可以用 `lark-cli auth check --scope "search:message im:message.send_as_user"` 检查。

4. 用户 token 约一周内不刷新就会过期。过期后插件会停止轮询，在 `hermes gateway status` 中显示 `auth_required`。此时 token 失效，也没法给你发提醒，需要你重新执行 `lark-cli auth login`，然后重启 gateway。

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

> **怎么找 open_id / chat_id**
> - 某人的 open_id：`lark-cli contact +search-user --query "姓名" --as user`；也可以在日志里找（DEBUG 级别）。
> - 群的 chat_id：`lark-cli im +chat-search --query "群名" --as user`。

---

## 4. 审核模式怎么用

`reply.delivery: review`（默认）时，每个待发出的回复都会以下面的形式出现在**你和自己的会话**中：

```
📝 待审核回复 #3
来自：Alice（群：项目组）
原消息：周五能发版吗？
---
（agent 起草的回复）
---
/send 3 发送 · /drop 3 丢弃 · /edit 3 新的回复内容 修改后发送
```

在**同一个会话**里发送以下命令：

| 命令 | 效果 |
|---|---|
| `/send [n]` | 发出草稿 n（不写 n 时发最新一条） |
| `/drop [n]` | 丢弃草稿 n（不写 n 时丢最新一条） |
| `/edit n 新内容` | 用"新内容"代替草稿 n 发出（同样会自动加前缀和后缀） |
| `/list` | 列出所有待审核草稿 |

规则：
- 命令**只认你本人**在**你和自己的会话**里发的。你在别的会话里输入 `/send`，或别人发 `/send`，都不会触发。
- 每条命令只执行一次。发送失败时草稿会保留，并提示失败原因，你可以再试。
- 超过 `review.ttl`（默认 24 小时）没处理的草稿会自动丢弃，并通知你。
- 设为 `reply.delivery: auto` 后直接发送，不经审核。可以全局设置，也可以只对某个会话设置。

---

## 5. 配置项详解

所有配置都在 `config.yaml` 的 `platforms.lark_user` 下。时长可以写成数字（秒）或带单位的字符串：`15`、`"15s"`、`"10m"`、`"24h"`、`"1d"`。

### 5.1 基础

| 配置项 | 类型 / 默认值 | 效果 |
|---|---|---|
| `enabled` | bool / `false` | 是否启用本平台。 |
| `cli_bin` | str / `lark-cli` | lark-cli 可执行文件，可以写 PATH 中的名字或绝对路径。 |
| `lark_cli_profile` | str / `""` | 传给 `lark-cli --profile`。不同 Hermes profile 可以绑定不同的 Lark 账号；同一个 Lark 账号同一时间只允许一个 Hermes profile 轮询，重复启动会被拒绝。 |
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
| `review.chat` | chat_id / `""` | 草稿发到哪个会话。空表示你和自己的会话（第一次发草稿时自动识别）。 |
| `review.ttl` | 时长 / `24h` | 草稿超过这个时间未处理就自动丢弃。 |

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

非敏感配置都放在 `config.yaml`。环境变量只有一个：

| 变量 | 效果 |
|---|---|
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

---

## 8. 排错

| 现象 | 排查 |
|---|---|
| `hermes gateway status` 显示 `auth_required` | 用户 token 过期，执行 `lark-cli auth login` 后重启 gateway。 |
| 显示 `config_invalid` | 配置有误，例如 `reply.prefix` 为空、枚举值拼错；具体原因见 gateway 日志。 |
| 显示 `cli_missing` | 找不到 lark-cli，检查 `PATH` 或配置 `cli_bin`。 |
| 启动报 "open policy without allow-all opt-in" | 设置了 `open` 策略但没有设置 `LARK_USER_ALLOW_ALL_USERS=true`。 |
| 有人发消息但没有回复 | 打开 DEBUG 日志后，每条被跳过的消息都会记录原因：`self` / `bot` / `not_mentioned` / `at_all` / `dm_policy` / `group_policy` / `external` / `stale` / `echo` / `chat_disabled` / `rate_limited`。 |
| 回复慢 | 正常延迟是 15–30 秒（索引延迟 + 轮询周期）；群上下文还会再加约 4 秒。 |
| 想重新开始（忽略积压消息） | 停止 gateway，删除 `<HERMES_HOME>/lark_user/<open_id>.json`，再启动。注意：待审核草稿会一并丢失。 |

---

## 9. 已知限制

- 消息靠轮询获取，不是实时推送。
- 只读取消息的文本；图片、文件不会下载给 agent。
- 不展示执行过程，只在一轮处理结束后发出最终答案。不要为本平台打开 `display.platforms.lark_user.streaming`：流式输出时最终答案会被当作中间消息丢掉。
- 用户身份无法编辑已发出的文本消息，所以回复发出后无法修改，只能在审核阶段用 `/edit` 改。
