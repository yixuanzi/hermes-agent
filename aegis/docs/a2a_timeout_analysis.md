# A2A 委派过程中的超时参数分析

## 1. 前台输入超时（Foreground Input Timeout）

### 参数定义
```python
_DELEGATE_FOREGROUND_INPUT_TIMEOUT_SECONDS = 25 * 60  # 第29行
# 值：1500 秒（25分钟）
```

### 作用
- 在**循环模式（`is_loop=True`）**下，等待用户前台输入的最大超时时间
- 当超过此时间未收到用户输入，委派会自动关闭

### 使用位置
```python
# 第1472-1490行，_run_loop() 中的输入轮询
next_message = await asyncio.to_thread(
    _read_delegate_input,
    input,
    _DELEGATE_FOREGROUND_INPUT_TIMEOUT_SECONDS,  # 1500秒
)

if next_message is _DELEGATE_INPUT_TIMEOUT:
    _emit(output, "status", "input timeout", ...)
    return _build_a2a_payload(
        ...
        loop_exit_reason="input_timeout",
        final_response=_A2A_INPUT_TIMEOUT_MESSAGE,
        ...
    )
```

### 环境变量
- **无直接环境变量控制**，此参数为硬编码常数
- 但是可以通过修改代码进行调整

### 影响范围
- ✅ 循环委派模式
- ❌ 非循环委派模式（一次性委派不受此限制）

---

## 2. 远程任务轮询超时（A2A Poll Timeout）

### 参数定义
```python
_A2A_POLL_TIMEOUT_ENV = "A2A_POLL_TIMEOUT"        # 第33行，环境变量名
_A2A_POLL_TIMEOUT_DEFAULT = 120.0                  # 第35行，默认值
# 默认值：120 秒（2分钟）
```

### 作用
- 等待远程 A2A 任务完成的最大总超时时间
- 在 `_wait_for_final()` 方法中持续轮询远程任务状态，直到达到此超时或任务完成
- 防止长时间等待无响应的远程代理

### 使用位置
```python
# 第758-766行，_A2ADelegateSession.__init__
self.timeout = (
    float(timeout)
    if timeout is not None
    else _read_a2a_poll_setting(_A2A_POLL_TIMEOUT_ENV, _A2A_POLL_TIMEOUT_DEFAULT)
)

# 第1070-1104行，_wait_for_final() 方法中使用
deadline = time.monotonic() + self.timeout
while True:
    ...
    now = time.monotonic()
    if interaction_paused_at is None and now >= deadline:
        raise TimeoutError(f"Timed out waiting for task {getattr(current_task, 'id', None)!r}.")
    await asyncio.sleep(self.poll_interval)
```

### 环境变量控制
```bash
export A2A_POLL_TIMEOUT=300    # 设置为 300 秒（5分钟）
export A2A_POLL_TIMEOUT=60     # 设置为 60 秒（1分钟）
```

### 特殊机制：交互暂停
```python
# 第1082-1090行，当存在待处理的交互请求时，自动延长超时
if self._has_pending_interaction():
    if interaction_paused_at is None:
        interaction_paused_at = now
elif interaction_paused_at is not None:
    # 延长 deadline，补偿用户交互的等待时间
    deadline += now - interaction_paused_at
    interaction_paused_at = None
```

**关键点**：如果远程任务在等待用户交互（approval_request/clarify_request），超时计时会被**暂停**，直到用户完成交互。

### 影响范围
- ✅ 所有 A2A 委派模式（循环和非循环都受影响）

---

## 3. 远程任务轮询间隔（A2A Poll Interval）

### 参数定义
```python
_A2A_POLL_INTERVAL_ENV = "A2A_POLL_INTERVAL"      # 第34行，环境变量名
_A2A_POLL_INTERVAL_DEFAULT = 1.0                   # 第36行，默认值
# 默认值：1.0 秒
```

### 作用
- 轮询远程任务状态的**查询间隔**
- 每次轮询后，等待此时间再发起下一次轮询请求
- 控制与远程代理的通信频率，影响响应延迟和负载

### 使用位置
```python
# 第763-766行，_A2ADelegateSession.__init__
self.poll_interval = (
    float(poll_interval)
    if poll_interval is not None
    else _read_a2a_poll_setting(_A2A_POLL_INTERVAL_ENV, _A2A_POLL_INTERVAL_DEFAULT)
)

# 第1103行，_wait_for_final() 中的轮询循环
await asyncio.sleep(self.poll_interval)
current_task = await self._client.get_task(GetTaskRequest(id=current_task.id))
```

### 环境变量控制
```bash
export A2A_POLL_INTERVAL=0.5   # 每 0.5 秒轮询一次（更频繁）
export A2A_POLL_INTERVAL=2.0   # 每 2.0 秒轮询一次（更稀疏）
export A2A_POLL_INTERVAL=5.0   # 每 5.0 秒轮询一次（大幅降低频率）
```

### 性能含义
| 间隔值 | 优点 | 缺点 |
|------|------|------|
| 0.5秒 | 响应快，实时性好 | 频繁请求，网络/CPU 负载高 |
| 1.0秒（默认） | 平衡方案 | - |
| 2.0秒 | 降低负载 | 响应延迟增加 1 秒 |
| 5.0秒+ | 显著降低负载 | 延迟明显增加，不适合高交互任务 |

### 影响范围
- ✅ 所有 A2A 委派模式

---

## 4. HTTP 客户端超时（HTTPx Timeout）

### 参数定义
```python
# 第881-885行，_build_http_client() 方法
return httpx.AsyncClient(
    timeout=httpx.Timeout(
        connect=10.0,      # 连接超时
        read=None,         # 读取超时（无限）
        write=60.0,        # 写入超时
        pool=60.0,         # 连接池超时
    ),
    headers=self.headers or None,
)
```

### 各个超时的作用

| 超时类型 | 值 | 作用 | 场景 |
|---------|-----|------|------|
| **connect** | 10.0秒 | 建立 TCP 连接的最大时间 | 连接到远程代理服务器 |
| **read** | None（无限） | 等待服务器响应数据的时间 | 接收远程任务的响应（可能很长） |
| **write** | 60.0秒 | 上传请求数据到服务器的最大时间 | 发送消息/请求到远程代理 |
| **pool** | 60.0秒 | 从连接池获取可用连接的最大时间 | 连接复用 |

### 关键设计
```python
read=None  # 读取超时为 None（无限制）
```

**原因**：远程任务可能需要长时间处理，网络读取不应该被打断。真正的超时控制由 `A2A_POLL_TIMEOUT` 负责。

### 使用位置
```python
# 第1019行，发送交互响应时
response = await self._http_client.post(self._interaction_url(), json=payload)

# 第1044行，发送消息时
async for event in self._client.send_message(request):
    ...
```

### 环境变量
- **无环境变量控制**，硬编码在代码中
- 需要修改代码才能调整

### 影响范围
- ✅ 所有网络通信（消息发送、任务查询、交互响应）

---

## 5. Agent Card 获取超时（Discovery Timeout）

### 参数定义
```python
# 第79-89行，_fetch_agent_card() 函数
response = httpx.get(card_url, timeout=5.0, headers=headers or None)
```

### 作用
- 获取远程代理的能力声明（`.well-known/agent-card.json`）的超时时间
- 在 A2A 注册表刷新时调用，用于发现代理的 API 和交互能力

### 使用位置
```python
# 第227行，_refresh_a2a_registry() 中
card, error = _fetch_agent_card(entry["url"], headers=entry.get("headers") or None)
```

### 环境变量
- **无环境变量控制**，硬编码为 5.0 秒

### 影响范围
- ✅ A2A 代理注册表刷新（`a2a_list` 命令）
- ❌ 实际委派过程中无影响

---

## 总结对比表

| 超时参数 | 值 | 环境变量 | 作用域 | 控制权 | 优先级 |
|---------|-----|---------|-------|-------|-------|
| **前台输入超时** | 1500秒(25min) | 无 | 循环模式 | 硬编码 | 最外层 |
| **任务轮询超时** | 120秒(2min) | `A2A_POLL_TIMEOUT` | 全部模式 | 可配置 | 中 |
| **轮询间隔** | 1.0秒 | `A2A_POLL_INTERVAL` | 全部模式 | 可配置 | 中 |
| **HTTP连接超时** | 10秒 | 无 | 网络层 | 硬编码 | 高 |
| **HTTP写入超时** | 60秒 | 无 | 网络层 | 硬编码 | 高 |
| **HTTP读取超时** | 无限 | 无 | 网络层 | 硬编码 | 高 |
| **AgentCard超时** | 5秒 | 无 | 注册表发现 | 硬编码 | 低 |

---

## 超时流程图

```
┌─────────────────────────────────────────────────────────────────┐
│ a2a_delegate() - 前台循环模式                                      │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
                    初始化会话，发送第一条消息
                              │
                    ┌─────────┴─────────┐
                    ▼                   ▼
           等待远程任务完成         等待用户输入
        (A2A_POLL_TIMEOUT)    (1500秒，仅循环模式)
                    │                   │
                ┌───┴───┐           ┌───┴───┐
                ▼       ▼           ▼       ▼
            超时  完成        超时   收到输入
                │       │           │      │
                └───┬───┘           └──┬───┘
                    │                  │
                    ▼                  ▼
            ┌──────────────────────────────┐
            │ 轮询远程任务状态              │
            │ 间隔：A2A_POLL_INTERVAL(1秒) │
            │ HTTP连接/写入超时:10/60秒   │
            └──────────────────────────────┘
                    │
                ┌───┴─────┐
                ▼         ▼
            收到响应   网络超时
                │         │
                └─────┬───┘
                      ▼
            ┌─────────────────────┐
            │ 处理交互（如有）     │
            │ 计时暂停&恢复       │
            └─────────────────────┘
                      │
                      ▼
            ┌─────────────────────────┐
            │ 任务完成或超时          │
            │ 返回最终响应            │
            └─────────────────────────┘
```

---

## 最佳实践建议

### 1. **默认配置足够吗？**
- ✅ 大多数场景都可以使用默认值（120秒轮询、1秒间隔）
- ⚠️ 长任务（>2分钟）需要调整 `A2A_POLL_TIMEOUT`

### 2. **如何处理超时？**

```bash
# 场景1：远程代理响应慢，频繁超时
export A2A_POLL_TIMEOUT=300    # 增加到 5 分钟
export A2A_POLL_INTERVAL=2.0   # 降低轮询频率

# 场景2：需要快速响应
export A2A_POLL_TIMEOUT=60     # 减少到 1 分钟
export A2A_POLL_INTERVAL=0.5   # 增加轮询频率

# 场景3：网络环境不稳定
# 硬编码中的 HTTP connect=10秒 可能过短，需要修改代码
```

### 3. **交互暂停机制**
当远程任务要求用户确认（approval_request）或输入信息（clarify_request）时：
- ✅ 任务轮询超时会自动暂停
- ✅ 可以安心等待用户交互（不会被轮询超时打断）
- ✅ 交互完成后，超时恢复计数

### 4. **循环模式特殊性**
```python
# 循环模式下的两层超时
一级：A2A_POLL_TIMEOUT   (等待远程任务完成)
  └─ 二级：1500秒        (等待下一条用户输入)
```

---

## 代码调整指南

如需修改硬编码超时：

| 参数 | 修改位置 | 当前值 | 建议范围 |
|-----|---------|-------|---------|
| 前台输入超时 | 第29行 | 1500秒 | 600-3600秒 |
| HTTP连接超时 | 第883行 | 10秒 | 5-30秒 |
| HTTP写入超时 | 第884行 | 60秒 | 30-120秒 |
| AgentCard超时 | 第82行 | 5秒 | 3-10秒 |
