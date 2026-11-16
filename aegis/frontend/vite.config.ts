import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import path from 'path';
import {defineConfig} from 'vite';

export default defineConfig(() => {
  return {
    plugins: [react(), tailwindcss()],
    build: {
      outDir: path.resolve(__dirname, '../backend/web_dist'),
      emptyOutDir: true,
    },
    test: {
      environment: 'jsdom',
      setupFiles: './src/test/setup.ts',
    },
    resolve: {
      alias: {
        '@': path.resolve(__dirname, '.'),
      },
    },
    server: {
      port: 3000,
      host: '0.0.0.0',
      proxy: {
        '/api': {
          target: 'http://127.0.0.1:9130',
          changeOrigin: true,
        },
        '/health': {
          target: 'http://127.0.0.1:9130',
          changeOrigin: true,
        },
      },
      // Allow remote editing sessions to disable HMR/file watching when needed.
      hmr: process.env.DISABLE_HMR !== 'true',
      // Reduce CPU churn during automated edits when HMR is turned off.
      watch: process.env.DISABLE_HMR === 'true' ? null : {},
    },
  };
});
