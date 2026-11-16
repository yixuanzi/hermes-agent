/**
 * AISOC 品牌徽标 —— "环带数据行星(Ringed Data Planet)"。
 * 立体渐变球体(青→蓝→紫 + 球面高光 + 晨昏亮缘)+ 一圈倾斜光环穿插绕球 + 三颗错落星芒:
 * 以「有引力、被环带守护的行星」隐喻 AISOC 对安全域的持续感知与掌控。
 * 纯内联 SVG,任意尺寸清晰缩放;行星漂浮 / 光环能量流动 / 星芒闪烁动效由 hud-overlay.css 驱动
 * (守 prefers-reduced-motion)。路径由 ellipseArc/ sparkPath 一次性生成,几何与设计预览逐点一致。
 */

const TAU = Math.PI * 2;

/** 采样生成(可旋转)椭圆弧折线,规避 SVG A 命令的 large-arc/sweep flag 歧义。 */
function ellipseArc(
  cx: number,
  cy: number,
  rx: number,
  ry: number,
  rotDeg: number,
  t0: number,
  t1: number,
  n: number,
  close = false,
): string {
  const rot = (rotDeg * Math.PI) / 180;
  let d = "";
  for (let i = 0; i <= n; i++) {
    const t = t0 + ((t1 - t0) * i) / n;
    const ex = rx * Math.cos(t);
    const ey = ry * Math.sin(t);
    const x = cx + ex * Math.cos(rot) - ey * Math.sin(rot);
    const y = cy + ex * Math.sin(rot) + ey * Math.cos(rot);
    d += `${i === 0 ? "M" : "L"}${x.toFixed(2)} ${y.toFixed(2)} `;
  }
  return d.trim() + (close ? " Z" : "");
}

/** 四角星芒。 */
function sparkPath(px: number, py: number, s: number): string {
  const t = +(s * 0.3).toFixed(2);
  return (
    `M${px} ${py - s}` +
    `L${px + t} ${py - t}` +
    `L${px + s} ${py}` +
    `L${px + t} ${py + t}` +
    `L${px} ${py + s}` +
    `L${px - t} ${py + t}` +
    `L${px - s} ${py}` +
    `L${px - t} ${py - t}Z`
  );
}

const CX = 24;
const CY = 24;
const R = 10.6; // 行星半径
const RX = 18.5; // 光环长半轴
const RY = 6.2; // 光环短半轴
const TILT = -20; // 光环倾角

const RING_BACK = ellipseArc(CX, CY, RX, RY, TILT, 0, TAU, 72, true); // 完整环(后方,压在球后)
const RING_FRONT = ellipseArc(CX, CY, RX, RY, TILT, Math.PI, TAU, 48); // 前弧(穿到球前)
const EDGE_ARC = ellipseArc(CX, CY, R, R, 0, -Math.PI * 0.55, -Math.PI * 0.02, 20); // 晨昏亮缘

export function BrandMark({ size = 40, className = "" }: { size?: number; className?: string }) {
  const uid = `bm${size}`;
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 48 48"
      fill="none"
      className={`brandmark ${className}`.trim()}
      aria-hidden="true"
      focusable="false"
    >
      <defs>
        {/* 球体底色:左上亮紫 → 中靛 → 右下深蓝 */}
        <linearGradient id={`${uid}-body`} x1="15" y1="14" x2="33" y2="34" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#a855f7" />
          <stop offset="0.5" stopColor="#6366f1" />
          <stop offset="1" stopColor="#1e3a8a" />
        </linearGradient>
        {/* 球面高光:左上青白光泽,营造体积 */}
        <radialGradient id={`${uid}-lit`} cx="0.34" cy="0.3" r="0.72">
          <stop offset="0" stopColor="#eafcff" stopOpacity="0.7" />
          <stop offset="0.45" stopColor="#7dd3fc" stopOpacity="0.18" />
          <stop offset="1" stopColor="#000000" stopOpacity="0" />
        </radialGradient>
        {/* 光环:紫 → 蓝 → 青签名渐变(stop 带 class 钩子,供 light 主题加深青端) */}
        <linearGradient id={`${uid}-ring`} x1="6" y1="30" x2="42" y2="16" gradientUnits="userSpaceOnUse">
          <stop className="bm-ring-s0" offset="0" stopColor="#8b5cf6" />
          <stop className="bm-ring-s1" offset="0.5" stopColor="#3b82f6" />
          <stop className="bm-ring-s2" offset="1" stopColor="#22d3ee" />
        </linearGradient>
      </defs>

      {/* 星芒(背景层,各自错峰闪烁) */}
      <g fill="#eafcff">
        <path className="brandmark-spark1" d={sparkPath(9.5, 11, 2.3)} />
        <path className="brandmark-spark2" d={sparkPath(40, 12.5, 1.7)} opacity="0.9" />
        <path className="brandmark-spark3" d={sparkPath(38.5, 34, 1.9)} />
      </g>

      {/* 行星整体缓慢漂浮 */}
      <g className="brandmark-bob">
        {/* 光环后半(压在球后) */}
        <path className="brandmark-ring" d={RING_BACK} fill="none" stroke={`url(#${uid}-ring)`} strokeWidth="1.7" opacity="0.55" />

        {/* 行星球体:底色 + 高光 + 晨昏亮缘 */}
        <circle cx={CX} cy={CY} r={R} fill={`url(#${uid}-body)`} />
        <circle cx={CX} cy={CY} r={R} fill={`url(#${uid}-lit)`} />
        <path d={EDGE_ARC} fill="none" stroke="#eafcff" strokeWidth="1.1" strokeLinecap="round" opacity="0.75" />

        {/* 光环前半(穿到球前)+ 沿环流动的亮段 */}
        <path className="brandmark-ring" d={RING_FRONT} fill="none" stroke={`url(#${uid}-ring)`} strokeWidth="2.1" strokeLinecap="round" />
        <path
          className="brandmark-shine"
          d={RING_FRONT}
          fill="none"
          stroke="#eafcff"
          strokeWidth="2.1"
          strokeLinecap="round"
          pathLength={100}
          strokeDasharray="14 86"
        />
      </g>
    </svg>
  );
}
