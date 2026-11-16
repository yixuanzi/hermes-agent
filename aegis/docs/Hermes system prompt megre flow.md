第一层：原生 System Prompt（一次构建，全局缓存）

agent/system_prompt.py:build_system_prompt_parts() 将 system prompt 分为 *三个 tier*，用 \n\n 拼成一条字符串：

┌─────────────────────────────────────────┐
│  Stable tier（不变）                      │
│  ├── SOUL.md / DEFAULT_AGENT_IDENTITY    │  ← 你的 Aegis 身份
│  ├── tool guidance（memory/skill 等）     │
│  ├── skills prompt                       │
│  ├── environment hints（macOS, Python）   │
│  ├── platform hints                      │
│  └── model operational guidance          │
├─────────────────────────────────────────┤
│  Context tier（会话级稳定）                 │
│  ├── context files（AGENTS.md 等）        │
│  └── system_message（如果有）             │
├─────────────────────────────────────────┤
│  Volatile tier（变化）                     │
│  ├── memory snapshot                     │
│  ├── USER profile                        │
│  ├── external memory provider block       │
│  └── timestamp / session_id / model      │
└─────────────────────────────────────────┘




*这整个是一条 {"role": "system", "content": "..."}*，被缓存为 agent._cached_system_prompt。

第二层：Ephemeral System Prompt（每次请求动态添加）

Gateway 在 run.py 第 17005-17010 行组装：

python
combined_ephemeral = context_prompt or ""                   # ← Current Session Context 块
                    + event_channel_prompt                   # ← 频道自定义 prompt
                    + self._ephemeral_system_prompt          # ← 全局 ephemeral 配置




第三层：API 调用时合并（conversation_loop.py 第 1000-1004 行）

python
effective_system = active_system_prompt or ""            # ← 原生 system prompt（缓存的整块）
if agent.ephemeral_system_prompt:
    effective_system = (effective_system + "\n\n" + agent.ephemeral_system_prompt).strip()
if effective_system:
    api_messages = [{"role": "system", "content": effective_system}] + api_messages




最终发给 LLM 的是 一条 system 消息，结构如下：

[原生 stable tier]
[原生 context tier]
[原生 volatile tier]
                    ← \n\n 分隔
[ephemeral: Current Session Context]
[ephemeral: channel_prompt]
[ephemeral: gateway configured]




架构设计意图

| 层级 | 缓存策略 | 目的 |
|------|----------|------|
| 原生 system prompt | 缓存到 session DB，跨轮次复用 | 保持 upstream prefix cache 命中 |
| ephemeral | 只拼接到 API 请求中，*不入 DB* | 不影响缓存，每次可以变化 |

context_prompt（Current Session Context）并没有替换或合并到原生 system prompt 里，它是在 API 调用时刻被附加到原生 system prompt 末尾的。 两者最终组成一条 {"role": "system"} 消息，用 \n\n 分隔。


# 精确分析：三个 ephemeral system prompt 的来源、构造与格式

三层拼接源代码（`run.py` 第 17005-17010 行）

python
combined_ephemeral = context_prompt or ""                                           # ← 第1块
event_channel_prompt = (channel_prompt or "").strip()
if event_channel_prompt:
    combined_ephemeral = (combined_ephemeral + "\n\n" + event_channel_prompt).strip()  # ← 第2块
if self._ephemeral_system_prompt:
    combined_ephemeral = (combined_ephemeral + "\n\n" + self._ephemeral_system_prompt).strip()  # ← 第3块




关键结论：这三个块拼接在一起后，作为整体传给 AIAgent(ephemeral_system_prompt=combined_ephemeral)。里面不含 [ephemeral: ...] 字面标签。

---

第①块：`context_prompt`（"Current Session Context"）

*来源函数*：`gateway/session.py::build_session_context_prompt()`

*格式*：纯 Markdown 文本，以 ## Current Session Context 开头，无任何 [ephemeral:] 标签。

*完整模板输出示例*（源码第 262-421 行逐条构造）：

## Current Session Context

**Source:** Slack (DM with Guisheng(郭桂生))
**User:** Guisheng(郭桂生)

**Platform notes:** You are running inside Slack. You do NOT have access to Slack-specific APIs ...

**Connected Platforms:** local (files on this machine), slack: Connected ✓

**Home Channels (default destinations):**
  - slack:  (ID: C0B5KKB3PGB)

**Delivery options for scheduled tasks:**
- `"origin"` → Back to this chat (...)
- `"local"` → Save to local files only (...)
- `"slack"` → Home channel (...)

*For explicit targeting, use `"platform:chat_id"` format if the user provides a specific chat ID.*




*动态附加内容*（在 run.py 8910-8975 行追加到 context_prompt 末尾）：
- *Session 自动重置标注*：`[System note: The user's session was automatically reset by the daily schedule. ...]`（当是 fresh session 时前置）
- *首次消息标注*：`[System note: This is the user's very first message ever. ...]`（首次交互时追加）
- *Discord 语音频道上下文*：追加语音频道状态（仅 Discord）
- *Thread 回复上下文*：追加 [Replying to: "父消息原文"]（由各平台 handler 注入到 message 的前缀中，而非在 context_prompt 内，参见 Slack handler 的 reply_to_text 处理）

---

第②块：`channel_prompt`（"channel_prompt"）

*来源*：`resolve_channel_prompt()` → 从平台 adpater 的 config.extra["channel_prompts"][channel_id] 读取

*格式*：用户/管理员在 config.yaml 中配置的纯文本字符串，格式完全自由。

yaml
# config.yaml 中的配置位置：
platforms:
  slack:
    config:
      extra:
        channel_prompts:
          C0B5KKB3PGB: "这是一个 Slack 私信频道"
          C1234567890: "你是 #ops 频道的助手，专注于故障响应"




*额外注入*（Telegram 特有）：
- Telegram 群组的 observe_prompt 会追加到 channel_prompt 末尾：
  python
  channel_prompt = f"{event.channel_prompt}\n\n{observe_prompt}"
    其中 observe_prompt 是 Telegram 监听模式下的群组上下文标注。

*Yuanbao 特有*：`_build_group_channel_prompt()` 动态构建包含群名称和 bot 身份的提示。

*默认值*：如果未配置 channel_prompts，此块为空字符串，不加入拼接。

---

第③块：`_ephemeral_system_prompt`（"gateway configured"）

*来源*：`_load_ephemeral_system_prompt()`（`run.py` 第 2913-2923 行）

*优先级*：
1. HERMES_EPHEMERAL_SYSTEM_PROMPT 环境变量（最高）
2. agent.system_prompt 中的值（`config.yaml`）

yaml
# config.yaml
agent:
  system_prompt: "You are a helpful assistant with personality XYZ."




*格式*：纯文本字符串，无任何特定封装或标签。

*在运行时修改*：支持通过 /personality <name> 或 API 调用动态修改：python
# run.py 第 10975 行
self._ephemeral_system_prompt = new_prompt  # 立即生效




---

最终 API 调用时的合并机制

在 chat_completion_helpers.py（第 1302-1306 行）：

python
effective_system = agent._cached_system_prompt or ""     # 原生 system prompt（三大 tier）
if agent.ephemeral_system_prompt:
    effective_system = (effective_system + "\n\n" + agent.ephemeral_system_prompt).strip()
if effective_system:
    api_messages = [{"role": "system", "content": effective_system}] + api_messages




*发送给 LLM 的结果*是一条 system 消息，内容结构如下：

```
[原生 Stable tier]
[原生 Context tier]
[原生 Volatile tier]               ← 以上是 _cached_system_prompt（跨轮次缓存、入 session DB）

[## Current Session Context         ← 第①块 context_prompt (动态，不入DB)
 **Source:** Slack (DM with ...)
 **User:** Guisheng(郭桂生)
 ...]

[用户自定义频道 prompt]              ← 第②块 channel_prompt (动态，不入DB)
[上午 10:31][用户自定义系统/个性提示]             ← 第③块 _ephemeral_system_prompt (动态，不入DB)
```

*每个块之间以 \n\n 分隔*，且 [ephemeral: ...] 标签并*不存在于实际 prompt 文本中*——文档中的标记只是用于说明来源的注释。