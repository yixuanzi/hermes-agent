# AISOC Backend (FastAPI)

## 1. 模块定位
AISOC Backend 是 `hermes aisoc` 的服务层，目前仅提供 `server` 模块：
- `server`：当前 Web Console / FastAPI API / 统一聊天 WebSocket 服务

`server` 模块当前负责：
- 统一认证（用户账号 + JWT）
- 会话、Cron、Skill、Memory、Logs、Overview 数据读取/写入
- 统一聊天模块（`/api/chat/session` WebSocket + quick commands + drawer 预览 + 附件）
- 托管前端静态资源（`web_dist`）

当前统一入口：`aisoc/backend/main.py`
- `hermes aisoc ...` 会复用这份入口逻辑
- 也可以直接运行 `python aisoc/backend/main.py ...`
- 直接以 Python 启动时额外支持 `-p/--profile`，用于显式指定 Hermes profile；`hermes -p <profile> aisoc ...` 仍保持原有逻辑不变

---

## 2. 开发与启动

### 2.1 推荐启动方式（集成前后端）
在仓库根目录执行：

```bash
hermes aisoc --module server --port 9120
```

常用参数：
- `--module server`：保留的服务模块入口，默认 `server`
- `--port 9120`：服务端口（默认 9120）
- `--host 127.0.0.1`：监听地址（默认 loopback）
- `--no-open`：不自动打开浏览器
- `--insecure`：允许非 loopback 绑定（有安全风险）
- `--skip-build`：跳过前端构建（需已有 `backend/web_dist`）

也支持直接以 Python 启动同一入口：

```bash
python aisoc/backend/main.py -p myprofile --module server --port 9120
```

说明：
- `-p/--profile` 仅用于 `python aisoc/backend/main.py ...` 这类 direct startup 场景
- `hermes -p <profile> aisoc ...` 继续由 Hermes 主入口负责 profile 解析，不需要额外改写参数

AISOC 聊天 agent 会按 Hermes 原生语义加载 `config.yaml` 中启用的 `mcp_servers`。可通过环境变量 `AISOC_MCP_ACTIVE` 覆盖该行为：未设置时自动加载，`true/1/yes/on` 强制启用，`false/0/no/off` 禁用。

### 2.2 直接以 Python 启动（调试后端）

```bash
python aisoc/backend/main.py -p myprofile --module server --host 127.0.0.1 --port 9120 --no-open
```

或只调后端 server 模块：

```bash
python -c "from aisoc.backend.server import start_server; start_server(host='127.0.0.1', port=9120, open_browser=False)"
```

### 2.3 认证配置
- `AISOC_BOOTSTRAP_ADMIN_PASSWORD`：首次启动播种初始 `admin` 账号所需（≥8 位）；未设置时不会创建 admin，`/api/system/bootstrap` 返回 `admin_setup_required: true`，任何账号均无法登录
- `AISOC_JWT_SECRET`：若设置，则 JWT 使用该固定密钥签发/校验；未设置时进程启动自动生成随机密钥（不落盘）——**重启且未设置该变量会使所有已登录会话失效**
- `AISOC_MCP_ACTIVE`：AISOC 聊天 agent 的 MCP 装载开关；默认跟随配置自动加载，设置为 `false/0/no/off` 可在启动时关闭 MCP
- `AISOC_OIDC_ISSUER` / `AISOC_OIDC_CLIENT_ID` / `AISOC_OIDC_CLIENT_SECRET` / `AISOC_OIDC_REDIRECT_URI` / `AISOC_OIDC_BACKCHANNEL_URL` / `AISOC_OIDC_POST_LOGIN_REDIRECT`：OIDC 单点登录（`/api/sso/*`）；未配置 client 时 `/api/sso/start` 返回 503

---

## 3. 架构与设计

### 3.1 分层架构
- `server.py`
  - FastAPI app 组装
  - CORS + 认证中间件
  - 路由注册 + Swagger Bearer 认证注入
  - SPA 静态文件与 fallback
- `routes/*.py`
  - API 路由层（参数校验、状态码转换）
- `services/*.py`
  - 业务编排层（调用 Hermes 现有能力）
- `models.py`
  - Pydantic 请求/响应模型
- `auth.py` + `config.py`
  - 密码哈希、JWT 签发/校验、配置装配

### 3.2 认证机制
- 独立于 aegis 的用户账号体系：sqlite `users` 表（`$HERMES_HOME/aisoc.db`），PBKDF2-HMAC-SHA256 密码哈希，JWT（HS256）访问令牌
- 角色模型极简：仅一个 `is_admin` 布尔位，硬编码 `username == "admin"` 为超级管理员，无多角色/权限表
- 所有 `/api/*` 默认受保护
- 白名单（无需认证）：
  - `/api/auth/login`
  - `/api/auth/register`
  - `/api/auth/session`
  - `/api/auth/logout`
  - `/api/sso/start`、`/api/sso/callback`、`/api/sso/exchange`
  - `/api/system/bootstrap`
  - `/health`
- HTTP：`Authorization: Bearer <JWT>`（`/api/auth/login` 或 `/api/sso/exchange` 换取）
- WebSocket：`?token=<JWT>`（浏览器 WS 升级不便带自定义 Authorization）
- 令牌吊销（HTTP 与 WS 建连时同样校验）：
  - JWT 携带 `ver`（用户的 `token_version`）与 `jti`；修改自己密码、管理员重置密码、禁用账号都会递增 `token_version`，此前签发的令牌全部失效（重新启用账号也不会复活）
  - `POST /api/auth/logout` 只吊销所携带的这一个令牌（`jti` 记入 `aisoc.db` 的 `revoked_tokens`，过期后清理），同一用户的其他会话不受影响；不带 `jti` 的旧令牌登出时只能递增 `token_version`，即注销该用户全部会话
  - 不带 `ver` 的令牌按版本 0 处理
- 自助注册（`POST /api/auth/register`）默认落地 `status=disabled`，需管理员在 `/api/users` 启用后才能登录

### 3.3 统一聊天链路设计（1.0）
聊天核心在 `aisoc/backend/chat/`（自 aegis chat 模块回迁；已恢复用户账号绑定，会话按认证用户的 `user_id` 隔离）：
- `WS /api/chat/session`：唯一聊天通道。客户端事件 `session.bind` / `message.send` /
  `approval.respond` / `clarify.respond` / `session.interrupt` / `session.resume`；
  服务端事件 envelope（`message.delta/completed`、`run.state`、`tool.*`、
  `delegate.*`、`approval.*`、`clarify.*`、`error`，含 `server_event_id` 去重）。
  - 非 JSON 文本帧或二进制帧返回 `error`（`code: invalid_message`），连接继续可用；连接以任何方式结束都会释放其绑定的会话。
  - 会话忙（运行中 / 等待审批 / 等待澄清）时，`message.send` 直接返回 `error`（带 `client_msg_id`），不会先发 `message.accepted`。
  - 冷启动 `session.bind`（构建 agent）与消息处理在线程池中执行，不阻塞其他请求。
- `message.send` 文本先经 `QuickCommandService.resolve_text` 做 `@[type_name]`
  快捷指令服务端展开与 `{var}` 参数替换（seed 在 `services/quick_command_seeds.py`，
  可用 `$HERMES_HOME/aisoc_quick_commands.json` 扩展/覆盖）。
- `GET /api/chat/quick-commands`：composer 快捷指令清单。
- `GET /api/chat/drawer-html?path=...`：文件预览（A2UI HTML 产物等），只返回当前用户自己的 agent 回合
  （`write_file`/`patch`）写入过的文件（不论位于何处，agent 的文件工具按终端 cwd 解析相对路径），
  且仍受核心读保护约束；其他路径一律按不存在返回 404，相对路径按 `HERMES_HOME` 解析。
- `POST /api/chat/attachments`：聊天附件上传。
- `/agent2ui/*`：Agent2UI bridge 与模板静态资源（匿名，供 sandboxed 预览 iframe 加载）。
- 会话即 `hermes_state.SessionDB` 会话（`platform=aisoc_web`），`/api/sessions?source=aisoc_web`
  可过滤出 Web 聊天会话。

旧 PTY/TUI 链路（`/api/chat/pty|ws|pub|events`、`--tui`、`services/tui_embed.py`）已于 1.0 移除。

---

## 4. API 模块清单

### 4.1 Auth
前缀：`/api/auth`
- `POST /login`
- `POST /register`（自助注册，落地 `status=disabled`）
- `GET /session`
- `POST /logout`（吊销所携带的令牌，见 3.2）
- `PUT /password`（成功后此前签发的令牌失效，需要重新登录）
- `PUT /profile`（修改 `display_name`）

### 4.1a Users（仅管理员）
前缀：`/api/users`
- `GET /`
- `POST /`
- `PUT /{uid}/status`
- `PUT /{uid}/password`
- `DELETE /{uid}`

### 4.2 System
- `GET /health`
- `GET /api/system/bootstrap`（含 `admin_setup_required`）
- `POST /api/system/restart`（仅管理员）

### 4.3 Chat
前缀：`/api/chat`
- `WS /session`
- `GET /quick-commands`
- `GET /drawer-html`
- `POST /attachments`

### 4.4 Sessions
前缀：`/api/sessions`
- `GET /`
- `GET /search`
- `GET /{session_id}`
- `GET /{session_id}/detail`
- `GET /{session_id}/latest-descendant`
- `GET /{session_id}/messages`
- `DELETE /{session_id}`

### 4.5 Cron
前缀：`/api/cron`
- `GET /jobs`
- `GET /jobs/{job_id}`
- `GET /jobs/{job_id}/history`
- `POST /jobs`
- `PUT /jobs/{job_id}`
- `PUT /jobs/{job_id}/raw`
- `POST /jobs/{job_id}/pause`
- `POST /jobs/{job_id}/resume`
- `POST /jobs/{job_id}/trigger`
- `DELETE /jobs/{job_id}`

非法输入（错误的 schedule、修改 job id、非法状态迁移等）返回 400。

### 4.6 Skills
前缀：`/api/skills`
- `GET /` （skill item 含 `path` 字段）
- `GET /{skill_name}` （返回 `content` + `appendix[]`）
- `GET /{skill_name}/appendix?path={path}` （返回附件文本内容）
- `PUT /toggle`
- `POST /reload`

### 4.7 Memory
前缀：`/api/memory`
- `GET /`
- `GET/PUT /soul`
- `GET/PUT /user`
- `GET/PUT /files/{name}`

### 4.8 Logs
前缀：`/api/logs`
- `GET /?file=agent&lines=100&level=&component=&search=`（`lines` 必须 ≥ 1，否则 422；上限 500）

### 4.9 Knowledge Base
前缀：`/api/kb`
- `GET /tree?cwd=` （列出指定目录下的文件和文件夹，`cwd` 为空时列出根目录）
- `GET /documents?path=` （返回指定文件的文本内容）

环境变量：`AISOC_WIKI_PATH` 指定知识库根目录，未设置或路径不存在时返回 503。

安全限制：
- 禁止路径遍历（`..`）和符号链接逃逸；`/tree` 跳过目标不存在或指向知识库外的符号链接（逐项跳过，不影响整个列表）
- 文件大小上限 2 MB（超出返回 413）
- 非 UTF-8 文本文件返回 415

### 4.10 Overview
前缀：`/api/overview`
- `GET /status`
- `GET /stats`
- `GET /token-trend`
- `GET /security-events`
- `GET /keywords`
- `GET /keywords/{keyword}/sessions`
- `GET /cron-token-dist`
- `GET /cronjobs`
- `GET /model-usage?period=today|7d|30d`

`stats`、`token-trend`、`cron-token-dist`、`model-usage` 是全局聚合；`security-events`、`keywords`、
`keywords/{keyword}/sessions` 返回会话内容，只包含当前用户自己的会话。

说明（2026-05 更新）：
- 原 `GET /api/overview/cronjobs/{job_id}/history` 已迁移到 `GET /api/cron/jobs/{job_id}/history`
- 原 `GET /api/overview/sessions/{session_id}/detail` 已迁移到 `GET /api/sessions/{session_id}/detail`
- Overview 仅保留总览/聚合接口；明细下钻接口归属到对应业务模块（Cron / Sessions）

### 4.11 MCP Servers
前缀：`/api/mcp-servers`（读写 `config.yaml` 的 `mcp_servers`，影响所有用户的 agent）
- `GET /`、`GET /{name}`：所有用户可读；非管理员看到的 URL 只保留 scheme+host（路径/查询替换为 `***`），`args`/`env` 值替换为 `***`
- `POST /`、`PUT /{name}`、`DELETE /{name}`、`PUT /{name}/enabled`、`PUT /{name}/tools`、`POST /{name}/test`、`POST /reload`：仅管理员（否则 403）
- `PUT /{name}` 只覆盖表单字段（url/command/args/env/Authorization 头），其他键（工具过滤、超时等）保留；未传 `enabled` 时保持原状态

### 4.12 Skill Hub
前缀：`/api/skill-hub`（租户 SkillHub，配置项 `skill_hub.url` / `skill_hub.api_key`）
- `GET /status`
- `GET /catalog?search=`
- `GET /skills`
- `POST /skills`（安装；仅在安装成功后才把 `~/.agents/skills` 加入 `skills.external_dirs`）
- `POST /skills/{name}/sync`（未安装返回 404）
- `DELETE /skills/{name}`（未安装返回 404）

### 4.13 Ontology
前缀：`/api/ontology`（产物默认位于 `$HERMES_HOME/artifacts/ontology/`，技能脚本位于 `$HERMES_HOME/skills/ontology/scripts`，可用 `AISOC_ONTOLOGY_*` 环境变量覆盖；读取接口不会创建目录；AI 判定子进程继承当前 `HERMES_HOME`，始终以 server 所在 profile 运行；`AISOC_ONTOLOGY_PROFILE` 是扫描脚本使用的 profile 目录路径，不是 profile 名）
- `POST /compile`
- `POST /scan`（异步 job）
- `GET /scan/active`
- `GET /scan/{job_id}`
- `GET /overview`
- `GET /artifacts/{name}`
- `GET /roadmap`

### 4.14 SSO（OIDC）
前缀：`/api/sso`（白名单）
- `GET /start`：跳转到 IdP
- `GET /callback`：IdP 回调，写入一次性登录票据 cookie 后跳转前端
- `POST /exchange`：用票据 cookie 换取 JWT（与 `/api/auth/login` 相同响应）

## 5. 关键实现与依赖

- FastAPI + Uvicorn：轻量、类型友好、便于 WS 与 docs 扩展
- Pydantic：请求模型和约束统一
- Hermes 内核复用：
  - `SessionDB`（会话）
  - `cron.jobs`（任务）
  - `skills_config` / `skills_tool`
  - `hermes_cli.logs`
- 聊天核心：`aisoc/backend/chat/`（agent 回调 → WS 事件流，复用 `agent_runtime.default_agent_factory`）

---

## 6. 技术栈选择（为什么）

- **FastAPI**：
  - 同时处理 REST + WebSocket 方便
  - 自动 OpenAPI，便于前端/agent 调试
- **服务层拆分（routes + services）**：
  - 路由层保持薄，业务逻辑集中，便于 agent 定位改动点
- **用户账号 + JWT**：
  - 与 aegis 保持一致的最小角色模型（单一 `is_admin` 布尔位），独立 sqlite 用户库，不与 aegis 共享登录态
  - 相比共享 token，支持多用户审计、逐用户禁用/重置密码，以及按用户隔离聊天会话
- **复用 Hermes 核心数据源**：
  - 避免双写与数据漂移，确保 CLI 与 Dashboard 一致

---

## 7. 开发建议（给其他 Agent）

1. 新增接口优先放在 `routes/*.py + services/*.py`，避免在 `server.py` 堆逻辑。
2. 涉及安全策略改动时，先检查：
   - `PUBLIC_API_PATHS`
   - `auth_middleware`
   - WS token 校验逻辑
3. 如果改动 Overview/Chat 接口，务必联动前端 `src/lib/*.ts` 的调用契约。
4. 发布前至少做一次：

```bash
cd aisoc/frontend && npm run build
```

确保 `backend/web_dist` 已更新可用。
