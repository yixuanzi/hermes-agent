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
3. [快速开始](#3-快速开始)（含 [开启 / 关闭自动回复](#31-开启--关闭自动回复lark-user)）
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

- **默认关闭，手动开启**：gateway 每次启动后自动回复都处于**关闭**状态：不拉取消息、不执行任务、不产生任何回复。需要你发送 `/lark-user start` 才开始工作，`/lark-user stop` 随时关闭（见 3.1）。开关只保存在内存里，gateway 重启后又回到关闭。
- **只发最终答案**：agent 执行过程中的工具进度、中间说明、心跳、出错提示都不会发给对方，只有这一轮的最终答案会发出（审核模式下也只生成一条草稿）。
- **消息来源**：
  - 私聊：`lark-cli im +messages-search --chat-type p2p`
  - @我：`lark-cli im +messages-search --is-at-me --chat-type group`，再在本地按消息的 @ 列表确认确实 @了你本人，因为这个接口会把 @所有人 的消息也一起返回。
- **每次开启**：开启那一刻已有的消息（包括关闭期间收到的）一律视为历史，只回复之后新来的消息。
- **状态数据库**：`<HERMES_HOME>/plugin-data/lark_user/<你的open_id>.db`（SQLite），保存已处理的消息、轮询进度和待审核草稿。重启后不会重复回复，也不会丢失草稿。每次变化只写入改动的那一行，不会每轮轮询都重写整份数据。旧版本的 `<HERMES_HOME>/lark_user/<open_id>.json` 会在第一次启动时自动导入，导入后改名为 `.json.migrated` 保留，确认无误后可以删除。
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
   > lark-cli 会识别自己是否运行在 AI agent 里：环境变量中只要有 Hermes 的变量（实测 `HERMES_HOME`、`HERMES_QUIET`、`HERMES_SESSION_KEY`、`HERMES_EXEC_ASK` 任一非空即可），它就使用一套独立的 **`hermes` 工作区**（`~/.lark-cli/hermes/config.json`），否则使用默认的 **`local` 工作区**（`~/.lark-cli/config.json`）。两个工作区各自有各自的应用、profile 和登录状态；变量的值是什么无所谓，有就切换。
   >
   > Hermes gateway 进程里一定有 `HERMES_HOME`，所以插件调用的 lark-cli **永远使用 `hermes` 工作区**；而你在普通终端里直接运行 `lark-cli`，用的是 `local` 工作区。后果是：
   > - 在普通终端执行 `lark-cli auth login`，登录的是 `local` 工作区，**插件用不上**；
   > - 两个工作区可能绑定不同的应用，你会在终端和 Hermes 里看到不同的 appId 和 open_id（同一个人在不同应用下的 open_id 不同）。
   > - 插件读到的每条消息里，发送者的 open_id（`sender.id`）属于 `hermes` 工作区登录的那个应用。`allow_from` 必须填这个应用下的 open_id。
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

**最后，开启自动回复**：在任意一个连到这个 gateway 的会话里，以你本人身份发送 `/lark-user start`（见 3.1）。不执行这一步，插件虽然已连接，但不会拉取消息，也不会回复。

默认行为如下：
- 自动回复默认**关闭**，每次 gateway 启动后都需要 `/lark-user start`。
- 审核模式：草稿发到你和自己的会话。
- 回复格式为 Markdown 富文本（post），引用原消息回复。
- 回复前缀为 `[🤖 AI 代答] `。
- 不响应 @所有人。
- agent 不能使用 terminal。

> **怎么找 open_id / chat_id**（open_id 按应用区分，必须在 `hermes` 工作区下查，否则白名单对不上）
> - 某人的 open_id：`HERMES_HOME=~/.hermes lark-cli contact +search-user --query "姓名" --as user`；也可以在日志里找（DEBUG 级别）。
> - 群的 chat_id：`HERMES_HOME=~/.hermes lark-cli im +chat-search --query "群名" --as user`。

### 3.1 开启 / 关闭自动回复（`/lark-user`）

| 命令 | 效果 |
|---|---|
| `/lark-user start` | **开启**：开始轮询私聊和 @我。开启那一刻已有的消息（包括关闭期间收到的）都视为历史，不会补回复，只处理之后新来的消息。 |
| `/lark-user stop` | **关闭**：停止轮询；中断所有正在执行的任务；丢弃排队中的消息。关闭期间不执行任何任务，不产生任何结果：不回复，也不生成审核草稿。 |
| `/lark-user status`（或只发 `/lark-user`） | 查看当前状态：开 / 关、开启时间、轮询的来源、回复方式、待审核草稿数、运行时修改的回复设置数。 |
| `/lark-user reset` | 列出有进行中会话的私聊和群：名称、会话 ID（`oc_…`）、会话数、最近活动时间。 |
| `/lark-user reset <会话 ID>` | **重置**这个私聊或群的全部进行中会话，见下方说明。 |
| `/lark-user reply` | 列出当前生效的回复设置，标出运行时改过的项和配置文件里的值。 |
| `/lark-user reply.<项> <值>` | **仅在内存中**修改一项回复设置（5.6 节的 `delivery`、`format`、`target`、`at_sender`、`prefix`、`suffix`、`card.title`），见下方说明。不带值时只查看。 |
| `/lark-user chats.<会话 ID>.reply.<项> <值>` | 只改某个私聊或群的这一项。 |
| `/lark-user reply.<项> --reset` | 这一项恢复为配置文件里的值；`/lark-user reply --reset` 全部恢复。 |

- **状态只在内存里**：gateway 每次启动（包括重启、平台重连）后都是**关闭**的，需要重新 `/lark-user start`。
- **谁能执行**：只有插件所代表的**你本人**。具体是以下三种之一：本机的本地会话（CLI、TUI、桌面端）；Feishu/Lark 上 union_id 与 lark-cli 登录用户相同的发送者（跨应用识别，比如你在 Aegis 机器人私聊里发命令）；open_id 相同的发送者。其他人（例如能和同一个机器人对话的同事）执行会被拒绝。
- **在哪里执行**：命令在 gateway 进程里执行，要在连到这个 gateway 的会话里发，例如 Hermes 的飞书机器人私聊、TUI 或桌面端。在独立运行的 `hermes` CLI 进程里执行，会提示 lark_user 不在本进程运行。
- **多 profile**：命令只作用于发命令的会话所属 profile 的 lark_user。
- **重置（`reset`）**：
  - 只需要给私聊或群的会话 ID，这个私聊或群里所有还没结束的会话都会一起重置，不管它们是按人（`per_chat_user`）、按话题（`per_thread`）还是整群共享（`per_chat`）分出来的。改过隔离方式后留下的旧会话也包括在内。
  - 重置方式和闲置重置一样（见 5.5 节）：旧会话完整结束，旧对话保留，这个私聊或群的下一条消息从新会话开始。
  - 某个会话有任务正在执行时，会先中断它，中断产生的回执不会发到飞书。
  - 会话 ID 区分大小写，可以先发 `/lark-user reset` 复制列表里的 ID。
  - 只影响 lark_user 自己的会话，不影响 Hermes 飞书机器人等其他平台上的会话；不改变自动回复开关；开关关闭时也能用。
  - 待审核的草稿不受影响。之后对这些草稿点“修订”，会在新会话里执行：修订指令里带着上一版草稿和你的意见，但 agent 看不到重置前的对话。
- **修改回复设置（`reply.<项>`）**：
  - 只改内存，**不写入 config.yaml**，只对当前 gateway 进程有效；平台重连后仍然有效，gateway 重启后按配置文件。
  - 修改后的下一条回复就按新设置发送；之后才通过的待审核草稿也按新设置发送。
  - 值的校验和配置文件完全一样：`prefix` 为空、`format` 写错等会被拒绝，设置保持不变。
  - 要保留首尾空格或写转义时加引号：`"…"` 按 JSON 字符串解析（如 `/lark-user reply.suffix "\n—— 如需本人回复请留言「转人工」"`），`'…'` 原样保留。
  - 配置文件里 `chats.<chat_id>.reply` 单独设置过的项，不受全局修改影响，回复里会列出这些会话。
  - 把 `delivery` 从 `auto` 改为 `review` 时，如果之前没有任何会话需要审核，会补上审核所需的东西：配置了审核应用就启用审核卡片，否则启用承载文本审核命令的私聊轮询。
- **关闭时已有的审核草稿**：仍然保留。你可以在关闭状态下对它们点“通过”或“拒绝”，这是你对已有结果的明确决定，不会触发 agent。但“修订”需要 agent 重新执行，关闭期间会被拒绝，卡片提示先开启。

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
- 自动回复关闭时（`/lark-user stop`）不能修订，卡片会提示先开启；“通过”和“拒绝”仍然可用。
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

所有配置都在 `config.yaml` 的 `platforms.lark_user` 下，下面表格里的配置项路径（如 `poll.interval`、`reply.prefix`、`chats.<chat_id>`）都是**相对于 `platforms.lark_user`** 的，例如 `poll.interval` 的完整路径是 `platforms.lark_user.poll.interval`。只有两处例外：工具集 `platform_toolsets`（5.10 节）写在 `config.yaml` 根节点；环境变量（5.11 节）写在 `~/.hermes/.env`。

> 写错层级不会报错，只会被**静默忽略**。例如把 `chats:` 写在 `config.yaml` 根节点，按会话的覆盖全部不会生效。

时长可以写成数字（秒）或带单位的字符串：`15`、`"15s"`、`"10m"`、`"24h"`、`"1d"`。

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
| `text_batch_delay_seconds` | 秒 / `2`（上限 2） | 同一会话短时间内连发的多条消息合并成一轮处理，避免对方连发三句、你回三次：每来一条就重新计时，这个会话静默这么久后，攒下的消息（文字和附件）作为一轮交给 agent。同一次轮询搜到的消息先全部准备好（下载附件、查话题、取群上下文）再一起入队，所以它们总能合并，不受准备耗时影响；分在两次轮询里的消息是两轮。不同会话各自合并、互不影响。设为 `0` 关闭合并（不建议：同一会话的后几条会打断正在运行的那一轮，见 gateway 的 `display.busy_input_mode`）。 |
| `rate_limit.per_chat` | int / `5` | 每个会话每分钟最多处理几条消息，超出的静默跳过；`0` 表示不限制。 |
| `instructions` | 多行文本 / `""` | 你写给 agent 的额外说明，见下方“额外指令”。可以在 `chats.<chat_id>` 里按会话覆盖。 |

**图片和文件**：消息里的图片（单独发的图片 `[Image: img_…]`、富文本里的图片 `![Image](img_…)`）和文件（`<file key="file_…" name="…"/>`）会下载到本机缓存，作为这条消息的附件交给 gateway。消息正文保持原样，不做替换；文件路径只出现在 gateway 加的附件说明里，每个资源只出现一次：

| 资源 | agent 看到的 |
|---|---|
| 图片（模型支持看图） | 图片本身，加一行 `[Image attached at: <路径>]` |
| 图片（模型不支持看图） | 先做图片识别，在消息前面加图片描述和路径 |
| 文件 | 消息前面加一段说明：文件名、路径，并提示 agent 自己去读 |

- 图片存进 `cache/images/`，文件存进 `cache/documents/`（文件名是图片格式的文件也放进 `cache/images/`），文件名是 `lark_<资源ID>.…`。这两个目录也是 gateway 其他平台用的媒体缓存，按 gateway 的规则定期清理旧文件。
- 下载前先按资源 ID（`img_…` / `file_…`）查缓存：已经有这个 ID 的文件就直接引用，不再下载。同一张图被转发、同一个文件被多次提到时只下载一次。
- 附件说明里的路径由 gateway 生成：终端在 Docker 里运行时是容器内的路径，多 profile 时文件会移到对应 profile 的缓存。
- 只处理图片和文件；语音、视频、表情包不下载。
- 下载失败（消息已撤回、没有权限、超过 `gateway.max_inbound_media_bytes`）时这个资源没有附件，正文里仍是原来的标识，这条消息照常处理。
- 每个资源下载约 2 秒，最多同时下载 3 个。只下载会被处理的消息里的资源，被过滤掉的消息不会触发下载。
- 开了群上下文（`context`，见 5.8）时，带上的群消息里**这次 @你 的人自己发的**图片和文件也会作为这一轮的附件（排在这条消息自己的附件前面），所以“先发文件、再 @你 让你看”能拿到文件。别人发的不下载，在上下文里只是标识。没带上下文的轮次（如 `when: new_session` 下会话中途的 @）不会补带。

**额外指令（`instructions`）**：你给 agent 的长期说明，比如身份介绍、回复语气、哪些事不能答应、遇到什么情况该怎么处理。它在每一轮都加进这个会话的系统提示词，不写进对话记录：

```yaml
platforms:
  lark_user:
    instructions: |
      你在替我（安全团队负责人）回复同事。
      - 只确认收到和说明情况，不替我答应任何排期或审批。
      - 涉及线上事故的，回复“已转给值班同学”，并提醒对方在 #oncall 群里同步。
    chats:
      oc_boss_dm:
        instructions: 这是我的上级，语气正式，所有问题都说我会尽快亲自回复。
      oc_team:
        instructions: ""          # 这个群不加额外指令
```

- `chats.<chat_id>.instructions` 整个**替换**全局的 `instructions`，不是追加；设为 `""` 表示这个会话不加。
- 同一个会话的每一轮（包括审核时的修订）用的都是同一份指令，不影响模型的提示词缓存。修改配置后重启 gateway 生效。
- 指令只用来引导 agent，不能替代访问控制：谁能触发回复仍由白名单决定，回复也仍然带固定前缀。

### 5.5 会话隔离（Session Key）

| 配置项 | 可选值 / 默认值 | 效果 |
|---|---|---|
| `session.dm_scope`（也可以直接写 `dm_scope`） | `per_chat` \| `per_message` / `per_chat` | `per_chat`：和每个人的私聊各一个会话，有上下文记忆；`per_message`：每条消息一个独立会话，没有记忆。 |
| `session.group_scope`（也可以直接写 `group_scope`） | `per_chat` \| `per_chat_user` \| `per_thread` \| `per_message` / `per_chat_user` | 见下表。 |
| `session.idle_reset` | 时长（`30m`、`4h`、`1d`）或 `0` / `4h` | 一个会话超过这么久没有新消息，下一条消息就开新会话；`0` 表示从不按闲置重置。最小 `1m`。 |
| `session.daily_reset_at` | `"HH:MM"`（本机时间）/ 不设置 | 每天这个时间之后的第一条消息开新会话，比如 `"04:00"`；不设置就不按天重置。 |

| `group_scope` | 一个会话对应 | 适合 |
|---|---|---|
| `per_chat` | 整个群（所有 @你 的人共享上下文） | 小团队群，大家围绕同一件事讨论 |
| `per_chat_user` | 群里的每个人 | 默认值，不同人的问题互不干扰（依赖 gateway 的 `group_sessions_per_user`，默认开启） |
| `per_thread` | 每条“根消息”及其下的话题（见下方说明） | 话题群；习惯开话题讨论的一般群 |
| `per_message` | 每次 @ | 一问一答、不需要记忆 |

**`per_thread` 的具体规则**：会话以“根消息”为准，根消息就是话题所挂的那条消息。

| 场景 | 归入的会话 |
|---|---|
| 在群里直接发消息 @你（不在话题里） | 以这条消息为根的会话 |
| 之后有人在这条消息下开话题，继续在话题里 @你 | **同一个会话**，上下文延续 |
| 同一个话题里不同的人 @你 | 同一个会话（话题内默认不按人拆分，由 gateway 的 `thread_sessions_per_user` 控制） |
| 另一条根消息或另一个话题里 @你 | 各自独立的会话 |
| 一般群里的“引用回复”（不开话题） | 当作新的根消息，各自独立（lark-cli 的搜索结果不带“回复的是哪条消息”） |

话题里的 @ 第一次出现时，插件会查一次这个话题的根消息（约 2 秒），之后缓存，同一话题不再重复查询。查询失败时退回按话题 ID 分会话：只是失去和根消息会话的衔接，不影响回复。

会话 key 统一由 gateway 生成，因此多 profile 的命名空间照常生效。

**会话什么时候结束（`idle_reset` / `daily_reset_at`）**：gateway 的会话只会在收到 `/new` 或 `/reset` 时结束，可是给你发消息的人不能控制 gateway，他们发 `/new` 只会被当成普通文字。不设结束条件的话，和每个人的私聊、`per_chat_user` 和 `per_chat` 的群会话都会一直累加下去：上下文越来越长，每次回复的成本越来越高，旧话题也会一直影响新的回答。所以插件默认在会话**闲置 4 小时**后结束它：

- 判断依据是这个会话最后一次活动的时间，由 gateway 记录，gateway 重启后依然有效。
- 判断发生在下一条消息进来时：插件把旧会话标记为结束，gateway 处理这条消息时就会开一个全新的会话。旧会话的结束流程（会话钩子、记忆写入）和 `/new` 一样完整，旧对话也照常保存，agent 之后仍能用 `session_search` 查到。
- gateway 在新会话开始时会发一条“会话已重置”提示，插件不会把它发到飞书，对方看不到。
- 正在执行任务的会话不会被结束。
- `per_message` 每条消息本来就是新会话；`per_thread` 一个话题一个会话，本身有边界，但话题闲置超过 `idle_reset` 后同样会开新会话。
- 开了群上下文（`context.when: new_session`）时，每个新会话的第一条消息都会重新带上群里最近的消息，所以会话短也能接上前因后果。

每个会话可以在 `chats.<chat_id>.session` 里单独设置，比如某个长期项目群不想重置，就设 `idle_reset: 0`。需要的时候也可以用 `/lark-user reset <会话 ID>` 手动重置（见 3.1 节）。

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

这些设置都可以用 `/lark-user reply.<项> <值>` 在运行时临时修改（只在内存里，重启后恢复，见 3.1 节）。

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
| `context.when` | `new_session` \| `always` / `new_session` | `new_session`：只在会话第一条消息时带上（之后会话本身已有记忆，节省 token；会话因 `idle_reset` / `daily_reset_at` 重新开始时也会带上）；`always`：每次 @ 都带上最新上下文。 |

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

上下文里 @你 的那个人自己发过的图片和文件会下载（已在缓存里的直接引用），作为这一轮的附件交给 agent，和消息本身的附件一样处理（见 5.4）；其他人发的只保留标识。

上下文是以你的身份读取的，只要是你所在的群都能读，不需要 bot 在群里。每次读取要额外调用 1–2 次 lark-cli（约 4 秒）。

### 5.9 按会话覆盖（`chats`）

`platforms.lark_user.chats.<chat_id>` 下可以覆盖以下配置，没写的项沿用全局配置。**`chats` 必须写在 `platforms.lark_user` 下面**，写在 `config.yaml` 根节点会被忽略。

| 可覆盖项 | 说明 |
|---|---|
| `enabled` | 设为 `false` 时完全忽略这个会话（相当于黑名单） |
| `dm_scope` / `group_scope`（或 `session.*`） | 这个会话的隔离策略 |
| `session.idle_reset` / `session.daily_reset_at` | 这个会话什么时候结束、开新会话 |
| `instructions` | 这个会话的额外指令（替换全局的，`""` 表示不加） |
| `reply.*` | 这个会话的回复方式（`delivery`、`format`、`target`、`prefix`……） |
| `context.*` | 这个群的上下文开关和参数 |

```yaml
platforms:
  lark_user:
    enabled: true
    # ……全局配置……
    reply: { delivery: review }  # 全局默认：审核
    chats:                       # 与 reply、session 等同级，都在 platforms.lark_user 下
      oc_team:                     # 团队群：按话题隔离，直接发送，带话题上下文
        group_scope: per_thread
        reply: { delivery: auto, format: card }
        context: { enabled: true, scope: thread }
      oc_noisy: { enabled: false } # 这个群一律不理
      oc_boss_dm:                  # 和某人的私聊：一定要审核
        reply: { delivery: review }
```

`<chat_id>` 是会话 ID（`oc_…`），群聊和私聊都有。查群的 chat_id 见第 3 节；私聊的 chat_id 可以在 DEBUG 日志里找到，或者用 `HERMES_HOME=~/.hermes lark-cli im +chat-messages-list --as user --user-id <对方 open_id> --page-size 1` 返回结果里的 `chat_id` 字段。

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
| 插件已连接，但完全不拉取、不回复 | 自动回复默认是关闭的。发送 `/lark-user status` 查看；gateway 重启后需要重新 `/lark-user start`。 |
| 执行 `/lark-user` 提示“只有……本人可以使用” | 发命令的人不是 lark-cli（`hermes` 工作区）登录的用户。用本人账号发送，或在本机 TUI / 桌面端发送。 |
| 执行 `/lark-user` 提示“没有在这个 gateway / profile 里运行” | 当前会话不在运行 lark_user 的 gateway 进程里（例如独立的 `hermes` CLI），或者会话属于另一个 profile，或者 `platforms.lark_user.enabled` 没开。 |
| 有人发消息但没有回复 | 打开 DEBUG 日志后，每条被跳过的消息都会记录原因：`self` / `bot` / `not_mentioned` / `at_all` / `dm_policy` / `group_policy` / `external` / `stale` / `echo` / `chat_disabled` / `rate_limited`。 |
| 回复慢 | 正常延迟是 15–30 秒（索引延迟 + 轮询周期）；群上下文还会再加约 4 秒。 |
| 显示 `review_app_error` | 审核应用凭证错误或机器人未发布 / 不可用；或者没有拿到你的 union_id。检查第 2 节第 4 步。 |
| 收到审核卡片，点击后没有反应 | 检查审核应用是否已在“回调配置”中选择长连接并添加 `card.action.trigger`；gateway 日志中会出现 `review card listener` 相关报错；若日志提示 “already has N other event connection(s)”，说明还有别的进程连着这个应用。 |
| 点“修订”后一直没有新卡片 | agent 改写失败（例如模型调用出错）时不会产生新卡片，原草稿已作废、对方不会收到回复。查看 gateway 日志；对方再发消息时会重新起草。 |
| 想重新开始（忽略积压消息） | 一般不需要：`/lark-user start` 本来就把开启前的消息当作历史。确实要清空时，停止 gateway，删除 `<HERMES_HOME>/plugin-data/lark_user/<open_id>.db`（以及同名的 `-wal`、`-shm` 文件），再启动。注意：待审核草稿会一并丢失。 |

---

## 9. 已知限制

- 消息靠轮询获取，不是实时推送。
- 图片和文件会下载给 agent（见 5.4），语音、视频、表情包不会；群上下文（`context`）里只下载 @你 的人自己发的图片和文件，其他人的不下载。
- 不展示执行过程，只在一轮处理结束后发出最终答案。不要为本平台打开 `display.platforms.lark_user.streaming`：流式输出时最终答案会被当作中间消息丢掉。
- 用户身份无法编辑已发出的文本消息，所以回复发出后无法修改，只能在审核阶段修订（卡片的“修订”或文本命令 `/revise`、`/edit`）。
- 审核卡片的回调监听每 90 分钟换新 token 重连一次，重连的几秒内点击可能失败，稍后再点即可。
