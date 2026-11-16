// Recharts HUD 图表共享内核。
// 关键约束:Recharts / SVG 读不到 CSS 变量,故所有颜色、动画时长/缓动均取自 tokens.ts 这一 TS 真相源。
// 所有组件不依赖 Tailwind —— 即使基座接线回退也能独立成立。
import type { ReactNode } from "react";
import { CHART_CHROME, MOTION, type ChartChrome } from "../../design/tokens";
import { useReducedMotion } from "../../design/motion";
import { useTheme } from "../../design/theme";

/** 数值格式化器类型:图表值 → 展示字符串。 */
export type ValueFormat = (value: number) => string;

export const defaultValueFormat: ValueFormat = (v) =>
  Math.abs(v) >= 1000 ? v.toLocaleString("en-US") : String(v);

/** 紧凑 token 格式(1.2K / 3.4M),与 Overview formatCompactTokens 精神一致。 */
export const compactFormat: ValueFormat = (v) => {
  const n = Math.abs(v);
  if (n >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(v / 1_000).toFixed(1)}K`;
  return String(Math.round(v));
};

/** 动画配置:reduced-motion 时关闭绘入动画,直接落定。 */
export function useChartAnim(): {
  isAnimationActive: boolean;
  animationDuration: number;
  animationEasing: "ease-out";
} {
  const reduced = useReducedMotion();
  return {
    isAnimationActive: !reduced,
    animationDuration: reduced ? 0 : MOTION.slow,
    animationEasing: "ease-out",
  };
}

/**
 * 图表 chrome(轴/网格/tooltip/光标/中心字/序列色)随主题重算。
 * 模块常量在 import 时冻结、读不到运行时主题,故坐标轴样式、网格、tooltip 均改由此 hook 派生。
 * 与 useChartAnim() 并列,各图顶部 `const ct = useChartTheme()`。
 */
export function useChartTheme(): ChartChrome & {
  axisTick: { fill: string; fontSize: number; fontFamily: string };
  axisLine: { stroke: string };
} {
  const theme = useTheme();
  const c = CHART_CHROME[theme] ?? CHART_CHROME.dark;
  return {
    ...c,
    axisTick: { fill: c.axisTickFill, fontSize: 10, fontFamily: "var(--font-mono, monospace)" },
    axisLine: { stroke: c.axisLineStroke },
  };
}

/**
 * 竖直渐变 <defs>:顶亮底透。返回 stops 与 id,供 fill={`url(#${id})`} 引用。
 * 在图表内以 <defs>{gradientStops(...)}</defs> 使用。
 */
export function gradientStops(id: string, color: string, topOpacity = 0.9, bottomOpacity = 0.08) {
  return (
    <linearGradient id={id} x1="0" y1="0" x2="0" y2="1" key={id}>
      <stop offset="0%" stopColor={color} stopOpacity={topOpacity} />
      <stop offset="100%" stopColor={color} stopOpacity={bottomOpacity} />
    </linearGradient>
  );
}

type TooltipEntry = {
  name?: ReactNode;
  value?: number | string;
  color?: string;
  dataKey?: string | number;
  payload?: Record<string, unknown>;
};

/**
 * 深色玻璃 tooltip。作为 <Tooltip content={<HudTooltip />} /> 传入,
 * Recharts 注入 active/payload/label。可选 valueFormat 统一数值格式。
 */
export function HudTooltip({
  active,
  payload,
  label,
  valueFormat = defaultValueFormat,
}: {
  active?: boolean;
  payload?: TooltipEntry[];
  label?: ReactNode;
  valueFormat?: ValueFormat;
}) {
  const ct = useChartTheme();
  if (!active || !payload || payload.length === 0) return null;
  return (
    <div
      style={{
        background: ct.tooltipBg,
        border: `1px solid ${ct.tooltipBorder}`,
        borderRadius: 10,
        padding: "8px 11px",
        boxShadow: ct.tooltipShadow,
        backdropFilter: "blur(12px)",
        fontFamily: "var(--font-mono, monospace)",
        fontSize: 12,
        minWidth: 120,
      }}
    >
      {label !== undefined && label !== "" && (
        <div style={{ color: ct.textMid, marginBottom: 6, letterSpacing: "0.04em" }}>{label}</div>
      )}
      {payload.map((entry, i) => {
        const v = typeof entry.value === "number" ? valueFormat(entry.value) : entry.value;
        return (
          <div
            key={`${entry.dataKey ?? i}`}
            style={{ display: "flex", alignItems: "center", gap: 8, lineHeight: 1.7 }}
          >
            <span
              style={{
                width: 8,
                height: 8,
                borderRadius: 2,
                background: entry.color,
                boxShadow: `0 0 6px ${entry.color}`,
                flexShrink: 0,
              }}
            />
            <span style={{ color: ct.textMid, flex: 1 }}>{entry.name}</span>
            <span style={{ color: ct.textHi, fontWeight: 600 }}>{v}</span>
          </div>
        );
      })}
    </div>
  );
}
