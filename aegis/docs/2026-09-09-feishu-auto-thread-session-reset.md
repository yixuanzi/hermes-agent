# Feishu 自动话题与 Session Reset 改造总结

## 1. 改造目标

本次改造为 Feishu 普通群和私聊增加“顶层消息自动创建话题，并在后续消息中复用同一个 Hermes session”的能力。

适用范围：

- 普通群顶层消息；
- 私聊顶层消息；
- 通过环境变量控制是否启用；
- 已经处于 Feishu 话题中的消息继续使用原有话题路由；
- forum/话题群等已有特殊聊天类型保持原有行为。

核心实现位于 [`plugins/platforms/feishu/adapter.py`](../../plugins/platforms/feishu/adapter.py)。本次生产代码尽量只修改 Feishu adapter，gateway 核心的发送、进度、审批、澄清、媒体和错误通知链路继续复用统一 metadata。

## 2. 自动话题的路由模型

Feishu 使用两类 ID：

- `om_*`：消息 ID，也作为自动话题的根消息 ID；
- `omt_*`：Feishu 创建出来的真实话题 ID。

### 2.1 首条顶层消息

当消息满足以下条件时，adapter 将消息 ID 作为预期话题根：

- `FEISHU_REPLY_THREAD` 已启用；
- 消息来自普通群或私聊；
- 入站消息没有 `thread_id` 和 `root_id`；
- 消息 ID 可用。

例如，首条消息为 `om_root_1` 时，source 会暂时使用：

```text
thread_id = om_root_1
```

session key 的逻辑形式为：

```text
agent:main:feishu:group:<chat_id>:om_root_1
agent:main:feishu:dm:<chat_id>:om_root_1
```

发送层不会把 `om_*` 当作 `receive_id_type=thread_id`。它会把 `om_*` 当作 reply anchor，并调用 Feishu reply API，设置 `reply_in_thread=true`，由 Feishu 创建真实的 `omt_*` 话题。

### 2.2 后续话题消息

后续消息通常包含：

```text
thread_id = omt_real_thread
root_id   = om_root_1
```

adapter 会用 `root_id` 构造候选 session key，并检查该 root 是否仍有活动或持久化 session：

- 找到 root session：使用 `om_root_1`，继续复用原 session；
- 找不到 root session：认为这可能是人工创建的话题，保留 `omt_real_thread`，维持原有话题路由。

这个判断位于 [`_session_exists_for_source()`](../../plugins/platforms/feishu/adapter.py) 及 Feishu 入站处理逻辑中。其目的，是避免把没有 Hermes 历史 session 的人工 Feishu 话题错误地绑定到某个 root session。

## 3. Session 状态的内存与持久化边界

自动话题并不只依赖内存缓存。

### 3.1 仅存在于内存的状态

- `_active_sessions`：当前正在处理中的 session 锁和中断事件，不是长期会话存储；服务重启后会丢失。
- `_pending_auto_thread_roots`：首次自动话题发送成功前的 pending 状态，只用于失败降级控制；按数量限制保存，没有时间 TTL。
- `_failed_auto_thread_roots`：记录本轮自动话题创建失败，避免 final、进度、错误等路径重复创建话题；同样是进程内状态。
- AIAgent cache：用于复用模型 Agent 和 prompt cache。缓存被 idle TTL 清理，不代表 Hermes session 被删除。

### 3.2 持久化状态

真正用于跨消息、跨进程恢复的是 SessionStore：

- `session_key -> session_id` 的路由映射持久化在 `state.db` 的 gateway routing 表；
- 旧版本或 SQLite 不可用时兼容 `sessions.json`；
- session transcript 和 session metadata 由 session store 持久化。

因此，单纯的 AIAgent cache 清理或服务重启，不应导致 session 丢失。只要路由映射仍然存在且 session 没有被 reset/prune，后续带有 `omt_* + root_id` 的消息仍可以通过 `root_id` 找回原 session。

## 4. 重要行为：自动清理后继续在原话题聊天

这里需要区分两种“清理”。

### 4.1 AIAgent cache 清理

AIAgent cache 被 idle TTL、LRU 或内存压力机制清理时：

- 只释放内存中的 Agent、LLM client 和工具资源；
- SessionStore 的路由映射仍保留；
- 下一条消息仍使用相同 session key；
- 系统会从持久化 transcript 重新构建 Agent。

这种清理不会导致新 session。

### 4.2 Session 自动过期或 reset

当 `session_reset` 策略触发时，处理的是完整的会话边界，而不只是内存缓存：

1. 旧 session 被 finalize；
2. 关闭旧 Agent 的工具和 memory 资源；
3. 清理 conversation-scoped 状态，例如 model override、审批、澄清、队列和运行代次；
4. 旧 session 在数据库中标记为 `session_reset`；
5. 后续消息会创建新的 `session_id`；
6. 旧 transcript 通常仍保留，可通过 `/resume` 找回。

在 gateway 尚未重启时，root 路由可能仍暂时存在。此时后续消息通常仍以 `om_root_*` 作为路由 key，但 SessionStore 会将它绑定到一个新的 session ID。

然而，服务重启时会加载并清理指向已结束 session 的旧路由。于是可能出现下面的链路：

```text
自动话题首条消息
  -> root session key = chat_id + om_root_id
  -> session_reset 结束旧 session
  -> 重启时清理已结束的 root 路由
  -> 后续消息带 omt_* + root_id
  -> 查不到 root session
  -> adapter 保留 omt_*
  -> 创建/使用新的 omt_* session
```

因此，当前改造的准确语义是：

- AIAgent cache 清理后，继续使用原 session；
- 未发生 session reset/prune 时，服务重启后继续使用原 session；
- 发生 session reset 后，继续聊天会使用新 session；
- 如果 reset 后又重启，root 路由可能丢失，后续会退回真实 `omt_*` 路由；
- 这不是 Feishu `omt_*` 本身失效，而是当前 root session 已经结束且 root 路由被清理。

这也解释了实测中“重启后进入之前的 Feishu 话题，却产生新会话”的现象：实际配置启用了 daily reset，旧 root session 在凌晨 4 点已经被结束，重启只是让 stale routing prune 显式清理了 root 映射。

如果产品要求“session reset 后也必须继续使用同一个 session”，就不能启用会话自动 reset；如果产品要求“允许生成新的 session，但仍始终在同一个 Feishu 话题内回复”，则还需要独立持久化“该 Feishu root 是 Hermes 自动创建话题”的标记，不能只依赖当前 live session mapping。

## 5. `session_reset` 配置说明

配置位置通常是：

```text
~/.hermes/config.yaml
```

### 5.1 参数表

| 参数 | 可选值/类型 | 默认值 | 作用 |
| --- | --- | --- | --- |
| `mode` | `none`、`idle`、`daily`、`both` | `none` | 选择自动 reset 策略 |
| `idle_minutes` | 正整数，分钟 | `1440` | `idle`/`both` 模式下，连续无活动多久后 reset；1440 分钟等于 24 小时 |
| `at_hour` | `0`–`23` | `4` | `daily`/`both` 模式的本地时间小时边界；`4` 表示每天凌晨 4 点 |
| `notify` | 布尔值 | `true` | 自动 reset 后是否发送用户通知；有活动的 session 才会通知 |
| `notify_exclude_platforms` | 平台名列表 | `api_server`、`webhook` | 不发送自动 reset 通知的平台 |
| `bg_process_max_age_hours` | 小时数 | `24` | 后台进程多长时间后不再阻止 session reset；进程不会被杀死，只是不再作为保活条件 |

`mode: both` 的触发条件是 idle 或 daily 任意一个满足即可，不是两个条件同时满足。实现中 idle 检查在 daily 之前，因此两个条件同时满足时，记录的原因可能是 `idle`。

reset 判断使用 session 的 `updated_at`，并以服务所在机器的本地时间计算 `at_hour`。后台 session expiry watcher 默认启动后等待 60 秒，再大约每 5 分钟检查一次；新消息到达时还会进行一次同步的 reset 判断。

### 5.2 常见配置

保持 session 长期不因时间自动切换：

```yaml
session_reset:
  mode: none
```

只按空闲时间 reset，例如 7 天：

```yaml
session_reset:
  mode: idle
  idle_minutes: 10080
```

每天凌晨 4 点 reset：

```yaml
session_reset:
  mode: daily
  at_hour: 4
```

同时启用 24 小时 idle 和凌晨 4 点 daily：

```yaml
session_reset:
  mode: both
  idle_minutes: 1440
  at_hour: 4
```

如果还希望避免长期不活跃的 routing entry 被清理，需要同时关注顶层配置中的：

```yaml
session_store_max_age_days: 90
```

该值默认是 90 天，按 `updated_at` 清理 session key 到 session ID 的路由映射；设置为 `0` 可关闭这项 prune。它不会立即删除 SQLite transcript，但映射被清除后，Feishu 自动话题的当前实现可能无法再从 `root_id` 识别出原来的自动话题 session。

平台级 reset 配置可以在 `~/.hermes/gateway.json` 中覆盖默认策略，例如：

```json
{
  "reset_by_platform": {
    "feishu": {
      "mode": "none"
    }
  }
}
```

平台覆盖优先于全局默认策略；具体优先级为 platform override、session type override、default policy。

如果目标是尽可能长期复用 Feishu 自动话题的同一个 session，建议至少使用：

```yaml
session_reset:
  mode: none

session_store_max_age_days: 0
```

这只控制 Hermes session 生命周期，不影响 Feishu 话题是否创建。自动创建话题仍由下面的环境变量独立控制。

## 6. `FEISHU_REPLY_THREAD` 配置说明

```bash
FEISHU_REPLY_THREAD=true   # 默认值：普通群和私聊顶层消息自动创建话题
FEISHU_REPLY_THREAD=false  # 直接在原聊天中回复，不自动创建话题
```

支持的关闭值包括 `false`、`0`、`no`、`off`；未设置、空值或非法值默认启用，非法值会记录 warning。

该变量只控制“无 thread 的顶层 Feishu 消息是否自动创建话题”，不控制 session reset，也不关闭已有 Feishu 话题中的正常回复。修改后需要重启 gateway，使 adapter 重新加载配置。

## 7. 手动 `/new` 与 `/reset` 的作用

`/new` 和 `/reset` 是显式的会话边界操作，不等同于清理 AIAgent cache：

- 立即使当前 session 的运行代次失效；
- 清理正在运行的 Agent 和工具资源；
- 清除会话级 model、审批、澄清、队列等状态；
- 创建新的 session ID；
- 旧 transcript 保留在历史存储中；
- 触发 `session:end`、`session:reset` 和生命周期 finalize 相关钩子。

对于 Feishu 自动话题，手动 reset 通常会沿用当前 root routing key，但切换到新的 session ID。它的作用是让用户主动开始一个干净上下文，同时仍可通过 `/resume` 找回旧会话。

## 8. 测试与验证结果

本次改造覆盖了：

- 环境变量默认开启、显式关闭和非法值回退；
- 普通群、私聊顶层消息的自动 root session；
- 已有话题和 forum 行为保持不变；
- root session 的活动/持久化复用；
- `reply_in_thread=true` 和 root message reply anchor；
- 自动话题失败后的一次性普通消息降级；
- 真实 `omt_*` 话题失败时不错误降级到顶层聊天；
- final、进度、审批、澄清和媒体发送链路。

定向验证结果：

```text
158 passed, 9 subtests passed
Ruff: All checks passed
Python compileall: passed
git diff --check: passed
```

## 9. 排查建议

遇到 Feishu 话题重启后变成新 session 时，按以下顺序检查：

1. 查看入站消息是否同时包含 `thread_id=omt_*` 和 `root_id=om_*`；
2. 查看 Feishu adapter 生成的 session key 是 root `om_*` 还是真实 `omt_*`；
3. 查看 `session_reset.mode`、`idle_minutes` 和 `at_hour`；
4. 查看 gateway 日志中是否有 `Session expiry`、`end_reason='session_reset'` 或 `pruning stale sessions`；
5. 查看 `session_store_max_age_days` 是否清除了长期不活跃的 routing entry；
6. 如果只是不再复用模型 Agent，但 session key 未变化，这是正常的 AIAgent cache 重建，不等价于新 session。

