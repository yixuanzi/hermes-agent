# Policy 与 Audit 筛选布局修正设计

## 目标

- 移除 Policy 页 Global Routing Rules 内容区中重复显示的 `Global Routing Rules (全局规则路由)` 标签，仅保留页面上方统一的 Policy 子 Tab。
- 统一 Audit Logs 筛选区所有文本输入框、下拉框和日期时间输入框的可视高度。
- 让 Audit Logs 第二行筛选控件的底边保持在同一水平线上。

## 根因

- Policy 的重复标签来自 Global Routing Rules 内容区 header 中单独渲染的静态 `<div>`，与上方子 Tab 没有状态关系。
- Audit 筛选区使用 CSS Grid。日期字段包含标签文字和输入框，会撑高所在网格行；同一行的普通输入框和下拉框受 Grid 默认拉伸行为影响，而日期输入框自身使用了不同的垂直 padding，导致控件高度不一致。

## 实现

- 在 `PolicyTab.tsx` 中删除内容区 header 内的重复静态标签，保留右侧搜索、刷新和新建规则操作区；不改变 Global Routing Rules 表格、弹窗和切换逻辑。
- 在 `AuditLogsTab.tsx` 中：
  - 为筛选表单的文本输入框、下拉框和日期时间输入框统一设置固定高度 `h-10`。
  - 为筛选网格增加底部对齐，使同一行控件底边一致。
  - 日期字段仍保留 `From UTC`、`To UTC` 标签，并使用纵向容器承载标签和输入框。
  - 保持现有六列宽屏布局、字段顺序、按钮位置、筛选参数和请求逻辑不变。

## 测试

- Policy 组件测试确认页面只保留一个可见的 Global Routing Rules 子 Tab，不再出现内容区重复标签。
- Audit 组件测试确认所有筛选表单控件使用统一高度，日期字段保留标签并参与底部对齐。
- 运行完整前端 Vitest、TypeScript lint、生产构建及 `git diff --check`。

## 非目标

- 不调整后端 API、筛选语义、分页、Audit 表格或 Agent Policy CRUD。
- 不重构全站表单组件或改变现有页面尺寸和视觉主题。
