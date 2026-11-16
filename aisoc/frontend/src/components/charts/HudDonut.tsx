// HUD 环形图。支持扇区间隙(paddingAngle)、圆角、逐片色与中心双行标签。
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";
import { HudTooltip, defaultValueFormat, useChartAnim, useChartTheme, type ValueFormat } from "./hudChartInternals";

export type DonutDatum = { name: string; value: number; color?: string };

export function HudDonut({
  data,
  size = 180,
  thickness = 26,
  gap = 0,
  centerPrimary,
  centerSecondary,
  valueFormat = defaultValueFormat,
}: {
  data: DonutDatum[];
  size?: number;
  /** 环宽(outer - inner)。 */
  thickness?: number;
  /** 扇区间隙角度(度)。 */
  gap?: number;
  centerPrimary?: string;
  centerSecondary?: string;
  valueFormat?: ValueFormat;
}) {
  const anim = useChartAnim();
  const ct = useChartTheme();
  const outer = size / 2 - 2;
  const inner = outer - thickness;

  return (
    <div style={{ position: "relative", width: size, height: size }}>
      <ResponsiveContainer width="100%" height="100%">
        <PieChart>
          <Tooltip content={<HudTooltip valueFormat={valueFormat} />} />
          <Pie
            data={data}
            dataKey="value"
            nameKey="name"
            cx="50%"
            cy="50%"
            innerRadius={inner}
            outerRadius={outer}
            paddingAngle={gap}
            cornerRadius={gap > 0 ? 3 : 0}
            stroke="none"
            isAnimationActive={anim.isAnimationActive}
            animationDuration={anim.animationDuration}
            animationEasing={anim.animationEasing}
          >
            {data.map((d, i) => {
              const c = d.color ?? ct.categorical[i % ct.categorical.length];
              return <Cell key={d.name} fill={c} style={{ filter: `drop-shadow(0 0 4px ${c}66)` }} />;
            })}
          </Pie>
        </PieChart>
      </ResponsiveContainer>
      {(centerPrimary || centerSecondary) && (
        <div
          style={{
            position: "absolute",
            inset: 0,
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            pointerEvents: "none",
            textAlign: "center",
          }}
        >
          {centerPrimary && (
            <span
              style={{
                fontFamily: "var(--font-display, var(--font-mono, monospace))",
                fontSize: Math.round(size * 0.15),
                fontWeight: 700,
                color: ct.textHi,
                lineHeight: 1,
                textShadow: `0 0 16px ${ct.sig.cyan}55`,
              }}
            >
              {centerPrimary}
            </span>
          )}
          {centerSecondary && (
            <span
              style={{
                fontFamily: "var(--font-mono, monospace)",
                fontSize: 10,
                letterSpacing: "0.18em",
                color: ct.axisTickFill,
                marginTop: 4,
                textTransform: "uppercase",
              }}
            >
              {centerSecondary}
            </span>
          )}
        </div>
      )}
    </div>
  );
}
