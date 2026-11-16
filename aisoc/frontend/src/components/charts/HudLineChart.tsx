// HUD 折线图(端点辉光)。多序列。
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import {
  HudTooltip,
  compactFormat,
  useChartAnim,
  useChartTheme,
  type ValueFormat,
} from "./hudChartInternals";

export type LineSeries = { key: string; name: string; color: string };

export function HudLineChart({
  data,
  xKey,
  series,
  height = 220,
  valueFormat = compactFormat,
}: {
  data: Array<Record<string, number | string>>;
  xKey: string;
  series: LineSeries[];
  height?: number;
  valueFormat?: ValueFormat;
}) {
  const anim = useChartAnim();
  const ct = useChartTheme();
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 12, right: 8, left: 4, bottom: 4 }}>
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
          <Line
            key={s.key}
            type="monotone"
            dataKey={s.key}
            name={s.name}
            stroke={s.color}
            strokeWidth={2}
            dot={{ r: 2.4, fill: s.color, strokeWidth: 0 }}
            activeDot={{ r: 4, fill: ct.dotCore, stroke: s.color, strokeWidth: 2 }}
            isAnimationActive={anim.isAnimationActive}
            animationDuration={anim.animationDuration}
            animationEasing={anim.animationEasing}
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}
