// JS/Canvas/图表消费的设计常量(与 tokens.css 保持一致的单一真相源镜像)。
// canvas 与 Recharts 无法读取 CSS 变量,故在此提供 TS 版本。
// 主题:Command Deck / Holographic Ops —— 深空海军蓝 + 青→蓝→紫签名渐变。

export const COLORS = {
  bgAbyss: "#070B13",
  bgDeep: "#0D1420",
  bgPanel: "#111A29",
  bgElevated: "#182236",
  inkHi: "#EAF2FF",
  inkMid: "#93A4C4",
  inkLo: "#5B6B8A",
  // 签名渐变体系:青 → 蓝 → 紫
  cyan: "#22D3EE",
  blue: "#3B82F6",
  blueStrong: "#5FA0FF",
  violet: "#8B5CF6",
  indigo: "#6366F1",
  emerald: "#34D399",
  amber: "#FBBF24",
  rose: "#FB7185",
  // 兼容旧命名(magenta 收敛到渐变紫端)
  magenta: "#A855F7",
  strokeSoft: "rgba(120,180,255,0.10)",
  strokeMid: "rgba(120,180,255,0.20)",
} as const;

// 签名渐变的停靠色(供图表描边/填充构造 <linearGradient>)。
export const GRADIENT_STOPS = ["#22D3EE", "#3B82F6", "#8B5CF6"] as const;

// 语义状态 → 颜色/中文标签。
export type StatusKey = "ok" | "warn" | "crit" | "info" | "idle";
export const STATUS: Record<StatusKey, { color: string; label: string }> = {
  ok: { color: COLORS.emerald, label: "OK" },
  warn: { color: COLORS.amber, label: "Warning" },
  crit: { color: COLORS.rose, label: "Critical" },
  info: { color: COLORS.blue, label: "Info" },
  idle: { color: COLORS.inkLo, label: "Idle" },
};

// 分类型可视化调色板(青/蓝主导冷色系,紫/品红退到末位,去除绿/琥珀等暖色)。
export const CATEGORICAL = [
  COLORS.cyan,
  COLORS.blue,
  COLORS.indigo,
  COLORS.blueStrong,
  COLORS.violet,
  COLORS.magenta,
] as const;

// 浅色主题下加深的分类色板(白底可读:亮青/亮蓝加深为墨青/皇家蓝阶)。
export const CATEGORICAL_LIGHT = [
  "#0891B2", // cyan  → 墨青
  "#2563EB", // blue  → 皇家蓝
  "#4F46E5", // indigo
  "#1D4ED8", // blueStrong → 深蓝
  "#7C3AED", // violet
  "#9333EA", // magenta
] as const;

// 图表 chrome(坐标轴/网格/tooltip/光标/中心字/端点)分主题。
// Recharts 读不到 CSS 变量,故 chrome 也镜像在此,由 useChartTheme() 按主题选取。
export type ChartChrome = {
  axisTickFill: string;
  gridStroke: string;
  axisLineStroke: string;
  tooltipBg: string;
  tooltipBorder: string;
  tooltipShadow: string;
  textHi: string;
  textMid: string;
  cursorFill: string;
  dotCore: string;
  categorical: readonly string[];
  // 语义签名色(柱/线序列直接指定的色):浅色需加深。
  sig: { cyan: string; blue: string; violet: string; indigo: string; magenta: string };
};

export const CHART_CHROME: Record<"dark" | "light", ChartChrome> = {
  dark: {
    axisTickFill: COLORS.inkLo,
    gridStroke: "rgba(120,180,255,0.10)",
    axisLineStroke: "rgba(120,180,255,0.18)",
    tooltipBg: "linear-gradient(155deg, rgba(20,30,52,0.96), rgba(12,18,32,0.94))",
    tooltipBorder: COLORS.strokeMid,
    tooltipShadow: "0 12px 34px rgba(4,8,16,0.6), 0 0 0 1px rgba(34,211,238,0.08)",
    textHi: COLORS.inkHi,
    textMid: COLORS.inkMid,
    cursorFill: "rgba(34,211,238,0.06)",
    dotCore: COLORS.bgAbyss,
    categorical: CATEGORICAL,
    sig: { cyan: COLORS.cyan, blue: COLORS.blue, violet: COLORS.violet, indigo: COLORS.indigo, magenta: COLORS.magenta },
  },
  light: {
    axisTickFill: "#7D8BA8",
    gridStroke: "rgba(15,30,60,0.10)",
    axisLineStroke: "rgba(15,30,60,0.18)",
    tooltipBg: "linear-gradient(155deg, rgba(255,255,255,0.97), rgba(246,249,253,0.98))",
    tooltipBorder: "rgba(15,30,60,0.16)",
    tooltipShadow: "0 14px 34px -18px rgba(15,30,60,0.30), 0 0 0 1px rgba(37,99,235,0.06)",
    textHi: "#0F1E33",
    textMid: "#46587A",
    cursorFill: "rgba(37,99,235,0.08)",
    dotCore: "#FFFFFF",
    categorical: CATEGORICAL_LIGHT,
    sig: { cyan: "#0891B2", blue: "#2563EB", violet: "#7C3AED", indigo: "#4F46E5", magenta: "#9333EA" },
  },
};

export const MOTION = {
  easeOut: "cubic-bezier(0.2,0.7,0.2,1)",
  easeSpring: "cubic-bezier(0.16,1,0.3,1)",
  fast: 140,
  base: 240,
  slow: 420,
  ambient: 2600,
} as const;
