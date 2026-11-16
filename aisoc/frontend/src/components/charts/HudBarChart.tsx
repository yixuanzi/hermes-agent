// HUD 分组柱状图(可叠加总量折线)。用 ComposedChart 承载柱 + 线。
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  HudTooltip,
  compactFormat,
  gradientStops,
  useChartAnim,
  useChartTheme,
  type ValueFormat,
} from "./hudChartInternals";

export type BarSeries = { key: string; name: string; color: string };
export type OverlaySeries = { key: string; name: string; color: string };

export function HudBarChart({
  data,
  xKey,
  bars,
  overlayLine,
  height = 240,
  valueFormat = compactFormat,
}: {
  data: Array<Record<string, number | string>>;
  xKey: string;
  bars: BarSeries[];
  overlayLine?: OverlaySeries;
  height?: number;
  valueFormat?: ValueFormat;
}) {
  const anim = useChartAnim();
  const ct = useChartTheme();

  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart data={data} margin={{ top: 12, right: 8, left: 4, bottom: 4 }} barGap={2} barCategoryGap="22%">
        <defs>{bars.map((b) => gradientStops(`barGrad-${b.key}`, b.color))}</defs>
        <CartesianGrid stroke={ct.gridStroke} vertical={false} />
        <XAxis dataKey={xKey} tick={ct.axisTick} axisLine={ct.axisLine} tickLine={false} minTickGap={8} />
        <YAxis
          tick={ct.axisTick}
          axisLine={false}
          tickLine={false}
          width={40}
          tickFormatter={(v: number) => valueFormat(v)}
        />
        <Tooltip
          cursor={{ fill: ct.cursorFill }}
          content={<HudTooltip valueFormat={valueFormat} />}
        />
        {bars.map((b) => (
          <Bar
            key={b.key}
            dataKey={b.key}
            name={b.name}
            fill={`url(#barGrad-${b.key})`}
            stroke={b.color}
            strokeOpacity={0.5}
            radius={[3, 3, 0, 0]}
            isAnimationActive={anim.isAnimationActive}
            animationDuration={anim.animationDuration}
            animationEasing={anim.animationEasing}
          />
        ))}
        {overlayLine && (
          <Line
            type="monotone"
            dataKey={overlayLine.key}
            name={overlayLine.name}
            stroke={overlayLine.color}
            strokeWidth={2}
            dot={{ r: 2.6, fill: overlayLine.color, strokeWidth: 0 }}
            activeDot={{ r: 4, fill: ct.dotCore, stroke: overlayLine.color, strokeWidth: 2 }}
            isAnimationActive={anim.isAnimationActive}
            animationDuration={anim.animationDuration}
            animationEasing={anim.animationEasing}
          />
        )}
      </ComposedChart>
    </ResponsiveContainer>
  );
}
