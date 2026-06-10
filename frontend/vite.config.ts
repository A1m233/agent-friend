import { defineConfig } from "vite";
import { resolve } from "node:path";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Tauri 期望前端 dev server 端口固定（与 tauri.conf.json devUrl 对齐）。
const host = process.env.TAURI_DEV_HOST;

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": resolve(__dirname, "src") },
  },
  // 多窗口 = 多 HTML entry（单 build 多 entry）
  build: {
    rollupOptions: {
      input: {
        index: resolve(__dirname, "index.html"),
        pet: resolve(__dirname, "pet.html"),
        chat: resolve(__dirname, "chat.html"),
      },
    },
  },
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    host: host || false,
    hmr: host
      ? { protocol: "ws", host, port: 1421 }
      : undefined,
    watch: { ignored: ["**/src-tauri/**"] },
  },
});
