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
        bubble: resolve(__dirname, "bubble.html"),
        settings: resolve(__dirname, "settings.html"),
        "voice-call": resolve(__dirname, "voice-call.html"),
        "memory-inspector": resolve(__dirname, "memory-inspector.html"),
        "live2d-debugger": resolve(__dirname, "live2d-debugger.html"),
      },
    },
  },
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    host: host || false,
    // overlay: false —— 桌宠是全屏 layer，编译报错弹窗会盖住一切且关不掉；
    // 关了之后错误仍在 terminal / 浏览器 console 输出，不影响排错
    hmr: host
      ? { protocol: "ws", host, port: 1421, overlay: false }
      : { overlay: false },
    watch: { ignored: ["**/src-tauri/**"] },
    // bridge 无 CORS 头（本期零侵入不给它加），故前端走同源相对路径，由这里代理到
    // bridge（127.0.0.1:18800）。web 调试与 tauri dev 都经 vite，故都免 CORS。
    proxy: {
      "/v1": { target: "http://127.0.0.1:18800", changeOrigin: true },
      "/healthz": { target: "http://127.0.0.1:18800", changeOrigin: true },
      "^/voice(/|$)": { target: "http://127.0.0.1:18900", changeOrigin: true, ws: true },
      "/ag-ui": {
        target: "http://127.0.0.1:18800",
        changeOrigin: true,
        // SSE：禁用代理缓冲，保证流式逐字下发。
        configure: (proxy) => {
          proxy.on("proxyReq", (proxyReq) => {
            proxyReq.setHeader("Accept-Encoding", "identity");
          });
        },
      },
    },
  },
});
