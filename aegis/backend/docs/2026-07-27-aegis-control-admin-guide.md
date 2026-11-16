# Aegis 管理员使用手册：CONTROL

本文面向 Aegis 管理员，说明侧栏 **CONTROL** 分组的管理功能、操作步骤、风险和推荐运行顺序。管理员也应阅读《Aegis 普通用户使用手册：CORE MODULES》，了解最终用户可见的基础体验。

适用版本：Aegis `v0.2.3`。

## 1. 适用人群、权限与菜单范围

**面向人群**

- Aegis 平台管理员、A2A Agent 运营人员、安全平台主管、审计管理员；
- 被授予 `is_admin=true` 的账号；
- 负责远程 Agent 注册、授权策略、用户生命周期、服务重启和审计复核的人员。

**管理员专属菜单**

```text
CONTROL
├─ Agent Orchestration（工作智能体编排）
├─ Routing Policy（路由策略配置）
├─ User Management（用户管理）
├─ System Settings（系统设置）
└─ Audit Logs（审计日志）
```

普通用户不会看到上述菜单，也不能调用对应管理接口。管理员所有重要变更应遵循组织的变更审批、最小权限和审计留痕要求。

## 2. 管理前准备与推荐顺序

建议按以下顺序完成首次接入或重大变更：

1. 在受控网络中部署并验证远程 A2A Work Agent；
2. 在 **Agent Orchestration** 注册 Agent，并验证其 Agent Card；
3. 在 **Routing Policy → Agent Policy** 配置最小必要的允许/拒绝范围；
4. 按需维护 **Global Routing Rules** 作为路由指导；
5. 在普通账号中验证 Overview、Chat Agents 悬浮窗和实际授权行为；
6. 在 **Audit Logs** 复核 `succ`、`fail`、`auth_denied` 结果；
7. 仅在必要时使用 **System Settings** 重启服务。

**通用注意事项**

- 生产环境的 Agent 地址、认证 Header 和策略改动应先在测试环境验证。
- 不要在截图、工单正文或普通聊天中暴露 Bearer Token、A2A 通信 Header 或用户密码。
- 任何删除操作均应先导出或记录当前配置；Aegis 界面中的删除通常不可恢复。

## 3. Agent Orchestration（工作智能体编排）

| 项目 | 内容 |
| --- | --- |
| 面向人群 | A2A Agent 管理员、平台管理员 |
| 功能 | 注册、浏览、编辑、筛选、刷新和删除远程 Work Agent；维护其地址、认证 Header、状态和补充能力描述 |
| 功能路径 | **CONTROL → Agent Orchestration（工作智能体编排）** |
| 配置存储 | `<HERMES_HOME>/a2a.json` |

### 3.1 页面信息

页面顶部显示已登记总数，以及 `Active`、`Idle`、`Offline` 数量。列表包含：

| 字段 | 含义 |
| --- | --- |
| Agent ID | Aegis 中的唯一登记键，也是委派和策略匹配使用的 Agent 名称 |
| Status | Aegis 是否将其作为活跃上下文候选；`Active` 才会进入当前 `/a2a` 上下文 |
| A2A Address | 远程 A2A 服务地址，通常为 `https://host.example/a2a` |
| Description | 面向用户和路由的简短业务说明 |
| Capabilities | Aegis 本地补充的能力说明（`extcapabilities`） |

### 3.2 注册新 Agent

1. 进入 **Agent Orchestration**，选择 **注册智能体**。
2. 在悬浮窗口填写：
   - **Agent ID**：稳定、唯一的标识，例如 `incident-responder`；创建后不能在编辑窗口修改；
   - **A2A 地址**：远程 A2A JSON-RPC 地址，例如 `https://work-agent.example.com/a2a`；
   - **验证头字段**：通常为 `Authorization`；
   - **验证字段值**：通常为 `Bearer <token>`；
   - **通信心跳状态**：初次建议选择 `Idle`，完成连通性验证后再设为 `Active`；
   - **功能描述**：说明适用任务、边界与预期输出；
   - **技能描述 / 能力列表**：选择 **添加能力**，逐项填写 Aegis 本地补充能力。
3. 选择 **保存（Save）**。
4. 选择 **Refresh**，确认新条目已写入列表。
5. 前往 **Aegis Chat → CENTRAL ARCHIVE → Agents**，刷新并确认 Card、状态和能力能正确显示。

**注意事项**

- Agent ID 必须长期稳定；更名应按“新建 → 迁移策略 → 验证 → 删除旧条目”的流程完成。
- Aegis 会用登记 URL 探测 `{A2A 地址}/.well-known/agent-card.json`。若登记 URL 为 `/a2a`，服务应发布 `/a2a/.well-known/agent-card.json`；建议同时保留标准根 Card。
- 只有远端 Agent Card 可读、A2A 地址可达、认证 Header 正确时，Agent 才适合标记为 `Active`。
- 当前 Header 配置会持久化在本地 Aegis 配置中；使用最小作用域、可轮换的专用 token，并限制 `<HERMES_HOME>` 的文件访问权限。
- `Capabilities` 仅补充 Aegis 本地展示与上下文；应在远端 Agent Card 的 `skills` 中同时发布真实、可路由的能力。

### 3.3 编辑、停用与删除 Agent

**编辑**

1. 在列表目标行选择编辑图标。
2. 修改地址、认证 Header、状态、描述或能力列表。
3. 保存后刷新页面，再通过 Chat Agents 窗口复核。

**临时停用**

1. 编辑 Agent。
2. 将状态改为 `Idle` 或 `Offline` 并保存。
3. 确认该 Agent 不再作为 `Active` A2A 上下文候选。

**删除**

1. 先确认没有 Agent Policy、运行任务或 Global Routing Rule 依赖该 Agent ID。
2. 在列表目标行选择删除图标。
3. 阅读不可逆确认提示后确认删除。

**注意事项**

- 仅因远端暂时故障时，优先改为 `Offline`，不要立即删除，这样可保留配置和审计关联。
- 删除登记不会停止远端 Work Agent 进程，也不会撤销该服务外部已发放的凭据。
- 更新认证 token 后，应立即使用 Chat Agents 的刷新按钮验证；旧 token 不应继续保留在运维记录中。

## 4. Routing Policy（路由策略配置）

| 项目 | 内容 |
| --- | --- |
| 面向人群 | 授权策略管理员、A2A 平台管理员 |
| 功能 | 维护全局路由指导规则，以及控制调用者能否委派到某一 Agent 的 Agent Policy |
| 功能路径 | **CONTROL → Routing Policy（路由策略配置）** |
| 配置/数据存储 | Global Routing Rules：`<HERMES_HOME>/a2a.json`；Agent Policy：`<HERMES_HOME>/aegis.db` |

页面有两个视图：**Global Routing Rules** 和 **Agent Policy**。两者目的不同，必须分别维护。

### 4.1 Global Routing Rules

**功能**

维护面向 Aegis 路由上下文的全局文字规则，例如统一分流原则、禁止条件、兜底处置要求。每条规则包含名称、规则内容和启用状态。

**使用方法：新建规则**

1. 进入 **Routing Policy**，保持在 **Global Routing Rules** 标签。
2. 选择 **新建路由规则**。
3. 填写：
   - **Rule Name**：可识别的名称，例如“高风险账号事件优先人工确认”；
   - **Rule Content**：完整、可执行的文字说明；
   - **Status**：选择 `Enabled` 或 `Disabled`。
4. 选择 **保存规则**。
5. 使用列表搜索、刷新和编辑操作维护规则。

**推荐写法**

```text
当请求包含账号接管、高权限变更或不可逆隔离动作时：
先输出证据、影响范围和建议；在得到用户明确授权前，不执行处置。
```

**注意事项**

- Global Routing Rules 是全局路由/提示上下文，不是代替访问控制的安全边界；真正的委派许可由下一节的 Agent Policy 决定。
- 当前表单只保存名称、文字策略和状态，不提供可视化的“条件 → 固定 Agent”机器规则编辑器。不要将其误认为确定性的自动分配引擎。
- 规则内容应明确优先级、适用范围、禁止动作和兜底行为；避免相互矛盾的模糊表述。
- 删除或停用前检查是否影响现有 SOP、演练脚本和用户模板。

### 4.2 Agent Policy（委派访问控制）

**功能**

Agent Policy 决定某个调用者是否可以委派到指定 Agent。匹配字段为：`Platform`、`User ID`、`Agent`；每个字段均可使用 `*` 表示任意值。

**关键规则：最小 rank 优先**

1. 系统查找所有匹配 Platform、User ID 和 Agent 的规则。
2. **数值最小的 `Rank`** 先命中并决定 `Allow` 或 `Deny`。
3. 如果没有任何规则匹配，当前实现的默认结果是 **Allow**。

因此，应该使用低 rank 放置明确拒绝或高优先级例外，而不是依赖列表显示顺序。

**新建策略方法**

1. 在 **Routing Policy** 选择 **Agent Policy** 标签。
2. 选择 **New Policy**。
3. 填写：
   - **Rank**：正整数，且不能与现有 Rank 重复；数值越小优先级越高；
   - **Platform**：来源平台标识；使用 `*` 匹配所有平台；
   - **User ID**：调用者用户 ID；使用 `*` 匹配所有用户；
   - **Agent**：目标 Agent ID；可从下拉提示中选择，或使用 `*` 匹配所有 Agent；
   - **Decision**：选择 `Allow` 或 `Deny`。
4. 选择 **Save Policy**。
5. 用受控测试账号发起委派，并在 **Audit Logs** 中确认 `succ` 或 `auth_denied` 结果。

**示例**

| Rank | Platform | User ID | Agent | Decision | 意图 |
| ---: | --- | --- | --- | --- | --- |
| 10 | `aegis` | `contractor-uid` | `incident-responder` | `deny` | 禁止外包账号调用处置 Agent |
| 20 | `aegis` | `*` | `incident-responder` | `allow` | 允许其他 Aegis 用户调用该 Agent |
| 100 | `*` | `*` | `*` | `deny` | 默认拒绝兜底（需要显式 allow） |

**注意事项**

- 不要同时为同一范围配置互相冲突且 rank 接近的规则；优先级判断只看最小 rank。
- 因为未命中规则默认允许，严格环境建议先建立“默认拒绝”兜底规则，再添加更低 rank 的精确允许规则。
- 修改已有策略的 Rank 可能改变大量调用者的匹配结果；修改前先列出受影响的平台、用户和 Agent。
- 删除策略后可能暴露默认允许行为；删除前确认仍有正确的兜底规则。

## 5. User Management（用户管理）

| 项目 | 内容 |
| --- | --- |
| 面向人群 | 用户生命周期管理员、平台管理员 |
| 功能 | 创建用户、启用/停用账号、重置密码、查看最后登录时间、删除非管理员用户 |
| 功能路径 | **CONTROL → User Management（用户管理）** |

### 5.1 查看与刷新

1. 进入 **User Management**。
2. 查看用户名、邮箱、状态和最后登录时间。
3. 选择 **Refresh** 重新读取列表。

### 5.2 新增用户

1. 选择 **新增用户**。
2. 填写用户名、初始密码、邮箱和初始状态。
3. 选择 **Create User**。
4. 将初始密码通过组织认可的安全渠道交付给用户，并要求其首次登录后修改密码。

### 5.3 启用、停用、重置和删除

**启用/停用**

1. 在目标用户行选择 `Enable <username>` 或 `Disable <username>`。
2. 阅读确认对话框后确认。
3. 刷新并确认状态已变化。

**重置密码**

1. 选择 `Reset password <username>`。
2. 输入新密码。
3. 选择 **Save Password**。
4. 安全通知用户，并要求其登录后修改密码。

**删除用户**

1. 确认该账号无保留需要，并提前导出相关审计信息。
2. 选择 `Delete <username>`。
3. 在不可逆确认对话框中确认。

**注意事项**

- 当前界面不提供删除管理员账号的操作；管理员账号应按组织的交接流程管理。
- 停用优先于删除：停用能保留用户记录和审计关联，适合离岗、调查或暂时冻结场景。
- 重置密码不会替代身份核验；应先按组织流程确认请求人身份。
- 新注册账号默认停用，管理员必须明确启用后用户才能登录。

## 6. System Settings（系统设置）

| 项目 | 内容 |
| --- | --- |
| 面向人群 | 平台管理员、值班运维人员 |
| 功能 | 查看 Aegis 运行健康状态与进程 ID；发起受保护的 Aegis 服务重启 |
| 功能路径 | **CONTROL → System Settings（系统设置）**，或右上角设置图标 |

### 6.1 查看运行状态

1. 进入 **System Settings**。
2. 查看 **Runtime Status**：Service、Health 和 Process ID。
3. `Health: ok` 表示健康端点可访问；`Unavailable` 表示浏览器暂时无法访问健康端点。

**注意事项**

- Health 仅表示 Aegis 进程健康端点可访问，不保证所有远程 A2A Agent 可用。
- 应结合 Chat Agents 悬浮窗、Audit Logs 和基础设施监控判断故障范围。

### 6.2 重启 Aegis

**使用方法**

1. 确认没有需要保留的进行中操作；提前通知受影响用户。
2. 在 **Dangerous Actions** 选择 **Restart Aegis**。
3. 第一层确认中阅读“服务中断”提示，选择 **Continue**。
4. 在第二层确认中准确输入：

```text
RESTART AEGIS
```

5. 选择 **Confirm restart**。
6. 等待页面监测到进程替换并自动刷新；重新登录或检查会话状态。

**注意事项**

- 重启会暂时断开控制台，并可能中断活跃聊天、远程委派和正在等待的交互。
- 不要以刷新浏览器代替正常重启，也不要连续发起多次重启请求。
- 若重启失败，页面会显示错误；此时保留时间、进程 ID、错误信息并按运维流程处理。

## 7. Audit Logs（审计日志）

| 项目 | 内容 |
| --- | --- |
| 面向人群 | 安全审计员、值班管理员、A2A 平台管理员 |
| 功能 | 查询 A2A 委派授权与执行结果，按条件筛选、展开目标、翻页和复核异常 |
| 功能路径 | **CONTROL → Audit Logs（审计日志）** |

### 7.1 日志字段说明

| 字段 | 说明 |
| --- | --- |
| Timestamp / Audit ID | UTC 时间与审计记录唯一标识 |
| Caller | 来源平台、用户名和用户 ID |
| Agent | 被委派的目标 Agent ID |
| Goal | 委派目标；选择后可展开全文 |
| Session | 关联会话 ID |
| Options | 是否 loop 模式、是否输出委派结果 |
| Status | `succ`、`fail` 或 `auth_denied` |

### 7.2 查询和复核方法

1. 进入 **Audit Logs**。
2. 按需填写一个或多个筛选条件：Audit ID、Platform、User ID、User Name、Agent Name、Goal、Session ID、状态、循环/输出选项、UTC 起止时间。
3. 选择 **Search**。
4. 选择某条 Goal 可展开完整内容。
5. 使用底部分页按钮浏览更多结果；选择 **Reset** 清除筛选；选择 **Refresh** 重新读取当前筛选下的数据。

**常见排查路径**

| 现象 | 建议筛选 | 后续动作 |
| --- | --- | --- |
| 用户称无法委派 | User ID、Agent Name、`auth_denied` | 检查 Agent Policy 的最小 rank 命中项 |
| 远程任务失败 | Agent Name、`fail`、时间范围 | 检查远端服务、认证 Header、Agent Card 与网络连通性 |
| 需要追溯一次会话 | Session ID | 导出/记录相关时间段全部记录，关联 Chat 事件核对 |
| 成功率下降 | Agent Name、时间范围 | 对比 `succ` / `fail`，检查最近 Agent 或策略变更 |

**注意事项**

- 审计记录中的 Goal、用户标识和会话信息可能含敏感信息；导出、截图和共享必须遵守数据分级要求。
- `auth_denied` 表示策略拒绝，不等同于远程 Agent 技术故障。
- 时间筛选使用 UTC；跨时区调查时务必换算后再比对外部系统日志。

## 8. 管理员变更后检查清单

- [ ] Agent 地址、Card、认证 Header 和状态均已验证；
- [ ] 新 Agent 先以最小权限策略进行受控测试；
- [ ] Agent Policy 的 Rank 无冲突，且默认允许/拒绝行为符合组织要求；
- [ ] Global Routing Rules 文字无冲突，且未被误当作访问控制；
- [ ] 用户创建、停用、密码重置均有身份核验和变更记录；
- [ ] 服务重启前已评估活动任务与用户影响；
- [ ] Audit Logs 已复核最近配置变更后的 `succ`、`fail` 与 `auth_denied`；
- [ ] 管理员完成操作后退出共享终端或锁定设备。
