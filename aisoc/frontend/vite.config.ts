import { resolve } from "node:path";

import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      // Dev-mode API + WebSocket passthrough to the AISOC backend.
      "/api": {
        target: "http://127.0.0.1:9120",
        changeOrigin: true,
        ws: true,
      },
    },
  },
  build: {
    outDir: resolve(__dirname, "../backend/web_dist"),
    emptyOutDir: true,
  },
});

