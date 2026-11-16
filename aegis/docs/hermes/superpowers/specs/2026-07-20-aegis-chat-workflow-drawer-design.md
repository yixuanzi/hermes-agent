# Aegis Chat 全局 Workflow 抽屉设计

## 目标

在 Aegis Chat 的 `CENTRAL ARCHIVE` 标题行添加 workflow 图标。点击后，页面进入全窗口 50/50 分屏状态：左半屏为当前 session 的 Workflow 抽屉，右半屏为继续可操作的现有聊天界面。首版只提供工作流画布的空状态，不绘制或持久化执行动作。

## 范围

- 在 `ChatTab` 的 `CENTRAL ARCHIVE` 标题行新增可访问的 workflow 触发按钮。
- 打开时，Chat 页面以 fixed 全窗口容器展示，归档栏不显示。
- Workflow 抽屉固定在视口左侧，占 `50vw`；右侧聊天窗占剩余 `50vw`。
- 右侧沿用同一个聊天组件树、消息、输入值和 WebSocket runtime，不创建镜像或第二份聊天状态。
- 抽屉标题显示当前会话标题；主体显示“执行轨迹将在这里呈现”的空状态和零动作计数。
- 支持点击触发图标、关闭按钮、再次点击图标和 Escape 关闭；关闭后恢复现有“归档栏 + 全宽聊天”布局。

## 不在范围内

- 将 tool、delegate、routing 或其他运行时事件绘制为节点、连线或时间轴。
- 新增 API、WebSocket 事件、浏览器存储字段或后端数据模型。
- 更改会话归档、消息收发、审批、澄清或复制逻辑。

## 组件与状态

`ChatTabContent` 增加本地布尔状态 `workflowDrawerOpen`。该状态只决定布局与抽屉可见性，不进入 `AegisChatProvider`，也不保存到 localStorage。

打开状态下，`ChatTabContent` 的根布局切换为 fixed 全窗口层。工作流抽屉和原聊天主区成为并列的两个 50% 面板；归档栏通过条件渲染隐藏。聊天主区保持其原有子树和输入交互，从而避免重连、滚动丢失和状态重复。

工作流抽屉使用语义化辅助面板（而非 modal dialog），因为右侧聊天必须持续可编辑。关闭按钮和触发按钮包含明确的 `aria-label`；`useEffect` 在抽屉打开时注册 Escape 监听，并在关闭或卸载时清理。

## 交互流程

1. 用户在 `CENTRAL ARCHIVE` 行点击 workflow 图标。
2. 页面立即切换为全窗口 50/50 布局，左侧抽屉从左边缘进入，右侧聊天对齐视口右半边。
3. 用户可以继续阅读消息、输入文字与发送请求；抽屉的画布区域保持空状态。
4. 用户点击关闭按钮、再次点击 workflow 图标或按 Escape。
5. 抽屉退出，页面恢复归档栏与全宽聊天布局。

## 测试

扩展 `ChatTab` 的 Vitest / Testing Library 测试，验证：

- workflow 图标可访问且可打开抽屉；
- 打开状态渲染当前 session 的 workflow 空状态，且右侧聊天输入框仍存在；
- 关闭按钮与 Escape 会恢复原始布局；
- 既有聊天运行时和会话测试保持通过。

运行目标测试、`npm run lint` 与 `npm run build` 作为实现后的验证。
