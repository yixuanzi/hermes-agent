# Aegis Frontend

`aegis/frontend/` 是 Aegis 安全协同中枢的前端控制台。它基于 `React 19 + Vite 6 + Tailwind CSS 4 + Motion`，其中 `Agent Orchestration` 与 `Routing Policy` 已接入 `aegis/backend` 的真实 API，并由后台直接托管生产静态资源。

## 当前能力

- `Overview`：展示安全中枢总览、拓扑星图、Agent 索引，以及基于近 7 天 A2A 委派审计的运行指标。三层星图的设计基线见 [`../aegis_starmapping.json`](../aegis_starmapping.json)。
- `Aegis Chat`：提供本地模拟的安全分析会话流，按预设场景回放 Agent/VIP Tool 协同链路。
- `Agent Orchestration`：支持登录后对 `/api/agents` 执行新增、编辑、删除。
- `Routing Policy`：支持登录后对 `/api/routing/global` 执行新增、编辑、删除。

## 技术栈

- `React 19`
- `TypeScript`
- `Vite 6`
- `Tailwind CSS 4`
- `lucide-react`
- `motion`

## 本地启动

前置要求：

- `Node.js 20+`
- `npm 10+`（推荐）

启动步骤：

1. 进入模块目录：
   `cd aegis/frontend`
2. 安装依赖：
   `npm install`
3. 启动 Aegis 后端（推荐）：
   `hermes aegis`
4. 在浏览器打开：
   `http://127.0.0.1:9130/login`

说明：

- 登录方式为用户名/密码，成功后前端会保存后端签发的 JWT access token。
- 登录页提供“Aegis SSO”和“Lark SSO”两个入口。Aegis SSO 通过 `/api/sso/start?sso=1` 进入 Aegis Portal；Lark SSO 通过 `/api/lark/start` 进入 Lark 授权。
- 从 Portal“我的服务”进入时，Aegis 根路径接收 `organization_id + client_id` 并自动启动 OIDC；不读取或转发 `subscription_id`。
- OIDC 回调页面为 `/sso/callback`，只兑换 HttpOnly 一次性票据，不在 URL 中保存 access token 或 refresh token。
- 首次启动前设置 `AEGIS_BOOTSTRAP_ADMIN_PASSWORD`，系统仅在该 secret 存在时创建 `admin`；未设置时显示初始化提示且拒绝登录，不提供匿名管理员注册或固定默认密码。
- Agent 与 Global Rule 数据持久化在 `HERMES_HOME/a2a.json`。
- 用户数据持久化在 `HERMES_HOME/aegis.db`。
- Chat 页面的会话回放仍然保存在浏览器 `localStorage`，键名为 `aegis_convs`。

如果只想跑前端开发服务器：

1. 启动后端：
   `python aegis/backend/main.py --no-open`
2. 另一个终端进入 `aegis/frontend`
3. 运行：
   `npm run dev`
4. 打开：
   `http://127.0.0.1:3000/login`

Vite 已经代理 `/api` 和 `/health` 到 `http://127.0.0.1:9130`。

## 常用命令

- `npm run dev`：启动开发环境，默认监听 `0.0.0.0:3000`
- `npm run lint`：执行 `tsc --noEmit`
- `npm run build`：生成生产构建产物到 `aegis/backend/web_dist`
- `npm run preview`：本地预览构建结果

## Portal OIDC 配置

后端运行环境需要配置 `OIDC_CLIENT_ID` 和 `OIDC_CLIENT_SECRET`，完整配置见
[`../backend/README.md`](../backend/README.md)。注册回调固定为
`http://127.0.0.1:9130/api/sso/callback`，回调成功后由前端处理
`/sso/callback` 页面并进入 Overview。

Lark SSO 默认关闭。后端设置 `LARK_SSO_ENABLE=true` 后，登录页才显示
Lark SSO 入口并允许发起授权；同时配置 `LARK_APP_ID`、`LARK_APP_SECRET`
和 `LARK_REDIRECT_URI=http://127.0.0.1:9130/api/lark/callback`，并在 Lark
开发者后台登记同一个完整回调地址、开启 `contact:user.email:readonly`
权限。Lark 回调成功后同样进入 `/sso/callback`，由前端兑换现有 Aegis JWT。

## 常规功能页面实现规范

新增或重构 Aegis 常规功能页时，遵循统一的三段式页面结构，并优先复用 `src/index.css` 中的 Aegis 页面原语：

1. **页面说明区块**：使用 `aegis-page-intro`，包含清晰的页面标题、简短用途说明，以及按需显示的右侧摘要徽标。说明应面向当前操作者，避免重复内容区已有的字段说明。
2. **Tab 分页块（如有）**：仅在页面存在多个独立业务视图时使用 `aegis-page-tabs` 和 `aegis-page-tab`。必须使用 `tablist` / `tab` / `tabpanel` 语义、正确的 ARIA 关联，并支持点击、方向键和 Home/End 键切换。需要与 Policy 相同的紧凑尺寸时，使用页面专属的 `aegis-page-tabs--compact` 修饰，不改变其它页面。
3. **内容区域块**：每个业务视图使用独立的 `aegis-page-content`，由标题/说明和操作区组成，再承载表格、指标、表单、空态或错误态；复杂操作不应挤入页面说明区。

视觉与可访问性要求：

- 优先使用 `--aegis-*` 主题令牌和现有页面原语，禁止为新页面添加深色专用硬编码背景、边框或文字颜色。
- 所有状态使用 `aegis-status-badge`、`aegis-status-indicator` 或 `aegis-status-text` 的 success / warning / danger / muted 语义变体，确保 Daylight Signal、Aegis Night 与 Neutral Ops 下均有足够对比度。
- 页面在窄屏下应使说明、操作与内容自然换行；交互控件必须具备可见焦点态和可读的标签。

### 紧凑布局要求

- 常规管理页优先使用 `aegis-admin-page`、`aegis-page-intro`、`aegis-page-tabs` 与 `aegis-page-content`；控制台类页面（如 Policy、Agent Orchestration）使用 `aegis-console-page`、`aegis-console-intro`、`aegis-console-tabs` 与 `aegis-console-section-header`。不要在单个页面重复叠加 `p-6`、`space-y-6` 等大间距工具类。
- 页面、卡片、标题栏和 Tab 的尺寸统一引用 `--aegis-layout-page-padding`、`--aegis-layout-page-gap`、`--aegis-layout-card-padding`、`--aegis-layout-card-gap`、`--aegis-layout-header-*` 与 `--aegis-layout-tab-padding`。若需要更紧凑的标题栏，只添加 `aegis-page-content__header--compact`，不要为单个对象写独立的像素间距。
- `aegis-page-content` 默认应按自身内容高度收束；表格、状态卡或筛选结果较少时，最后一条数据的底线应紧贴内容，而不是由 `flex: 1` 拉出大块空白。只有确有固定视窗、滚动或可视化需求的区域才显式使用填满高度的布局。
- 有内边距的内容体使用 `aegis-page-content__body--padded`，筛选/创建表单使用 `aegis-page-filter-bar`；手册等阅读型页面使用 `aegis-manual-header` 与 `aegis-manual-article`，保持统一的紧凑留白。
- 数据表默认参考 Policy 的节奏：紧凑表头、`p-3` 单元格、清晰的行分隔与末行底线；列宽优先通过 `colgroup`、截断和横向滚动解决，不用放大单元格或操作区挤压内容。
- Chat、Overview、登录与危险操作弹窗等高交互区域已采用专属密度；不要仅为压缩视觉空间而减小输入框、主要操作按钮或触控目标。紧凑化应优先消除重复的外层留白和无意义的卡片空白。

## 目录结构

```text
aegis/frontend
├── index.html
├── logo
├── package.json
├── vite.config.ts
├── src
│   ├── App.tsx
│   ├── index.css
│   ├── main.tsx
│   ├── types.ts
│   ├── vite-env.d.ts
│   ├── data/mockData.ts
│   ├── lib
│   │   ├── adapters.ts
│   │   ├── api.ts
│   │   └── auth.ts
│   └── components
│       ├── Sidebar.tsx
│       ├── LoginScreen.tsx
│       ├── OverviewTab.tsx
│       ├── ChatTab.tsx
│       ├── AgentTab.tsx
│       └── PolicyTab.tsx
```

## 数据与交互说明

- `Agent Orchestration` 读取和写入 `/api/agents`。
- `Routing Policy` 读取和写入 `/api/routing/global`。
- `Overview` 读取 `/api/overview/agents` 和 `/api/overview/stats`；后者统计近 7 天委派的 Agent、平台、用户、任务量、成功率及相邻周期变化。
- Chat 页面的回复与执行链路仍为前端模拟逻辑，不会真正调用 A2A、RPC 或外部安全系统。
- 当前侧边栏品牌图标接入的是 `logo/aegis-icon-brand-tile-color.svg`。

## 本次 Review 摘要

- 已将页面顶部和总览页中的 UTC 时间改为动态生成，避免“LIVE”状态下仍显示固定时间。
- 已为总览页 Agent 占比增加空数据保护，避免在 Agent 被删空时出现 `NaN%`。
- 原模块最初残留了 AI Studio 模板文案；本 README、页面标题和部分注释已经更新为当前模块语义。

## 已知边界

- 当前只对接了 `Agent Orchestration` 和 `Routing Policy` 两个后端模块。
- 拓扑星图与 Aegis Chat 仍然偏演示态，尚未接真实安全执行链路；Overview 的近 7 天委派指标已接入审计日志。旧拓扑渲染仍在组件中写死，后续将按 [`../aegis_starmapping.json`](../aegis_starmapping.json) 的 Aegis 中心、7 个业务域 Agent 外环和 84 个群星节点重构。
