# rbac-guard — Hermes RBAC 权限插件

双层防御的 RBAC 层：**prompt 软约束 + 代码硬控制**，按"平台用户身份 → 角色 → 权限"工作。

## 架构

```
用户消息（Feishu/Telegram/CLI…）
   │
   ▼
pre_llm_call 钩子 ──── 拿到 platform + sender_id ──► 查 Aegis user_roles 表
   │                                                    │
   │  返回 {"context": 角色约束块}                        ▼
   ▼                                        prompt_block() 注入本轮 user message
LLM 生成回复/工具调用（软约束：模型看到角色限制）        【prompt 逻辑层】
   │
   ▼
pre_tool_call 钩子 ──── 拿到 tool_name + args ──► resolve_identity()（每轮身份缓存）
   │                                                │
   │  白名单放行 / 黑名单 block / 危险操作 approve     ▼
   ▼                                        tool_allowed() 硬判定    【代码执行层】
Hermes core: resolve_pre_tool_block()
   ├─ block   → 工具不执行，message 成为 tool result（fail-closed）
   └─ approve → 升级人工审批门，拒绝/超时 = 拒绝（fail-closed）
```

## 关键事实（源码验证）

- **插件不能改 system prompt**。`pre_llm_call` 返回的 `{"context": ...}` 永远注入
  **本轮 user message**（保护 prompt cache 前缀）。效果等同：模型每轮生成前都先看到角色约束。
- `pre_tool_call` 返回 `{"action":"block","message":...}` 时由 core 的
  `hermes_cli.plugins.resolve_pre_tool_block()` 统一执行，审批门出错也会 fail-closed 成 block。
- 群聊 session_key 只含群 ID（如 `agent:main:feishu:group:oc_xxx`），不含个人 uid。
  本插件用 pre_llm_call 阶段的 platform+sender_id 建 `_IDENTITY_CACHE`
  （`session_id:turn_id` → 身份），工具层优先从同一轮缓存取个人身份，
  保证两层看到同一个用户。不能只按 session_id 缓存，否则同一群组 session
  的不同用户会互相覆盖身份；缺少 turn_id 时不会读取 session 级缓存。
- 身份永远来自 hook 注入的 platform/sender_id，**不接受 LLM 在 args 里自报角色**。

## 文件

- `plugin.yaml` — manifest
- `__init__.py` — hooks + 管理工具注册
- `role_rules.json` — 角色规则配置（危险命令正则、prompt 约束、工具白名单/黑名单、参数正则约束）
- `roles.py` — 数据库身份映射、配置加载校验、权限判定与审计日志
- `<HERMES_HOME>/aegis.db:user_roles` — 运行期角色表，Users 页面修改后下一次判定立即生效
- `data/audit.log` — JSONL 审计：context_injected / tool_blocked / escalate_approval / role_set / tool_executed；默认关闭，仅 `AEGIS_RBAC_AUDIT=true` 时输出

## 角色模型（role_rules.json）

| 角色 | 工具控制 | 危险操作 |
|---|---|---|---|
| admin | 全放行 | 免审批 |
| operator | 黑名单（github 等） | rm -rf/git push 等 → 升级人工审批 |
| user | 只读规则 | 危险操作需审批 |

未登记用户，以及数据库中出现无法识别角色的用户，统一按 `user` 角色处理，
使用 `user` 的完整规则，不再存在独立的 `unknown` 角色。

顶层 `dangerous_pattern` 是用于审批升级的大小写不敏感正则。它匹配
`terminal`、`execute_code` 和 `computer_use` 的调用参数；`user` 与 `operator`
命中后会进入人工审批。修改该字段无需改代码，重启 Hermes 后会加载新的配置。

```json
{
  "dangerous_pattern": "(rm\\s+-rf|git\\s+push|shutdown|reboot)",
  "admin": { "...": "..." },
  "operator": { "...": "..." },
  "user": { "...": "..." }
}
```

`tools_paras` 使用按工具分组的规则列表，每条规则再按参数名配置正则。
工具被列出后，列表中的每条规则都要检查：规则里配置的参数如果出现在调用参数
中，必须通过 `re.search` 包含匹配；调用参数中**没有**该 key 时视为跳过，
不判定失败。规则之间使用 AND 逻辑。未配置参数规则的工具不增加限制。

```json
{
  "tools_paras": {
    "terminal": [{
      "command": "^ls(\\s|$)",
      "cwd": "/workspace"
    }]
  }
}
```

参数规则在 `allow_tools` / `denied_tools` 判定之后执行，黑名单拒绝优先。

### 权限判定优先级

每次工具调用按以下顺序进行判定：

1. **`denied_tools` 黑名单**：工具命中黑名单时立即拒绝，不再进行后续判断。
2. **`allow_tools` 白名单**：当值为数组时，只有数组中明确列出的工具才允许继续；值为 `null` 时不启用白名单。
3. **`tools_paras` 参数约束**：工具配置了参数规则时，列表中的每条规则逐一检查；规则中配置的参数只要出现在本次调用参数里，就必须使用 `re.search` 匹配成功，调用参数未携带该 key 则跳过（不算失败）；规则之间为 AND 关系。
4. **危险操作审批**：通过前述权限和参数检查后，`user`、`operator` 的危险操作进入人工审批；`admin` 直接继续执行。

因此，工具同时出现在 `allow_tools` 和 `denied_tools` 中时，`denied_tools` 优先。例如：

```json
{
  "allow_tools": ["terminal"],
  "denied_tools": ["terminal"]
}
```

此时 `terminal` 仍会被拒绝。

## 管理

```bash
hermes plugins list | grep rbac        # 状态
hermes plugins enable rbac-guard       # 启用（写 plugins.enabled）
# 角色身份映射: 在 Aegis Users → 角色管理中维护 user_roles
# 会话内: 让 agent 调 rbac_status / rbac_set_role 工具；变更写入 user_roles 后立即生效
# role_rules.json 修改后需重启 Hermes 以重新加载规则配置
# RBAC 审计默认关闭；需要时设置 AEGIS_RBAC_AUDIT=true
# HERMES_RBAC_AUDIT 可选地指定审计 JSONL 文件路径
```

## 已验证（真实 hermes chat 会话）

1. 未登记用户询问身份 → 返回 `user` 角色约束（软约束注入 ✓）
2. 未登记用户请求执行受限命令 → 模型依据 `user` 规则拒绝（prompt 层 ✓）
3. 强制未登记用户调用受限工具 → 工具被 core 拦截，返回 `[RBAC] 已拒绝`（硬控制 ✓）
4. 提权 admin 后同一请求 → 命令真实执行，输出返回（放行 ✓）
5. args 里伪造 `role=admin` → 判定不变（身份不可伪造 ✓）

## tool paras 参数正则语法
1. 包含匹配
/output/

2. 必须以 ls 开头
^ls

3. 必须完整匹配
^ls -la$

4. 禁止出现危险字符串
^(?!.*(?:rm\s+-rf|shutdown)).*$
