# Runtime 用户环境隔离

本文档描述当前 Hermes 主线中 Aegis 二次开发的 messaging 用户级 runtime env 隔离。它覆盖 live gateway turn、local terminal、`userenv` 工具、Slack 直入和 Aegis 远端 A2A 委托的来源身份前缀，以及带身份的 cron job（包括 `no_agent=True` 脚本模式）。目标是让同一 Hermes 进程服务多个用户、多个会话时，用户环境变量不会互相读取、覆盖或残留。

当前 A2A 交互服务端实现位于 `workagent/`（对应 AISOC 升级目录），本次更新不修改 `aisoc/` 下的 A2A executor/server。交互审批或澄清被响应后，会继续原有 Workagent agent turn，不会新建一份用户环境分区。

## 1. 设计目标与数据模型

需要同时满足：

1. 同一平台不同用户的环境变量持久化隔离。
2. 不同平台即使 user id 相同也隔离。
3. 同一用户跨 turn 可复用 local runtime，但用户改名不会产生新分区。
4. tool 执行、线程传播和 cron 恢复都读取正确身份。
5. local shell snapshot 不保存任何用户的持久 secret。

使用两种 key，职责不可互换：

| 名称 | 格式 | 用途 |
| --- | --- | --- |
| storage key | `platform.user_id` | `$HERMES_HOME/users.env.json` 的持久化分区。 |
| runtime scope key | `local::{platform}::{user_id}` | `TERMINAL_ENV=local` 的 local environment cache 分区。 |

`user_name` 不是分区键，而是作为保留字段 `CURRENT_USER_NAME` 存在每个用户 payload 中。

示例：

```json
{
  "slack.u123": {
    "CURRENT_USER_NAME": "alice",
    "API_TOKEN": "secret-value"
  },
  "feishu.u123": {
    "CURRENT_USER_NAME": "alice",
    "API_TOKEN": "another-value"
  }
}
```

主要实现文件：

- `tools/user_env_store.py`: JSON 存储、迁移和保留字段。
- `tools/user_env_runtime.py`: `UserEnvIdentity` 与 ContextVar binding。
- `tools/userenv_tool.py`: 当前用户的管理入口。
- `agent/tool_executor.py`、`agent/agent_runtime_helpers.py`: 所有工具执行路径的身份绑定。
- `tools/environments/base.py`、`tools/environments/local.py`、`tools/terminal_tool.py`: 注入、snapshot hygiene 和 local cache isolation。
- `gateway/run.py`: 为 Slack 直入 Agent 的入站 turn 组装 `<source>` 前缀。
- `tools/a2a_delegate_tool.py`: Aegis 父 Agent 委托远端 A2A Agent 时组装 `<source>` 前缀。
- `workagent/backend/a2a_service/executor.py`: 解析收到的 `<source>` 前缀，将身份绑定到 Workagent Agent runtime，并桥接 A2A 审批/澄清交互。
- `workagent/backend/a2a_service/interactions.py`、`workagent/backend/a2a_server.py`: 注册交互等待、校验响应并在同一 A2A task 上发布 resolved metadata。
- `cron/jobs.py`、`tools/cronjob_tools.py`、`cron/scheduler.py`: 延迟执行时的 cron 所属身份。

## 2. 存储与 `userenv`

### 2.1 `$HERMES_HOME/users.env.json`

`make_user_env_key(platform, user_id, user_name=None)` 只返回 `platform.user_id`。读取、写入和删除都必须通过 store helper，不要自行拼接历史 username key。

`load_user_env(platform, user_id, user_name)` 的行为：

1. 读取当前 key。
2. 缺失时查找旧格式 `platform.user_id.user_name` 前缀。
3. 恰好匹配一项时迁移到当前 key，并持久化 `CURRENT_USER_NAME`。
4. 匹配多项时记录告警且不猜测合并目标。
5. 返回值总会以当前名字刷新 `CURRENT_USER_NAME`。

`CURRENT_USER_NAME` 是系统管理字段：用户不能通过 delete 删除；它会被注入运行时，但不能作为普通 secret/变量向模型展示。

### 2.2 `userenv` 工具

工具名为 `userenv`，支持：

```text
userenv(action="list")
userenv(action="set", key="NAME", value="value")
userenv(action="delete", key="NAME")
```

它只通过当前 `UserEnvIdentity` 操作自己的 storage key。返回值隐藏变量值，也隐藏 `CURRENT_USER_NAME`，因此模型无法借由该工具列出另一位用户的数据或回显 secret。没有已绑定身份时，工具应返回明确错误，而不是退化为全局环境。

## 3. 运行时身份传播

`UserEnvIdentity` 包含：

- `platform`
- `user_id`
- `user_name`
- `user_key` / `storage_key`
- `runtime_scope_key`

身份保存在 ContextVar。标准路径是：

```text
gateway event / scheduled agent
  -> agent fields (platform, user_id, user_name)
  -> bind_current_user_env_identity_from_agent(...)
  -> tool execution
  -> get_current_user_env_values()
  -> terminal or subprocess env overlay
```

`agent/tool_executor.py` 和 `agent/agent_runtime_helpers.py` 都必须包裹身份 binding，覆盖顺序执行、并发/辅助执行以及 agent-owned tool 分发。`tools/thread_context.py` 的 ContextVar 线程传播自然携带当前 identity；任何新开工具线程也必须使用现有 context propagation helper，不能重新从 process-global `os.environ` 推断用户。

调用 `bind_current_user_env_identity_from_agent(agent)` 时优先读取 runtime-only 的 `agent._user_env_platform`，再读取 `agent.platform`。这是 cron 需要保留 scheduler 身份与原始 messaging platform 两种语义时的关键。

### 3.1 `<source>` 来源身份前缀：Slack 直入与 Aegis 远端委托

`<source>` 是 runtime identity 的补充载体，当前有两处**写入端**。两者都使用紧凑 JSON，并要求 envelope 位于最终消息的第一行，但触发时机和字段不同。

#### 场景 A：Slack 消息直接进入 Agent

`gateway/run.py` 为每个带 Slack `user_id` 的入站 Agent turn（DM、频道和 thread 都包括）在最终组装文本的首行添加：

```text
<source>{"platform":"slack","channel":"C02MVR0PADS","uid":"U02LQJ2S5HN","uname":"Guisheng(郭桂生)"}</source>

获取 TEST_KEY 内容
```

这里的 `channel` 来自 Slack channel 或 DM 会话 ID，因此 runtime 可同时获得用户身份和消息范围。reply context、附件说明、时间戳、channel context 与用户正文都必须置于该行之后。

```text
Slack adapter / gateway
  -> gateway/run.py writes first-line <source>
  -> direct Agent runtime
  -> userenv / terminal isolation and runtime RBAC decisions
```

#### 场景 B：Aegis 与远端 A2A Agent 通信

当 Aegis 父 Agent 调用 `aegis_a2a_delegate` 向远端 A2A Agent 发送首轮目标或前台 follow-up 时，`tools/a2a_delegate_tool.py::_decorate_a2a_user_message()` 会根据父 Agent 当前绑定的 runtime identity 重建前缀，并把它置于远端请求首行：

```text
<source>{"platform":"slack","uid":"U02LQJ2S5HN","uname":"Guisheng(郭桂生)"}</source>

<context>可选委托上下文</context>

请远程 Agent 完成此任务
```

该出站格式由 `_format_aegis_source_header()` 生成。它当前从父 Agent 读取 `platform`、`_user_id` 和 `_user_name`；**不会携带 `channel`**。远端 A2A Agent 可据此恢复调用方用户的 platform/user identity，进行用户环境隔离和 RBAC 判断；若远端授权还需要频道范围，必须通过经认证的 A2A 元数据或后续协议扩展传递，不能假定该字段存在。

```text
Aegis parent Agent
  -> a2a_delegate_tool writes first-line <source>
  -> remote A2A Agent
  -> remote runtime identity / RBAC decisions
```

#### 字段与信任边界

| 字段 | Slack 直入 | Aegis → 远端 A2A | RBAC / 隔离用途 |
| --- | --- | --- | --- |
| `platform` | 固定为 `slack`。 | 父 Agent 当前来源平台。 | 与 `uid` 共同构成身份命名空间。 |
| `channel` | Slack channel 或 DM 会话 ID。 | 当前不发送。 | Slack 直入时可用于频道范围判断。 |
| `uid` | Slack 稳定用户 ID。 | 父 Agent 的 `_user_id`。 | 用户隔离与 RBAC 的主身份键；不得用显示名替代。 |
| `uname` | Slack 显示名。 | 父 Agent 的 `_user_name`。 | 仅用于展示、审计上下文与 `CURRENT_USER_NAME`；不作为授权或存储分区键。 |

Aegis executor 接收 A2A 请求时只解析文本偏移 0 处、精确形如 `<source>{...}</source>` 的 envelope；成功后保留原始文本给模型，并将可用的 `platform`、`uid`、`uname` 绑定到 runtime identity 与 session context。其他 messaging adapter、CLI、cron 和普通 A2A client 当前都不会自行写入该前缀。

`<source>` 是**受信任上游附加的身份声明**，而不是独立的授权凭证。运行时 RBAC 可以使用已验证的 `platform + uid`（Slack 直入时可附加 `channel`）作判断，但不得仅因任意 A2A 请求正文含有该标签就授予权限。直接 A2A client 和远端服务仍必须依赖传输层认证、gateway/adapter 授权及服务端信任边界；消息正文中第二个或非首行的 `<source>` 一律只是非可信内容。

### 3.2 A2A transport identity 与 userenv 分区的分离

Workagent executor 使用 A2A `context_id` 作为稳定的 Hermes session key；`task_id` 只标识当前 task，不能用它替代跨轮 session。收到来源信封后，执行线程临时设置以下字段：

| 字段 | 作用 | 是否作为 userenv 分区键 |
| --- | --- | --- |
| `agent.platform = "{platform}_a2a"` | 标记当前 Agent 的 A2A 执行 surface、日志和运行时语义。 | 否 |
| `agent._user_env_platform = "{platform}"` | 保留原始 Slack/Feishu 等来源平台，供 userenv identity binding 优先读取。 | 是（与 user id 组合） |
| `agent._user_id` / `agent._user_name` | 恢复委托方用户身份。 | 只有 `_user_id` 参与 key；名字仅保留为 `CURRENT_USER_NAME` |

因此，来自 Slack 用户 `U123` 的远端 turn 即使执行 surface 是 `slack_a2a`，terminal/userenv 仍读取：

```text
storage key:       slack.U123
runtime scope key: local::slack::U123
agent platform:    slack_a2a       # 仅 A2A 执行标识
```

审批或澄清的交互过程也遵循同一分离：Workagent 在 `context_id` 对应的 agent 线程内注册并阻塞，用户点击后通过经认证的 A2A response endpoint 唤醒原 wait。它不会启动第二个 Agent turn，也不会因为 `platform` 的 `_a2a` 后缀生成 `slack_a2a.U123` 新分区。`_run_agent_conversation()` 在回合结束后恢复临时字段；terminal 在审批通过后的同一回合中仍通过 `UserEnvIdentity` 读取原始平台分区。

如果 A2A 请求没有 `<source>`，executor 仍使用 transport-only 的 `HERMES_SESSION_PLATFORM=a2a` 参与审批上下文判断，但不会凭空构造用户 userenv identity，也不会退化为读取全局环境。

## 4. Terminal 和 shell snapshot

### 4.1 直接 subprocess 注入

`tools/environments/local.py` 的 local 前台/后台 subprocess 创建会从 `get_current_user_env_values()` 读取 payload，并添加到子进程环境。它不修改 process-global `os.environ`。

不适合作 shell export 的 key 仍可进入普通 subprocess env；只有匹配 shell 变量名称规则的 key 才能写入 bash wrapper。

### 4.2 可复用 local shell

local backend 使用 spawn-per-call + shell snapshot，而不是为每个用户保持一个长期 shell。正确顺序为：

1. `LocalEnvironment.init_session()` 创建基础 login-shell snapshot，不能包含 user env。
2. 每次命令执行前，在命令 wrapper 中 overlay 当前 identity 的 shell-safe env。
3. 在保存 snapshot 前 unset 当前用户 env。
4. 追踪并 unset 上一轮已注入但本轮已删除的 key。

因此同一用户 set/delete 会在下一条命令立即生效，而用户 A 的 secret 不会存入 snapshot 被用户 B 继承。

### 4.3 Local environment cache

`tools/terminal_tool.py` 在 `TERMINAL_ENV=local` 且存在当前 identity 时，用 `identity.runtime_scope_key` 取代默认 task cache key。

结果：

- `slack.u123` 与 `slack.u456` 使用不同 local environment。
- `slack.u123` 与 `feishu.u123` 使用不同 local environment。
- 同平台同 user id 改名时复用原 local environment。

这项隔离只改变 local backend。Docker、SSH、Modal、Daytona 等后端沿用各自既有缓存/复用语义，除非单独实现同等隔离。

Workagent A2A 的审批/澄清恢复不改变上述 cache key：响应只是解除当前 agent thread 的阻塞，后续 terminal 仍沿用原始 `platform + user_id` 的 runtime scope。

## 5. Cron 身份、权限与环境

cron 有两阶段：创建时存在 live messaging identity，调度执行时通常不存在。为此 job record 额外保存不可变的 `identify`：

```json
{
  "platform": "slack",
  "user_id": "u123",
  "user_name": "alice"
}
```

### 5.1 创建与可见性

`tools/cronjob_tools.py` 创建 job 时从当前 `UserEnvIdentity` 写入 `build_job_identify(...)` 的结果。`cron/jobs.py` 规范化 `identify` 并把它列为 immutable field，update 不可转移 job 所属用户。

用户可见 job 集合由 `is_job_visible_to_identity(...)` 决定：

- `identify is None`: legacy/global job，对所有用户可见，可继续操作。
- 有效 identify: 只有当前 `platform + user_id` 同时匹配才可见；`user_name` 仅信息用途。
- malformed identify: 不当作 public，用户工具层不可见。

`cronjob` 的 `list`、`update`、`pause`、`resume`、`remove`、`run` 和 `context_from` 都只能在当前可见集合中解析 job id/name。故消息用户不能通过该工具读取或修改另一用户的 identified job。

重要边界：`cron/jobs.py` 是共享全局存储层，直接调用其低层 API 的 CLI、后端或管理员调用方仍拥有全局视图；权限边界实施在 user-facing `cronjob` 工具层。

### 5.2 Agent-backed cron job

Scheduler 运行 agent-backed job 时先解析 identify：

1. malformed identify 直接使 job 失败，不能降级为 public/空身份。
2. 有效 identify 时，将 `platform`、`user_id`、`user_name` 传入 `AIAgent`。
3. 同时设置 `agent._user_env_platform = identify.platform`。
4. 后续工具执行由 ContextVar binding 载入对应 `platform.user_id` payload。

这使 agent-backed cron 在没有 live gateway session 的情况下，仍能让 terminal 等工具使用 job 所属用户的环境变量。

### 5.3 `no_agent=True` 脚本 job

`no_agent=True` 不构造 `AIAgent`，也不经过 agent tool executor 或 local terminal snapshot cache；它直接执行 `$HERMES_HOME/scripts/` 中受路径校验的脚本。

当前实现仍支持该模式的用户环境恢复：

```text
run_job(job)
  -> parse_job_identify(job["identify"])
  -> _run_job_script(script, identify=...)
  -> _job_user_env_values(identify)
  -> load_user_env(platform, user_id, user_name)
  -> _sanitize_subprocess_env(base_env, user_env_overlay)
  -> script subprocess
```

所以带有效 identify 的 script 可读取该用户的 custom env 和 `CURRENT_USER_NAME`。无 identify 的 legacy/global script 不获得某位用户的专属 env；malformed identify 在 `run_job()` 起始处失败。这条路径采用一次性 subprocess env overlay，不会创建或复用 `LocalEnvironment` snapshot。

## 6. 并发与安全约束

- 隔离键必须始终使用 platform + stable user id，不能把 display name 用作身份边界。
- userenv、cron tool visibility 和 terminal cache 都依赖当前 ContextVar identity；新增执行路径时必须显式 bind/propagate。
- 保存 shell snapshot 前的 unset 是防止跨用户泄漏的必要条件，不能改为仅在 session cleanup 时处理。
- 用户环境值不应写入 tool response、日志或 clarify/card payload；`userenv` 只返回脱敏元数据。
- `identify=None` 的历史 job 是兼容性上的全局/public job。将其改成私有 job 需要单独迁移设计，不能在正常读取时隐式改变。

排查顺序：确认当前 identity，再检查 `users.env.json` 的 storage key、cron job 的 identify、当前 backend 类型、local runtime scope key，以及脚本/terminal 走的是哪条注入路径。

## 7. 验证

从仓库根目录执行：

```bash
venv/bin/python -m pytest tests/tools/test_user_env_store.py tests/tools/test_user_env_runtime.py tests/tools/test_userenv_tool.py -q
venv/bin/python -m pytest tests/tools/test_local_user_env.py tests/tools/test_userenv_terminal_isolation.py -q
venv/bin/python -m pytest tests/tools/test_cronjob_tools.py tests/cron/test_scheduler.py tests/cron/test_cron_no_agent.py -q
venv/bin/python -m pytest tests/gateway/test_shared_group_sender_prefix.py tests/tools/test_a2a_delegate_tool.py tests/workagent/backend/test_a2a_interactions.py tests/workagent/backend/test_a2a_executor.py -q
venv/bin/python -m py_compile tools/user_env_store.py tools/user_env_runtime.py tools/userenv_tool.py tools/cronjob_tools.py cron/jobs.py cron/scheduler.py
git diff --check
```

回归至少应覆盖：同平台不同用户、跨平台相同 user id、用户名变更、legacy 单记录迁移、多 legacy 候选拒绝迁移、`CURRENT_USER_NAME` 不可删除、变量值脱敏、local snapshot 不泄漏/删除即时生效、terminal cache scope、cron 跨用户不可见/不可操作、global job 兼容、malformed identify 失败、identified `no_agent` 脚本读取专属 env、Slack 直入 `<source>` 的首行与 `channel` 字段、Aegis 远端委托首轮和 follow-up 的 `<source>` 前缀，以及 Workagent 中 `agent.platform` 的 `_a2a` 标识与 `_user_env_platform` 原始平台分离、审批/澄清恢复后继续使用原始 userenv 分区。
