import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// 开发服务器把 /api 代理到本地后端，避免 CORS；
// SSE（text/event-stream）由 http-proxy 透传，无需额外配置。
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: process.env.VITE_API_PROXY_TARGET ?? "http://127.0.0.1:8000",
        changeOrigin: true,
        ws: true,
      },
    },
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
