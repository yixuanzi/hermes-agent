/**
 * Command Deck 环境层(全局动态光效背景)。
 * 深空底 + 多团缓慢游移的柔和发光光晕(青/蓝/紫,screen 混合)
 * + 极淡扫描网格 + 暗角/顶光。纯 CSS(见 hud-overlay.css 的 .tac-* 规则),零 JS 开销。
 * 叠在最底层(pointer-events:none),内容在其上。动效守 prefers-reduced-motion。
 */
export function HudGrid() {
  return (
    <div aria-hidden className="tac-bg pointer-events-none">
      <div className="tac-halo tac-halo-1" />
      <div className="tac-halo tac-halo-2" />
      <div className="tac-halo tac-halo-3" />
      <div className="tac-halo tac-halo-4" />
      <div className="tac-halo tac-halo-5" />
      <div className="tac-bg-grid" />
      <div className="tac-bg-vignette" />
      <div className="tac-bg-topglow" />
    </div>
  );
}
