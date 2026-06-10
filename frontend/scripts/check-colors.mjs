#!/usr/bin/env node
/**
 * 颜色硬编码门禁（机械版）—— 配合 .cursor/rules/frontend-ui-conventions.mdc §颜色。
 *
 * 背景：规则文档禁止硬编码颜色，但此前只有"原生交互件"那条接了 ESLint 门禁，颜色这条
 * 纯靠自觉、会失效。本脚本把它变成 scripts/check 的机械门禁：扫描 src/ 下的 ts/tsx/css，
 * 命中 Tailwind 内置色板类 / #hex / rgb()/hsl() / 任意值色即报错退出。
 *
 * 豁免：src/styles/theme/ 是颜色定义源头（CSS 变量的唯一落点），不扫。
 * 单实现（node）跨平台，由 scripts/frontend/lint.{sh,ps1} 调用，语义两端一致。
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { extname, join } from "node:path";
import { fileURLToPath } from "node:url";

const FRONTEND = fileURLToPath(new URL("..", import.meta.url));
const SRC = join(FRONTEND, "src");
const EXCLUDE_DIRS = new Set([join(SRC, "styles", "theme")]);
const EXTS = new Set([".ts", ".tsx", ".css"]);

const PREFIX =
  "bg|text|border|ring|ring-offset|from|via|to|fill|stroke|decoration|outline|shadow|divide|placeholder|caret|accent";
const COLOR =
  "slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose";
const SHADE = "50|100|200|300|400|500|600|700|800|900|950";

const RULES = [
  { name: "tailwind 色板类", re: new RegExp(`\\b(?:${PREFIX})-(?:${COLOR})-(?:${SHADE})\\b`) },
  { name: "任意值色 [...]", re: /\[(?:#[0-9a-fA-F]{3,8}|(?:rgb|rgba|hsl|hsla)\()/i },
  // hex 颜色（3/4/6/8 位），排除 JS 私有字段 this.#x 与标识符内的 #
  { name: "hex 颜色", re: /(?<![\w.])#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{4}|[0-9a-fA-F]{3})\b/ },
  { name: "rgb()/hsl() 函数", re: /\b(?:rgb|rgba|hsl|hsla)\(/i },
];

function collectFiles(dir, out) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) {
      if (!EXCLUDE_DIRS.has(p)) collectFiles(p, out);
    } else if (EXTS.has(extname(name))) {
      out.push(p);
    }
  }
}

const files = [];
collectFiles(SRC, files);

const violations = [];
for (const file of files) {
  const lines = readFileSync(file, "utf8").split("\n");
  lines.forEach((line, i) => {
    for (const rule of RULES) {
      const m = rule.re.exec(line);
      if (m) {
        violations.push({ file, line: i + 1, rule: rule.name, text: line.trim() });
        break;
      }
    }
  });
}

if (violations.length > 0) {
  console.error("✗ 发现硬编码颜色（违反 frontend-ui-conventions §颜色）：\n");
  for (const v of violations) {
    const rel = v.file.slice(FRONTEND.length);
    console.error(`  ${rel}:${v.line}  [${v.rule}]`);
    console.error(`    ${v.text}`);
  }
  console.error(
    "\n请改用 src/styles/theme/ 下的 CSS 变量 / 映射 token（新增颜色需先在所有主题文件补同名变量）。",
  );
  process.exit(1);
}

console.log(`color-guard: ${files.length} 个文件，无硬编码颜色 ✓`);
