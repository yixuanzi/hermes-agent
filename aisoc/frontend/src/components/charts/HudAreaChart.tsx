// HUD 面积图(渐变填充)。支持多序列堆叠。
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import {
  HudTooltip,
  compactFormat,
  gradientStops,
  useChartAnim,
  useChartTheme,
  type ValueFormat,
} from "./hudChartInternals";

export type AreaSeries = { key: string; name: string; color: string };

export function HudAreaChart({
  data,
  xKey,
  series,
  height = 220,
  stacked = false,
  valueFormat = compactFormat,
}: {
  data: Array<Record<string, number | string>>;
  xKey: string;
  series: AreaSeries[];
  height?: number;
  stacked?: boolean;
  valueFormat?: ValueFormat;
}) {
  const anim = useChartAnim();
  const ct = useChartTheme();
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 12, right: 8, left: 4, bottom: 4 }}>
        <defs>{series.map((s) => gradientStops(`areaGrad-${s.key}`, s.color, 0.55, 0.02))}</defs>
        <CartesianGrid stroke={ct.gridStroke} vertical={false} />
        <XAxis dataKey={xKey} tick={ct.axisTick} axisLine={ct.axisLine} tickLine={false} minTickGap={8} />
        <YAxis
          tick={ct.axisTick}
          axisLine={false}
          tickLine={false}
          width={40}
          tickFormatter={(v: number) => valueFormat(v)}
        />
        <Tooltip content={<HudTooltip valueFormat={valueFormat} />} />
        {series.map((s) => (
          <Area
            key={s.key}
            type="monotone"
            dataKey={s.key}
            name={s.name}
            stackId={stacked ? "stack" : undefined}
            stroke={s.color}
            strokeWidth={2}
            fill={`url(#areaGrad-${s.key})`}
            dot={false}
            activeDot={{ r: 4, fill: s.color, strokeWidth: 0 }}
            isAnimationActive={anim.isAnimationActive}
            animationDuration={anim.animationDuration}
            animationEasing={anim.animationEasing}
          />
        ))}
      </AreaChart>
    </ResponsiveContainer>
  );
}
