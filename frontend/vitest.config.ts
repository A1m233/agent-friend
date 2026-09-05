import { defineConfig } from "vitest/config";
import { resolve } from "node:path";

// 单测配置：复用 `@` 别名（与 vite.config.ts 一致），node 环境跑纯逻辑单测。
export default defineConfig({
  resolve: {
    alias: {
      "@": resolve(__dirname, "src"),
      // This package only declares `module`; SSR must resolve its entry even
      // when a timeline test replaces the custom-element renderer with a mock.
      "@tdesign-react/chat": resolve(__dirname, "node_modules/@tdesign-react/chat/es/index.js"),
    },
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
