# AISOC Design System v4 — Master (Global Source of Truth)

> v2(2026-08):从"霓虹赛博黑客风"迁移到专业 SOC 作战台风格。
> v3(2026-08):对齐 aegis(aegis-night,近黑底 `#020408`、cyan 单色强调、**明令去辉光**)。
> **v4(2026-08):"Command Deck / Holographic Ops"。** 移植 aisoc_dev 的成熟设计语言:
> 深空海军蓝底 `#0a0f1a`、**青→蓝→紫签名渐变**、频玻璃面板 + 渐变发丝边、环境氛围层、
> **克制但真实的辉光**、Recharts HUD 图表、**深/浅双主题**。**本版有意推翻 v3 的去辉光立场**
> —— 辉光/渐变/氛围漂移在 v4 是设计语言的一部分,但受"一处出彩、余处克制"与 WCAG AA +
> `prefers-reduced-motion` 双重约束。
>
> **真相源**:`src/design/tokens.css`(CSS 变量,深/浅双主题)、`src/design/tokens.ts`(JS/canvas 色板镜像,
> Recharts/cytoscape 用)、`src/design/base.css`(玻璃/排版/发丝/动效工具类)、`src/design/hud-overlay.css`
> (壳 chrome + `.tac-*` 氛围 + 浅色补丁)。页面级覆盖放 `pages/<page>.md`,无覆盖时以本文件为准。

## 1. 设计原则

1. **签名能量**:青→蓝→紫渐变是整个系统的"能量线",用于品牌字、活动态、关键强调、图表序列;不滥用。
2. **一处出彩,余处克制**:每屏只让一个元素(hero 数字、活动态、关键图表)承载辉光/渐变,周围保持安静。
3. **深/浅双主题**:`<html data-theme>` 为真相源,`localStorage["aisoc_theme"]` 持久,首帧脚本防闪屏(FOUC)。两个主题都须满足 WCAG AA(正文 4.5:1);浅色下签名渐变加深(青 `#0891b2`、蓝 `#2563eb`、紫 `#7c3aed`)避免洗白。
4. **高密度作战台**:`--density-scale: 0.9`,间距 8–24px 档,冷色数据优先。
5. **动效有节奏、可关闭**:140/240/420ms 三档 ease;氛围漂移、count-up、live 呼吸、图表描入均属语义动效;**所有动效必须在 `prefers-reduced-motion: reduce` 下停用**。

## 2. 色彩 Tokens

分层(换值不换名):**L0** Command Deck 基础(`--bg-*`/`--ink-*`/`--stroke-*`/`--accent-*`/`--grad-*`/`--glow-*`)→ **L1** `--aisoc-*` 别名重指向 L0(`styles.css` 的 `@theme inline` 据此把 `bg-aisoc-*`/`text-aisoc-*` 工具类映射到运行时变量,主题翻转自动重着色)→ **L2** 语义状态。

### 深色(`:root`)
| 角色 | Token | 值 |
|---|---|---|
| 底/深/面板/浮起 | `--bg-abyss` / `--bg-deep` / `--bg-panel` / `--bg-elevated` | `#0a0f1a` / `#0d1420` / `#111a29` / `#182236` |
| 玻璃 | `--bg-glass` / `--bg-glass-strong` | `rgba(20,30,48,.24)` / `rgba(28,40,62,.34)` |
| 墨阶 | `--ink-hi` / `--ink-mid` / `--ink-lo` | `#eaf2ff` / `#93a4c4` / `#5b6b8a` |
| 描边/发丝 | `--stroke-soft` / `--stroke-mid` / `--hairline` | 冷白 0.09 / 0.16 / 0.06 |

### 浅色(`:root[data-theme="light"]`)
纸感浅灰底 `#f4f6fb`、面板 `#ffffff`、墨阶深蓝墨 `#0f1e33 / #46587a / #7d8ba8`、描边深墨蓝、玻璃转白半透明、签名渐变加深、辉光转浅投影(去黑、去白内高光)。

### 签名与强调
| Token | 值 |
|---|---|
| `--accent-cyan` / `--accent-blue` / `--accent-violet` | `#22d3ee` / `#3b82f6` / `#8b5cf6` |
| `--grad-signature` | `linear-gradient(135deg, #22d3ee, #3b82f6 48%, #8b5cf6)` |
| `--grad-hairline` | 透明→青→紫→透明(发丝边/分隔) |
| 图表副色 | `--accent-indigo #6366f1` / `--accent-emerald #34d399` / `--accent-amber #fbbf24` / `--accent-rose #fb7185` |

### 语义状态
`--st-ok`=emerald / `--st-warn`=amber / `--st-crit`=rose / `--st-info`=blue。状态信息必须**同时**有非颜色标识(文字/图标),不靠颜色单独承载。

### JS/canvas 色板镜像(`design/tokens.ts`)
Recharts/canvas/cytoscape 读不到 CSS 变量,故 `tokens.ts` 镜像:`CHART_CHROME`(深/浅:axisTick/gridStroke/tooltip/textHi/dotCore)、`CATEGORICAL` / `CATEGORICAL_LIGHT`、`GRADIENT_STOPS`、`STATUS`、`MOTION`。`useChartTheme()` 经 `useTheme()` 订阅,主题切换时图表自动重着色。本体图谱域色(`ontology/design/tokens.ts` DOMAIN_META,宝石色)是主题无关的数据编码色板,两主题下都可读。

## 3. 字体

- **展示/标题**:`--font-display` = **Chakra Petch**,Space Grotesk / Inter 回退(h1–h4、`.font-display`)。
- **正文**:Inter(400–800)。
- **数据/代码/eyebrow**:`--font-mono` = JetBrains Mono;eyebrow = mono 600 大写 + 0.16em 字距;表格数字 `tabular-nums`。
- 正文 16px × 0.9 密度;代码 12px。

## 4. 形状 · 玻璃 · 辉光

- 圆角:`--radius-sm 10px` / `md 14px` / `lg 18px`(徽章 999px)。
- **频玻璃**:`.glass` = `--bg-glass` + `--stroke-soft` + `backdrop-blur(14px)` + `--shadow-panel`;`.glass-strong` 重模糊为特例(本体节点详情)。
- **渐变发丝边**:关键面板/分隔用 `--grad-hairline`(伪元素或 `.hairline`)。
- **辉光(v4 恢复,克制)**:`--glow-cyan/violet/blue` 为 `0 0 26px -6px` 冷色低扩散;仅用于 hero/活动态/关键 CTA,浅色下自动转浅投影。禁止满屏辉光、荧光文字堆叠。
- z-index 走 `--z-*` 阶梯。

## 5. 环境氛围层(`.tac-*`,`components/ambient/HudGrid`)

深空底 + 多团缓慢游移光晕(青/蓝/紫,screen 混合)+ 极淡扫描网格 + 暗角/顶光。纯 CSS、`pointer-events:none`、叠最底层。挂在 AppShell 与 Login 的 `.app-ambient`。**漂移动画在 reduced-motion 下停用。**

## 6. 组件要点

- **按钮**:玻璃底 + 发丝边;hover 提亮边框/投影;主 CTA 可用签名渐变或实心青(`--aisoc-on-accent` 文字)。
- **侧导航**:活动项 = 青文字 + `inset 2px 0 0 accent` 左信号条 + 极淡青染底;hover 玻璃提亮。
- **状态徽章**:胶囊,语义色 ~12% 底 + ~34% 边 + 语义色文字。
- **KPI count-up**:`useCountUp`(`design/motion.ts`)数字滚动入场,reduced-motion 直接落定终值。
- **live 指示**:emerald 圆点 + 柔和涟漪。
- **图表**:Recharts HUD(`components/charts/*`),渐变填充柱/面积、甜甜圈中心双标签、`HudTooltip` 玻璃提示;经 `useChartTheme()` 双主题重着色。
- **终端主题**:`.terminal-host` 深/浅补丁(如接入 xterm,用 `TERMINAL_THEME_LIGHT`)。

## 7. 动效预算

| 场景 | 规格 |
|---|---|
| 页面/卡片入场 | fade + 上移,~320–340ms ease,stagger |
| hover 反馈 | 140–160ms,颜色/边框/投影 |
| KPI count-up | ~900ms,reduced-motion 落定终值 |
| 氛围漂移 / live | 2.4–2.6s 循环,极低透明度变化 |
| 图表描入 | Recharts 默认动画,reduced-motion 关闭 |
| **全部** | **`prefers-reduced-motion: reduce` 下停用** |

## 8. A2UI 主题传播

聊天生成的 HTML 交付物经 `{theme_color}` 消息参数(`chat/lib/theme.ts` 的 `getThemeMessageArgument()`)带上**提交时的当前主题**(`getActiveTheme()` → `readTheme()`)。契约键 `theme_color` 形状不变,值随深/浅变化。**已渲染的 sandboxed iframe 不追溯重着色**(下一条交付物起生效),可接受。

## 9. 维护方式

- 新增组件一律引用 `--aisoc-*` / `--bg-*` / `--ink-*` / `--accent-*` tokens 或 Tailwind `*-aisoc-*` 工具类,**不写裸色值**;canvas/图表从 `design/tokens.ts` 取色。
- 深色改值只改 `tokens.css :root`;浅色差异放 `:root[data-theme="light"]`;壳/页面浅色补丁放 `hud-overlay.css` 的 `[data-theme="light"]` 段。
- 三寄存器已归一:`styles.css --aisoc-*`(被 tokens.css 覆盖)、`OverviewPageReplica.css`(走 `var()`)、`ontology.css`(删重复 `:root`,语义色收敛到 `.ontology-scope`)。勿再引入并列的 `:root` 基础 token 块。
