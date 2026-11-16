import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { App } from "./App";
// 加载顺序即层叠顺序:styles.css(@theme inline + 遗留语义类 + @layer)先落地,
// design/tokens.css 随后重定义 :root --aisoc-* 别名(换值不换名)→ 全站换肤 + 双主题,
// base.css 补 Command Deck 工具类/关键帧(无 Preflight),hud-overlay.css 收尾外壳质感 + 氛围 + 浅色补丁。
import "./styles.css";
import "./design/tokens.css";
import "./design/base.css";
import "./design/shell.css";
import "./design/hud-overlay.css";

const container = document.getElementById("root");
if (!container) {
  throw new Error("Missing root element");
}

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: false, refetchOnWindowFocus: false, staleTime: 30_000 },
  },
});

createRoot(container).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
