import js from "@eslint/js";
import globals from "globals";
import tseslint from "typescript-eslint";
import react from "eslint-plugin-react";

/**
 * 禁止页面 / 业务代码散写的原生交互件 —— 一律用 components/ui/ 封装件。
 * 这是 .cursor/rules/frontend-ui-conventions.mdc 的机械门禁实现：门禁不认
 * "占位 / MVP / 临时" 理由，见到即报错。components/ui/ 内部豁免（见下方 override）。
 */
const forbiddenNativeElements = ["button", "input", "select", "textarea"].map((element) => ({
  element,
  message:
    "禁止散写原生交互元素，请用 components/ui/ 下的封装件（见 .cursor/rules/frontend-ui-conventions.mdc）。",
}));

export default tseslint.config(
  { ignores: ["dist/**", "src-tauri/**", "node_modules/**", "scripts/**", "public/**", "*.config.{js,ts}"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["src/**/*.{ts,tsx}"],
    plugins: { react },
    languageOptions: { globals: { ...globals.browser } },
    settings: { react: { version: "detect" } },
    rules: {
      "react/forbid-elements": ["error", { forbid: forbiddenNativeElements }],
      "no-restricted-imports": [
        "error",
        {
          paths: [
            {
              name: "radix-ui",
              message:
                "业务代码禁止直接 import radix-ui；请通过 components/ui 封装件使用，或先抽一个项目 UI 封装。",
            },
          ],
        },
      ],
      // 18 · stub 实现 / 占位接口里允许下划线前缀 + caughtErrors 显式 _ 略过
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_", caughtErrorsIgnorePattern: "^_" },
      ],
    },
  },
  {
    // 封装层豁免：ui/ 组件本就要包原生件
    files: ["src/components/ui/**/*.{ts,tsx}"],
    rules: {
      "react/forbid-elements": "off",
      "no-restricted-imports": "off",
    },
  },
);
