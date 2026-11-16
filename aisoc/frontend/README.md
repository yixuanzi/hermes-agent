# AISOC Frontend (React + Vite)

## 1. 模块定位
AISOC Frontend 是 `hermes aisoc` 的 Web 控制台，负责：
- 登录认证与 token 缓存
- Overview / Chat / Sessions / Cron / Skills / Memory 页面
- 调用后端 `/api/*` 与 WebSocket 接口

构建产物输出到：`aisoc/backend/web_dist`

---

## 2. 开发与启动

### 2.1 安装依赖

```bash
cd aisoc/frontend
npm install
```

### 2.2 本地开发命令

```bash
npm run dev      # Vite 开发模式
npm run test     # Vitest
npm run build    # tsc + vite build（输出到 ../backend/web_dist）
```

### 2.3 推荐联调启动
推荐由根命令统一启动：

```bash
hermes aisoc --port 9120
```

该命令会构建前端并由后端托管静态资源，避免前后端端口/CORS错配。

---

## 3. 架构与设计

### 3.1 前端分层
- `src/main.tsx`：React 入口，挂载 Router
- `src/App.tsx`：路由总表
- `src/components/*`
  - `AppShell`：整体壳与左侧导航
  - `RequireAuth`：鉴权守卫
  - `PageMissionHeader`、`StateBlock`：通用 UI 结构
- `src/pages/*`：页面级业务
- `src/lib/*`
  - `api.ts`：统一 fetch + Bearer 注入 + 错误封装
  - `auth.ts`：token 持久化（localStorage）
  - `overview.ts` / `chat.ts`：模块 API 访问器
- `src/styles.css` + `pages/OverviewPageReplica.css`：全局与页面主题样式

### 3.2 路由设计
受 `RequireAuth` 保护的主路由：
- `/overview`
- `/chat`
- `/sessions`
- `/cron`
- `/skills`
- `/memory`

未认证访问会跳转 `/login`。

### 3.3 认证流程
1. 登录页输入 token -> `POST /api/auth/login`
2. 成功后写入 `localStorage`（`aisoc.sessionToken`）
3. `RequireAuth` 启动时调用 `GET /api/auth/session` 校验
4. `fetchJSON` 默认自动注入 `Authorization: Bearer <token>`

### 3.4 统一聊天模块（`src/chat/`，1.0）
- 运行时：`src/chat/runtime/chatRuntime.tsx`（`AisocChatProvider` 挂在 AppShell 上，
  每会话一条 `WS /api/chat/session`，`session.bind`/`message.send`/approval/clarify）
- 会话列表来自后端 `GET /api/sessions?source=aisoc_web`，历史用
  `GET /api/sessions/{id}/detail` 水合；`/chat?session=<id>` 续聊、`/chat?quick=<name>`
  预填快捷指令草稿
- 组件层：`src/chat/components/`（三栏 ChatLayout、SessionListPane、MessageStream +
  bubbles、contentEditable Composer（`@` 快捷指令 pill、附件、A2UI 开关）、右侧 drawer
  （Workflow 图 + 文件预览 sandboxed iframe + Agent2UI postMessage 回流））
- 右下角 `FloatingChatWidget` 复用同一 Provider 的活跃会话，仅作便捷入口

---

## 4. 页面模块清单

- `LoginPage`：登录
- `OverviewPage`：总览大盘（赛博风复刻）
- `ChatPage`：嵌入式终端 chat
- `SessionsPage`：会话列表、消息查看、relaunch
- `CronPage`：任务列表/操作/详情
- `SkillsPage`：skills 开关
- `MemoryPage`：SOUL、USER、memory files 编辑
- `NotFoundPage`：404

说明：`LogsPage.tsx` 目前不在 `App.tsx` 路由中挂载（后端 logs API 仍可用）。

---

## 5. 关键接口依赖
前端主要调用：
- `/api/auth/*`
- `/api/system/bootstrap`
- `/api/overview/*`
- `/api/chat/*`（含 WS）
- `/api/sessions/*`
- `/api/cron/*`
- `/api/skills/*`
- `/api/memory/*`

Overview 页面接口说明（2026-05 更新）：
- 总览聚合数据：`/api/overview/*`（如 status/stats/token-trend/security-events/keywords/cron-token-dist/cronjobs）
- Cron 历史下钻：`GET /api/cron/jobs/{job_id}/history`
- Session 明细下钻：`GET /api/sessions/{session_id}/detail`
- 不再使用旧路径：`/api/overview/cronjobs/{job_id}/history`、`/api/overview/sessions/{session_id}/detail`

接口契约主要集中在：
- `src/lib/api.ts`
- `src/lib/overview.ts`
- `src/lib/chat.ts`

---

## 6. 技术栈选择（为什么）

- **React + React Router**：
  - 页面化工作台结构清晰，便于按模块并行开发
- **TypeScript**：
  - 接口类型可追踪，减少前后端契约偏差
- **Vite**：
  - 构建速度快，适合高频迭代
- **Vitest**：
  - 页面结构与行为测试轻量可维护

---

## 7. 开发建议（给其他 Agent）

1. 新增页面先补 `src/lib/*` 接口封装，再写页面逻辑，避免直接散落 `fetch`。
2. 任何鉴权相关改动必须同时检查：
   - `lib/auth.ts`
   - `components/RequireAuth.tsx`
   - `lib/api.ts`
3. 改动聊天模块时保持 `/api/chat/session` WS 事件协议与 aegis chat 模块兼容（fork 关系，勿单方面改事件字段）。
4. 提交前建议至少执行：

```bash
npm run test
npm run build
```

确保类型、测试与产物同步通过。

5. 偏好（开发流程约定）：
   - 每完成一次 AISOC 前端开发测试（如执行 `npm run test`）后，需继续执行 `npm run build` 完成构建校验。
