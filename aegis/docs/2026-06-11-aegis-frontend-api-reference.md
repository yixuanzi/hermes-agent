# Aegis 前端模块 Review 与后端 API 设计参考

## 1. 文档目标

本文基于 `aegis/frontend` 当前高保真原型，对 Aegis 模块的前端设计、功能模块和数据依赖进行梳理，并按模块拆解出后端 API 需求，供后端设计、接口联调和后续前端改造使用。

本文覆盖范围：

- `Overview`
- `Aegis Chat`
- `Agent Orchestration`
- `Routing Policy`
- 页头与侧边栏中的公共状态区
- 侧边栏中已露出但尚未实现的 `System Settings`、`Audit Logs` 预留模块

不覆盖范围：

- 具体 AI 推理链路内部算法
- 底层安全产品适配器实现
- 数据库选型和消息总线选型

## 2. 前端 Review 结论

### 2.1 设计优点

- 整体视觉方向明确，采用安全控制台风格，品牌感和“中控枢纽”定位一致。
- 4 个核心模块的信息架构清晰，符合“总览 - 对话 - 编排 - 策略”的主流程。
- 前端状态模型已经初步收敛到 `Agent`、`RoutingRule`、`Conversation`、`Message`、`ChainStep` 五个核心实体，适合直接映射到后端领域模型。
- CRUD 行为和过滤行为已经完整具备，说明前端对后端最小能力边界已经相对明确。

### 2.2 当前原型与真实系统之间的差距

- 所有数据目前由 mock 数据和 `localStorage` 驱动，尚未接入真实鉴权、服务端持久化、实时订阅和审计。
- `Overview` 中拓扑星图、任务趋势、告警数量、安全评分等核心指标大多仍是写死或半写死状态，尚未从统一后端视图投影出来。
- `Chat` 页的“意图识别”和“编排链路”是前端关键词模拟，不是真实的 Agent 路由与执行回放。
- `Agent` 的认证头名称和值直接和普通业务字段放在同一前端表单中，未来接真实后端时需要拆分“展示字段”和“敏感凭据”。
- `Policy` 页中的规则条件当前是自由文本，后端需要额外承担规则校验、版本化、模拟执行和冲突检测能力。

### 2.3 需要优先修正的建模问题

- `Overview` 星图节点与 `Agent Register` 列表并非完全同源，后端必须提供统一拓扑模型，避免前端各处各自维护一套节点定义。
- `tasksCount`、`lastUpdated`、状态灯等运行态字段不能继续视为静态配置字段，建议拆为“注册信息”和“运行时状态”两部分。
- `Conversation` 与 `ChainStep` 已经隐含出“会话”和“运行实例”两个概念，后端不应只建消息表，还应建任务运行表或执行轨迹表。

## 3. 模块与后端域映射

| 前端模块 | 前端职责 | 推荐后端域 |
| --- | --- | --- |
| Overview | 总览指标、拓扑、Agent 快速索引 | `dashboard`、`topology`、`agents`、`alerts` |
| Aegis Chat | 会话管理、消息流、执行链路回放 | `conversations`、`messages`、`runs`、`attachments` |
| Agent Orchestration | Agent/VIP Tool 注册与维护 | `agents`、`agent_runtime`、`credentials` |
| Routing Policy | 路由规则维护与启停 | `routing_policies`、`policy_validation` |
| Header / Sidebar | 当前用户、系统联通状态、通知 | `session`、`system_status`、`notifications` |
| Admin 预留模块 | 系统设置、审计日志 | `settings`、`audit_logs` |

## 4. API 设计总则

### 4.1 建议规范

- API 前缀：`/api/v1`
- 时间字段统一使用 ISO 8601 UTC，例如 `2026-06-11T07:42:15Z`
- 列表接口统一支持 `page`、`page_size`、`sort_by`、`sort_order`
- 检索接口统一支持 `q`
- 状态字段建议后端统一使用小写枚举，例如 `active`、`idle`、`offline`、`enabled`、`disabled`，由前端负责展示层文案映射
- 启停类操作优先使用显式状态变更接口，不建议完全依赖“toggle”
- 前端实时状态优先通过 `SSE` 或 `WebSocket` 提供，不建议前端高频轮询

### 4.2 通用响应格式

```json
{
  "request_id": "req_01JY7Y2M1Y2N5N8G4F3R1F2ABC",
  "data": {},
  "error": null,
  "meta": {
    "page": 1,
    "page_size": 20,
    "total": 125
  }
}
```

### 4.3 通用错误格式

```json
{
  "request_id": "req_01JY7Y2M1Y2N5N8G4F3R1F2ABC",
  "data": null,
  "error": {
    "code": "POLICY_EXPRESSION_INVALID",
    "message": "Rule condition syntax is invalid.",
    "details": {
      "field": "condition",
      "position": 18
    }
  }
}
```

### 4.4 鉴权建议

- 控制台 API 使用 Bearer Token 或企业统一身份认证网关。
- 对敏感操作增加 RBAC，例如：
  - `aegis.viewer`
  - `aegis.operator`
  - `aegis.admin`
- 涉及 Agent 密钥、凭据和策略变更的接口建议记录操作者、来源 IP、审批上下文和变更前后值摘要。

## 5. 模块 API 需求

### 5.1 公共状态区

对应前端区域：

- 页头用户信息
- `LIVE_SYNC: CONNECTED`
- UTC 时间展示
- 通知按钮
- 设置按钮

### 必需接口

#### `GET /api/v1/session/me`

用途：获取当前登录用户、角色和租户信息。

返回重点字段：

- `user_id`
- `display_name`
- `role_name`
- `permissions`
- `tenant_id`

#### `GET /api/v1/system/status`

用途：提供控制台页头状态，包括协调器联通状态、A2A 网关状态、规则引擎状态、时间基准。

返回重点字段：

- `coordinator_status`
- `a2a_gateway_status`
- `policy_engine_status`
- `server_time_utc`
- `unread_notifications`

#### `GET /api/v1/notifications`

用途：提供通知列表和未读计数。

说明：前端当前只有按钮和红点，可先只做轻量版未读计数，再补通知详情。

### 5.2 Overview 模块

对应前端能力：

- 顶部总览 Banner
- 4 张指标卡
- 星图拓扑
- 节点详情浮层
- 右侧 `Agent Index` 搜索和筛选

### 必需接口

#### `GET /api/v1/dashboard/summary`

用途：返回总览页顶部和 4 张指标卡需要的聚合数据。

建议返回：

```json
{
  "coordinator_state": "root_active",
  "channel": "A2A_RPC",
  "agents": {
    "total": 15,
    "active": 13,
    "idle": 0,
    "offline": 2
  },
  "tasks": {
    "in_progress": 37,
    "trend": [23, 29, 21, 35, 27, 41, 31, 37]
  },
  "alerts": {
    "unchecked_24h": 8,
    "risk_level": "high",
    "headline": "Critical S3 breach vector mapped"
  },
  "security_posture": {
    "score": 92,
    "grade": "excellent",
    "protected_assets": 1284
  }
}
```

#### `GET /api/overview/topology`

用途：返回总览三层星图的中心、Agent 外环、群星节点及边，不再由前端写死。该接口需要认证；Agent 的 `runtime` 状态会叠加当前 A2A 注册表数据，未注册的设计基线 Agent 返回 `planned`。

响应字段：

- `schema_version`、`updated_at`
- `center`：Aegis 中心节点、连接符 `symbol`、职责和布局
- `agents[]`：7 个业务域 Agent、神话 `symbol`、业务语义、`layout`、`runtime` 与 `star_nodes[]`
- `agents[].star_nodes[]`：Tool、API、Data 节点的接入方式、用途和必需性
- `edges[]`：`orchestrates`（Aegis → Agent）与 `requires`（Agent → 群星）边

#### `GET /api/v1/agents`

用途：支撑右侧 `Agent Index`、搜索和状态筛选。

建议参数：

- `q`
- `status=Active|Idle|Offline`
- `type=agent|vip_tool`
- `page`
- `page_size`

#### `GET /api/v1/agents/{agent_id}`

用途：点击星图节点或列表条目后展示详情浮层。

说明：该接口应返回配置态和运行态的合并视图。

### 推荐补充接口

#### `GET /api/v1/dashboard/events`

用途：提供总览页最近告警、任务和策略命中事件，未来可支撑更丰富的 live feed。

#### `GET /api/v1/dashboard/stream`

用途：通过 SSE 推送总览核心指标变化，例如：

- Agent 上下线
- 新增高危告警
- posture score 变化
- 队列任务数变化

### 5.3 Aegis Chat 模块

对应前端能力：

- 会话列表
- 新建会话
- 删除会话
- 清空历史
- 发送消息
- 展示 Aegis 回复
- 展示编排链路
- 预设场景入口

### 必需接口

#### `GET /api/v1/chat/presets`

用途：返回首页空态时展示的预设场景卡片。

说明：如果后续不需要预设场景，也可由前端配置化；但如果希望按租户或行业展示不同模板，建议后端提供。

#### `GET /api/v1/conversations`

用途：获取会话列表。

建议返回：

- `id`
- `title`
- `last_message_at`
- `message_count`
- `status`

#### `POST /api/v1/conversations`

用途：创建新会话。

请求示例：

```json
{
  "title": "New Investigation"
}
```

#### `GET /api/v1/conversations/{conversation_id}`

用途：获取单个会话详情。

#### `GET /api/v1/conversations/{conversation_id}/messages`

用途：分页获取消息记录。

建议参数：

- `before`
- `after`
- `limit`

#### `POST /api/v1/conversations/{conversation_id}/messages`

用途：发送用户消息，并触发一次真实的编排执行。

请求示例：

```json
{
  "content": "帮我分析最近钓鱼邮件攻击的趋势和来源分布，并给出处置建议。",
  "attachments": [],
  "context": {
    "source": "aegis_web_console"
  }
}
```

建议同步返回：

- `message_id`
- `run_id`
- `conversation_id`
- `status=accepted`

#### `GET /api/v1/runs/{run_id}`

用途：查询一次对话执行实例的当前状态。

建议返回：

- `run_id`
- `conversation_id`
- `status`
- `intent`
- `target_agents`
- `started_at`
- `finished_at`

#### `GET /api/v1/runs/{run_id}/steps`

用途：获取编排链路步骤。

建议返回字段：

- `step_id`
- `name`
- `type`
- `status`
- `message`
- `started_at`
- `finished_at`
- `tool_ref`

### 强烈建议使用流式接口

#### `GET /api/v1/runs/{run_id}/stream`

用途：使用 `SSE` 向前端实时推送执行过程。

事件建议：

- `run.accepted`
- `run.routed`
- `step.started`
- `step.completed`
- `step.failed`
- `message.delta`
- `message.completed`
- `run.completed`

前端现有“Live Decisioning Paths”和最终回答展示非常适合直接映射为流式事件消费。

### 推荐补充接口

#### `DELETE /api/v1/conversations/{conversation_id}`

用途：删除单个会话。

#### `DELETE /api/v1/conversations`

用途：清空当前用户会话历史。

#### `POST /api/v1/attachments`

用途：上传附件、IOC 文件、日志片段或样本摘要。

说明：前端当前已有附件按钮，但尚未接入任何行为，这个接口建议作为第二阶段能力。

### 5.4 Agent Orchestration 模块

对应前端能力：

- Agent/VIP Tool 列表
- 搜索与状态筛选
- 注册新 Agent
- 编辑 Agent
- 删除 Agent
- 统计总数、活跃数、空闲数、离线数

### 建议的数据模型拆分

前端表单当前把“静态注册信息”和“运行时状态”混在了一起，后端建议拆成：

- `agent_profile`
- `agent_runtime`
- `agent_credential_ref`

### 必需接口

#### `GET /api/v1/agents`

用途：列表、搜索、筛选。

建议额外支持：

- `include_runtime=true`
- `include_credentials=false`

#### `POST /api/v1/agents`

用途：注册新 Agent 或 VIP Tool。

请求示例：

```json
{
  "name": "Threat Intel Agent",
  "type": "agent",
  "description": "情报分析与威胁源追溯评估",
  "capability_summary": "提取威胁指标并评定威胁等级。",
  "connection": {
    "protocol": "a2a",
    "endpoint": "a2a://threat-intel.aegis.local",
    "auth_scheme": "header",
    "auth_header_name": "Authorization",
    "credential_ref": "cred_01JY..."
  },
  "desired_state": "active"
}
```

#### `PATCH /api/v1/agents/{agent_id}`

用途：编辑 Agent 元信息、连接方式和预期状态。

#### `DELETE /api/v1/agents/{agent_id}`

用途：删除 Agent。

说明：建议增加后端保护，若已有策略规则引用该 Agent，应返回引用冲突信息，而不是静默删除。

#### `GET /api/v1/agents/{agent_id}/runtime`

用途：获取运行态，如：

- 当前任务数
- 最近心跳时间
- 平均响应耗时
- 最近失败原因
- 可用性状态

#### `POST /api/v1/agents/{agent_id}/connectivity-test`

用途：在创建或编辑后主动探测 Agent 连接是否可用。

### 敏感字段建议

不要让列表接口直接返回 `auth_header_value` 明文。建议改为：

- 前端提交密钥到凭据接口
- 主业务对象只保存 `credential_ref`
- 列表返回 `credential_bound=true`

### 推荐补充接口

#### `POST /api/v1/credentials`

用途：安全保存 Agent 的认证值、密钥或令牌。

#### `GET /api/v1/agents/stats`

用途：单独提供统计卡片数据。

说明：如果前端后续继续保留大表格和统计卡片并存，这个接口能避免每次列表查询都扫描全量数据。

### 5.5 Routing Policy 模块

对应前端能力：

- Agent 路由规则列表
- Global 路由规则列表
- 搜索规则
- 新建规则
- 编辑规则
- 删除规则
- 启用/停用规则

### 当前设计特点

- 前端已经区分 `agent` 规则和 `global` 规则
- 规则内容当前为自由文本
- 目标 Agent 可以是具体 Agent，也可以是 `all`

### 必需接口

#### `GET /api/v1/routing-policies`

用途：获取规则列表。

建议参数：

- `scope=agent|global`
- `status=enabled|disabled`
- `q`
- `agent_id`

#### `POST /api/v1/routing-policies`

用途：创建规则。

请求示例：

```json
{
  "name": "钓鱼邮件深度分析",
  "scope": "agent",
  "target_agent_id": "email-sec",
  "condition": {
    "expression": "category == 'phishing'"
  },
  "action": {
    "type": "route",
    "value": "email-sec"
  },
  "priority": 30,
  "status": "enabled"
}
```

#### `PATCH /api/v1/routing-policies/{policy_id}`

用途：编辑规则。

#### `DELETE /api/v1/routing-policies/{policy_id}`

用途：删除规则。

#### `POST /api/v1/routing-policies/{policy_id}/status`

用途：显式启停规则。

请求示例：

```json
{
  "status": "disabled"
}
```

### 强烈建议补充的治理接口

#### `POST /api/v1/routing-policies/validate`

用途：在保存前校验规则语法、目标 Agent 是否存在、是否与高优先级规则冲突。

#### `POST /api/v1/routing-policies/simulate`

用途：输入一段请求样本、告警字段或结构化事件，返回会命中哪些规则。

说明：这对前端策略页非常重要，否则规则只能“写进去”，不能“验证它会怎么路由”。

#### `GET /api/v1/routing-policies/{policy_id}/references`

用途：查看一条规则被哪些会话、运行实例或审计事件命中过。

### 5.6 Settings 与 Audit Logs 预留模块

前端侧边栏已经露出了这两个模块入口，但还未实现页面。

建议后端提前预留以下领域：

### `GET /api/v1/settings`

用途：获取控制台级配置，例如默认路由策略、聊天保留天数、审计保留天数、通知开关。

### `PATCH /api/v1/settings`

用途：更新系统配置。

### `GET /api/v1/audit-logs`

用途：查询关键操作留痕。

建议支持检索维度：

- `actor`
- `resource_type`
- `resource_id`
- `action`
- `time_from`
- `time_to`

### `GET /api/v1/audit-logs/{log_id}`

用途：查看单条操作详情，包括变更前后值摘要。

## 6. 核心对象参考模型

### 6.1 Agent

```json
{
  "id": "agent_threat_intel",
  "name": "Threat Intel Agent",
  "type": "agent",
  "description": "情报分析与威胁源追溯评估",
  "capability_summary": "提取威胁指标并评定威胁等级。",
  "desired_state": "active",
  "runtime": {
    "status": "active",
    "active_tasks": 5,
    "queued_tasks": 2,
    "last_heartbeat_at": "2026-06-11T07:42:15Z",
    "latency_ms_p50": 210
  },
  "connection": {
    "protocol": "a2a",
    "endpoint": "a2a://threat-intel.aegis.local",
    "auth_scheme": "header",
    "auth_header_name": "Authorization",
    "credential_bound": true
  }
}
```

### 6.2 Routing Policy

```json
{
  "id": "policy_critical_vuln",
  "name": "高危漏洞优先处理",
  "scope": "agent",
  "target_agent_id": "vul-mgmt",
  "priority": 10,
  "condition": {
    "expression": "severity == 'critical'"
  },
  "action": {
    "type": "route",
    "value": "vul-mgmt"
  },
  "status": "enabled",
  "updated_at": "2026-06-11T07:42:15Z",
  "updated_by": "amber_soc_admin"
}
```

### 6.3 Conversation

```json
{
  "id": "conv_01JY...",
  "title": "钓鱼邮件分析与来源分布",
  "status": "active",
  "last_message_at": "2026-06-11T07:42:15Z",
  "message_count": 4
}
```

### 6.4 Run Step

```json
{
  "step_id": "step_01JY...",
  "run_id": "run_01JY...",
  "name": "Threat Intel Agent",
  "type": "agent",
  "status": "completed",
  "message": "对 IOC 和样本哈希进行全球情报比对。",
  "started_at": "2026-06-11T07:42:20Z",
  "finished_at": "2026-06-11T07:42:24Z"
}
```

## 7. 前端到后端的落地优先级

### Phase 1：先替换本地存储

- `session/me`
- `system/status`
- `agents` CRUD
- `routing-policies` CRUD
- `conversations` CRUD
- `messages` 查询与发送

目标：把 `localStorage` 替换为真实服务端持久化。

### Phase 2：补实时与运行态

- `dashboard/summary`
- `topology`
- `agents/{id}/runtime`
- `runs`
- `runs/{id}/steps`
- `runs/{id}/stream`

目标：把“模拟编排”替换为真实的事件流和执行链路。

### Phase 3：补治理能力

- `routing-policies/validate`
- `routing-policies/simulate`
- `audit-logs`
- `settings`
- `attachments`
- `notifications`

目标：把 Aegis 从“可演示原型”推进到“可治理平台”。

## 8. 结论

从当前前端原型看，Aegis 的产品形态已经足够清楚，后端最核心的不是“先把所有安全能力都接进去”，而是先围绕以下 5 个统一对象建模：

- `Agent`
- `RoutingPolicy`
- `Conversation`
- `Run`
- `DashboardSummary`

只要这 5 个对象和它们之间的关系先稳定下来，前端当前 4 个核心模块就可以较顺畅地从 mock 迁移到真实 API；反之，如果后端仍然按“页面临时字段”来拼接口，后续一旦进入实时编排、审计留痕和多 Agent 协同，就会很快出现模型分裂问题。
