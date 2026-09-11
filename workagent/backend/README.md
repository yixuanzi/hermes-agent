# WORKAGENT Backend (FastAPI)

## 1. 模块定位
WORKAGENT Backend 是 `hermes workagent` 的服务层。当前以 `server` 模块为主，后续扩展 `a2a` 模块后，同一入口将根据 `--module` 参数启动不同服务：
- `server`：当前 Web Console / FastAPI API / Chat TUI PTY-WebSocket 网关
- `a2a`：A2A (Agent-to-Agent) 协议服务
- `extcli`：本地增强交互命令行，直接在终端里与 `AIAgent` 对话

`server` 模块当前负责：
- 统一认证（Bearer Token）
- 会话、Cron、Skill、Memory、Logs、Overview 数据读取/写入
- Chat TUI 的 PTY/WebSocket 网关
- 托管前端静态资源（`web_dist`）

当前统一入口：`workagent/backend/main.py`
- `hermes workagent ...` 会复用这份入口逻辑
- 也可以直接运行 `python workagent/backend/main.py ...`
- 直接以 Python 启动时额外支持 `-p/--profile`，用于显式指定 Hermes profile；`hermes -p <profile> workagent ...` 仍保持原有逻辑不变

---

## 2. 开发与启动

### 2.1 推荐启动方式（集成前后端）
在仓库根目录执行：

```bash
hermes workagent --module server --port 9120 --tui
```

常用参数：
- `--module server|a2a|extcli`：选择启动模块，默认 `server`
- `--port 9120`：服务端口（默认 9120）
- `--host 127.0.0.1`：监听地址（默认 loopback）
- `--no-open`：不自动打开浏览器，仅 `server` 模块使用
- `--insecure`：允许非 loopback 绑定（有安全风险）
- `--tui`：启用嵌入式 chat PTY/WS 能力，仅 `server` 模块使用
- `--skip-build`：跳过前端构建（需已有 `backend/web_dist`），仅 `server` 模块使用

A2A 模块启动形态：

```bash
hermes workagent --module a2a --host 127.0.0.1 --port 9086
```

A2A 模块默认允许直接连接；设置 `WORKAGENT_A2A_AUTH=true` 后，会在 HTTP 中间件层对 A2A RPC 请求启用 Bearer Token 认证。

A2A 审批/澄清交互扩展（`hermes.interaction.v1`）的服务端协议、任务元数据、响应 endpoint 和清理语义见 [`backend/docs/a2a-interaction-extension.md`](docs/a2a-interaction-extension.md)。该扩展使用同一 A2A Bearer 认证；未声明扩展的调用方保持原有行为。

#### A2A 模式的 Toolset 配置

A2A 服务端创建的 Hermes agent 使用固定的平台键 `workagent-a2a`。因此，A2A agent 的 toolset 必须写在当前 profile 的 `platform_toolsets.workagent-a2a` 下：

```yaml
# ~/.hermes/config.yaml（或当前 profile 的 config.yaml）
platform_toolsets:
  workagent-a2a:
    - hermes-cli
    - userenv
```

`userenv` 是独立的 opt-in toolset，不属于 `hermes-cli`。只在 `platform_toolsets.cli`、`slack`、`aegis` 或其他调用方平台下启用 `userenv`，不会自动传递给 WorkAgent A2A agent。这里的 `a2a` toolset 是调用方使用 `a2a_list` / `a2a_delegate` 的能力，不是 A2A 服务端的配置键。

如果当前 profile 已经存在 `platform_toolsets.workagent-a2a`，请在原有列表中追加 `userenv`，保留其他已启用的 toolset。修改配置后需要重启 A2A 服务；已存在 context 复用的 agent 不会动态刷新工具列表。

另外，toolset 出现在工具 schema 中不代表每次调用都能成功。`userenv` 还要求 A2A 请求携带已认证的运行时用户身份（`source` 中包含 `platform` 和 `uid`）；没有用户身份时，调用仍会被拒绝。

#### A2A 模式启用 YOLO

如果要使用当前 `aisoc` profile 启动 A2A service，并让 A2A agent 自动跳过危险命令的人工审批，使用：

```bash
hermes -p aisoc --yolo aisoc --module a2a --host 127.0.0.1 --port 9120
```

`--yolo` 是 Hermes 的全局参数，必须放在 `aisoc` 子命令之前。下面的写法不会生效：

```bash
hermes aisoc --module a2a --yolo  # 错误：aisoc 子解析器不认识 --yolo
```

原因是 `--yolo` 注册在顶层解析器（`hermes_cli/_parser.py`），而 `aisoc` 子命令只解析 `--module`、`--host`、`--port` 等 A2A 参数。Hermes 主入口会在插件和工具加载前设置 `HERMES_YOLO_MODE=1`；`tools/approval.py` 在导入时冻结该值，因此 A2A service 进程内的每个 agent turn 都能继承 YOLO 状态。A2A service 使用同进程启动 Uvicorn，不会重新执行或重新启动一个丢失该状态的子进程。

YOLO 的作用是旁路危险命令的人工 approval prompt，适用于没有交互式审批通道的 A2A 委派场景。例如，普通 approval 模式下，A2A 委派执行 `chmod 777` 可能进入 `pending_approval` 并无法继续；启用 YOLO 后会自动执行。

YOLO 仍受以下安全规则约束：

- hardline 灾难命令（例如 `rm -rf /`、`mkfs`、块设备覆写、关机）仍然硬阻断；
- `approvals.deny` 命中的命令优先于 YOLO，仍然无条件拦截；
- 建议为 A2A 配置 deny glob，例如 `chmod * 777*`、`*curl*|*sh*`、`git push --force*`。

也可以在启动前使用环境变量开启同样的进程级 YOLO：

```bash
HERMES_YOLO_MODE=1 hermes -p aisoc aisoc --module a2a --host 127.0.0.1 --port 9120
```

持久化配置 `approvals.mode: off` 也会跳过审批，但它会影响该 profile 下的 CLI、gateway 和 A2A 全部渠道，范围大于仅为 A2A 启用 YOLO，不建议作为首选方案。

安全提示：A2A service 是远程委派执行入口。启用 YOLO 后，能够访问该端点的调用方可以让宿主机自动执行大部分原本需要人工确认的危险命令。除非有明确需求，否则应保持 `--host 127.0.0.1`、不要使用 `--insecure`，并同时启用 A2A 认证及 `approvals.deny` 高危命令兜底。

`extcli` 模块启动形态：

```bash
hermes workagent --module extcli
```

也支持直接以 Python 启动同一入口：

```bash
python workagent/backend/main.py -p myprofile --module server --port 9120 --tui
python workagent/backend/main.py --profile myprofile --module a2a --host 127.0.0.1 --port 9086
python workagent/backend/main.py -p myprofile --module extcli
```

说明：
- `-p/--profile` 仅用于 `python workagent/backend/main.py ...` 这类 direct startup 场景
- `hermes -p <profile> workagent ...` 继续由 Hermes 主入口负责 profile 解析，不需要额外改写参数

`extcli` 特性：
- 直接复用 `AIAgent` 对话循环，并通过 SessionDB 恢复上下文，不再手动维护 history
- 输出默认写入 `/tmp/extcli_output`，输入继续通过当前终端读取，实现输入/输出通道分离
- 支持用户输入、AI 流式输出、tool call 展示、tool result 摘要展示
- tool result 最多显示前 50 个字符，超出部分以 `...` 省略
- `main` 会话忙碌时拒绝新的主会话输入，不缓存待回放消息
- 支持 `delegate_ext(is_loop=true)` 前台子会话；子会话激活时，终端输入会临时路由给子 agent
- 子会话内 `/main` 与 `/exit` 等价，都会结束子会话并返回主会话；只有主会话前台时 `/exit` 才会退出整个 `extcli`
- 支持 `/new` 重置当前主会话；当子会话前台时，`/new` 会作为普通输入传给子 agent
- `a2a` / `extcli` 模块启动的 agent 会按 Hermes 原生语义加载 `config.yaml` 中启用的 `mcp_servers`
- 可通过环境变量 `WORKAGENT_MCP_ACTIVE` 覆盖该行为：未设置时自动加载，`true/1/yes/on` 强制启用，`false/0/no/off` 禁用

### 2.2 直接以 Python 启动（调试后端）

```bash
python workagent/backend/main.py -p myprofile --module server --host 127.0.0.1 --port 9120 --no-open --tui
```

或只调后端 server 模块：

```bash
python -c "from workagent.backend.server import start_server; start_server(host='127.0.0.1', port=9120, open_browser=False, embedded_chat=True)"
```

### 2.3 Token 配置
- `WORKAGENT_SESSION_TOKEN`：若设置，则使用静态 token（`token_source=env`）
- 未设置时：进程启动自动生成随机 token（`token_source=generated`）
- `WORKAGENT_A2A_AUTH`：A2A 模块认证开关；`true/1/yes/on` 启用，未设置或 `false/0/no/off` 关闭
- `A2A_SESSION_TOKEN`：仅 `a2a` 模块使用；启用 A2A 认证时若设置，则使用静态 token（`a2a_token_source=env`）
- 启用 A2A 认证且未设置 `A2A_SESSION_TOKEN` 时：进程启动自动生成随机 A2A token（`a2a_token_source=generated`）
- `WORKAGENT_A2A_ADMIN_TOKEN`：仅用于启用 `/man` A2A 管理页面和换取短期管理凭证；这是独立的管理 secret，不得与负责 A2A 通信认证的 `A2A_SESSION_TOKEN` 复用相同值
- `WORKAGENT_MCP_ACTIVE`：仅影响 `a2a` / `extcli` 的 MCP 装载；默认跟随配置自动加载，设置为 `false/0/no/off` 可在启动时关闭 MCP

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
  - token 校验与配置装配

### 3.2 认证机制
- 所有 `/api/*` 默认受保护
- 白名单（无需认证）：
  - `/api/auth/login`
  - `/api/auth/session`
  - `/api/auth/logout`
  - `/api/system/bootstrap`
  - `/health`
- HTTP：`Authorization: Bearer <token>`
- WebSocket：`?token=<token>`（浏览器 WS 升级不便带自定义 Authorization）

### 3.3 A2A 认证机制
- 默认关闭；通过 `WORKAGENT_A2A_AUTH=true` 启用
- 启用后使用 `Authorization: Bearer <A2A_SESSION_TOKEN>` 保护 A2A HTTP 路由
- 公开白名单：
  - `/health`
  - `/.well-known/agent-card.json`
  - `/a2a/.well-known/agent-card.json`（或 `A2A_BASE_PATH` 对应前缀）
- 仅在 HTTP 中间件层认证，不改变 A2A executor、消息流或任务状态机
- `WORKAGENT_SESSION_TOKEN` 仍仅用于 `server` 模块；`A2A_SESSION_TOKEN` 仅用于 `a2a` 模块

### 3.4 A2A 管理与重启
- 设置非空 `WORKAGENT_A2A_ADMIN_TOKEN` 后启用 `GET /man`；未设置时页面会明确显示管理未启用，管理 API 返回 503，重启不可用
- 页面通过 `POST /man/api/auth` 提交 `{"token":"<WORKAGENT_A2A_ADMIN_TOKEN>"}`，成功后换取有效期 5 分钟的专用 Bearer；管理 secret 和短期 Bearer 都只保存在浏览器内存中，不写入 cookie、localStorage 或 sessionStorage
- `POST /man/api/restart` 只接受上述短期 Bearer，不接受原始管理 secret 或 `A2A_SESSION_TOKEN`；浏览器流程需要危险操作确认并准确输入 `RESTART A2A`
- 管理路由仅允许浏览器同源调用；无 `Origin` 的非浏览器客户端仍可使用相同的管理认证流程
- 重启成功返回 HTTP 202，统一字段为 `accepted`、`already_requested`、`service`、`pid`；重复请求不会启动第二个 watcher
- 重启会重放当前启动命令；若认证 token 原本由进程启动时随机生成，新进程可能生成不同值

### 3.5 Chat（`--tui`）链路设计
当 `embedded_chat=True`（CLI `--tui`）时启用：
- `/api/chat/pty`：浏览器 <-> PTY 双向字节流
- `/api/chat/ws`：JSON-RPC sidecar（tui_gateway）
- `/api/chat/pub`、`/api/chat/events`：事件分发通道

非 `--tui` 模式下上述 WS 返回 `4403`。

---

## 4. API 模块清单

### 4.1 Auth
前缀：`/api/auth`
- `POST /login`
- `GET /session`
- `POST /logout`

### 4.2 System
- `GET /health`
- `GET /api/system/bootstrap`

### 4.3 Chat
前缀：`/api/chat`
- `GET /status`
- `WS /pty`
- `WS /ws`
- `WS /pub`
- `WS /events`

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

### 4.6 Skills
前缀：`/api/skills`
- `GET /` （skill item 含 `path` 字段）
- `GET /{skill_name}` （返回 `content` + `appendix[]`）
- `GET /{skill_name}/appendix?path={path}` （返回附件文本内容）
- `DELETE /{skill_name}` （删除当前 profile 本地 skills root 下的整个 skill 文件夹）
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
- `GET /`

### 4.9 Knowledge Base
前缀：`/api/kb`
- `GET /tree?cwd=` （列出指定目录下的文件和文件夹，`cwd` 为空时列出根目录）
- `GET /documents?path=` （返回指定文件的文本内容）
- `PUT /documents` （写回已存在的文件，JSON 请求体为 `{"path":"relative/path.md","content":"..."}`）

`PUT /documents` 成功返回 `{"ok":true,"path":"...","size":123,"modified":1720000000}`。
写入仅允许知识库 root 下的现有普通文件，沿用 2 MB 限制；路径遍历、root 外路径、目录和外部符号链接会被拒绝。

环境变量：`WORKAGENT_WIKI_PATH` 指定知识库根目录，未设置或路径不存在时返回 503。

安全限制：
- 禁止路径遍历（`..`）和符号链接逃逸
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

说明（2026-05 更新）：
- 原 `GET /api/overview/cronjobs/{job_id}/history` 已迁移到 `GET /api/cron/jobs/{job_id}/history`
- 原 `GET /api/overview/sessions/{session_id}/detail` 已迁移到 `GET /api/sessions/{session_id}/detail`
- Overview 仅保留总览/聚合接口；明细下钻接口归属到对应业务模块（Cron / Sessions）

### 4.11 A2A Management
- `GET /man`
- `POST /man/api/auth`：请求体 `{"token":"..."}`，返回 5 分钟、仅用于管理 API 的 Bearer 及到期信息
- `POST /man/api/restart`：需要管理 Bearer，成功返回 HTTP 202 和 `accepted`、`already_requested`、`service`、`pid`

---

## 5. 关键实现与依赖

- FastAPI + Uvicorn：轻量、类型友好、便于 WS 与 docs 扩展
- Pydantic：请求模型和约束统一
- Hermes 内核复用：
  - `SessionDB`（会话）
  - `cron.jobs`（任务）
  - `skills_config` / `skills_tool`
  - `hermes_cli.logs`
- PTY/TUI 桥接：`services/tui_embed.py` + `tui_gateway`

---

## 6. 技术栈选择（为什么）

- **FastAPI**：
  - 同时处理 REST + WebSocket 方便
  - 自动 OpenAPI，便于前端/agent 调试
- **服务层拆分（routes + services）**：
  - 路由层保持薄，业务逻辑集中，便于 agent 定位改动点
- **Bearer Token 简模型**：
  - 局域网本地工具场景下实现成本低、调试效率高
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
cd workagent/frontend && npm run build
```

确保 `backend/web_dist` 已更新可用。
