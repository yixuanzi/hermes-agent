import { useEffect, useState } from "react";

// 主题:深色(默认)/ 浅色。真相源是 <html data-theme>,由 index.html 首帧脚本落定。
export type ThemeMode = "dark" | "light";

export const THEME_STORAGE_KEY = "aisoc_theme";
export const THEME_EVENT = "aisoc:themechange";

/** 读取当前主题(SSR 守卫,默认 dark)。 */
export function readTheme(): ThemeMode {
  if (typeof document === "undefined") return "dark";
  return document.documentElement.dataset.theme === "light" ? "light" : "dark";
}

/** 应用主题:写 <html data-theme> + localStorage + 派发事件(供图表等 JS 感知)。 */
export function applyTheme(mode: ThemeMode): void {
  if (typeof document !== "undefined") {
    document.documentElement.dataset.theme = mode;
  }
  try {
    localStorage.setItem(THEME_STORAGE_KEY, mode);
  } catch {
    /* localStorage 不可用时静默降级 */
  }
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent(THEME_EVENT, { detail: mode }));
  }
}

/** 在深/浅之间翻转当前主题,返回切换后的模式。 */
export function toggleTheme(): ThemeMode {
  const next: ThemeMode = readTheme() === "light" ? "dark" : "light";
  applyTheme(next);
  return next;
}

/**
 * 订阅当前主题。监听自定义事件 + MutationObserver 兜底(直接改 data-theme 时也能感知)。
 * Recharts/canvas 等读不到 CSS 变量的场景据此重算色板。
 */
export function useTheme(): ThemeMode {
  const [theme, setTheme] = useState<ThemeMode>(readTheme);

  useEffect(() => {
    const sync = () => setTheme(readTheme());
    window.addEventListener(THEME_EVENT, sync);
    const mo = new MutationObserver(sync);
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    // 挂载后校正一次(首帧脚本可能在 hydration 前已改)。
    sync();
    return () => {
      window.removeEventListener(THEME_EVENT, sync);
      mo.disconnect();
    };
  }, []);

  return theme;
}
