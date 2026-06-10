import { defineConfig } from "vitest/config";
import { resolve } from "node:path";

// 单测配置：复用 `@` 别名（与 vite.config.ts 一致），node 环境跑纯逻辑单测。
export default defineConfig({
  resolve: {
    alias: { "@": resolve(__dirname, "src") },
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
