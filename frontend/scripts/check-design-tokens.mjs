#!/usr/bin/env node
/**
 * 设计 token 硬编码综合门禁（机械版）—— 配合 .cursor/rules/frontend-ui-conventions.mdc §设计 token。
 *
 * 扫描 frontend/src/ 下的 ts/tsx/css，命中字号 / 字重 / 行高 / 字间距 / 间距 / 圆角 / 阴影
 * 维度的 Tailwind arbitrary value 或 CSS 裸字面值即报错退出。
 *
 * 豁免：
 * - src/styles/theme/ —— token 定义源头，硬编码是必然（与 color-guard 一致）。
 * - src/components/ui/ —— shadcn vendored 区域；其内部 arbitrary value 多为几何 calc 与组件专属 magic，
 *   不是项目级设计常量，改动会与上游 CLI 更新冲突。
 *
 * 单实现（node ESM）跨平台，由 scripts/frontend/lint.{sh,ps1} 调用，语义两端一致。
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { extname, join } from "node:path";
import { fileURLToPath } from "node:url";

const FRONTEND = fileURLToPath(new URL("..", import.meta.url));
const SRC = join(FRONTEND, "src");
const EXCLUDE_DIRS = new Set([
  join(SRC, "styles", "theme"),
  join(SRC, "components", "ui"),
]);
const EXTS = new Set([".ts", ".tsx", ".css"]);

// 白名单：命中 arbitrary value 内容时跳过（几何计算 / 视口单位 / 已用变量 / 特殊关键字）。
const ARB_WHITELIST_RE = /calc\(|min\(|max\(|clamp\(|var\(--|%|vh|vw|dvh|dvw|inherit|auto|100%/i;

const RULES = [
  {
    name: "字号 arbitrary (text-[...])",
    re: /\btext-\[(?!.*\b(?:calc\(|min\(|max\(|clamp\(|var\(--|%|vh|vw|dvh|dvw|inherit|auto|100%))[^\]]+\]/,
  },
  { name: "字重 arbitrary (font-[N])", re: /\bfont-\[\d+\]/ },
  {
    name: "行高 arbitrary (leading-[...])",
    re: /\bleading-\[(?!.*\b(?:calc\(|min\(|max\(|clamp\(|var\(--))[^\]]+\]/,
  },
  {
    name: "字间距 arbitrary (tracking-[...])",
    re: /\btracking-\[(?!.*\b(?:calc\(|min\(|max\(|clamp\(|var\(--))[^\]]+\]/,
  },
  {
    name: "间距 arbitrary (p/m/gap/space-[...])",
    re: /\b(?:p|m|gap|px|py|pt|pr|pb|pl|mx|my|mt|mr|mb|ml|space-x|space-y)-\[(?!.*\b(?:calc\(|min\(|max\(|clamp\(|var\(--|%))[^\]]+\]/,
  },
  {
    name: "圆角 arbitrary (rounded-[...])",
    re: /\brounded(?:-[a-z]+)?-\[(?!.*\b(?:calc\(|min\(|max\(|clamp\(|var\(--))[^\]]+\]/,
  },
  // shadow arbitrary 几乎无合理用法，不开白名单。
  { name: "阴影 arbitrary (shadow-[...])", re: /\bshadow-\[[^\]]+\]/ },
  // CSS 裸字面值：字体栈 / 字号 / 字重 / 行高 / 字间距 / 间距 / 圆角 / 阴影。
  {
    name: "CSS 字体栈裸字面值",
    re: /font-family\s*:\s*(?!.*var\()[^;]+;/,
  },
  {
    name: "CSS token 裸字面值",
    re: /(?:font-size|font-weight|line-height|letter-spacing|padding|margin|gap|border-radius|box-shadow)\s*:\s*(?!.*var\()[^;]*\d+(?:px|rem|em)/,
  },
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
  console.error("✗ 发现硬编码设计 token（违反 frontend-ui-conventions §设计 token）：\n");
  for (const v of violations) {
    const rel = v.file.slice(FRONTEND.length);
    console.error(`  ${rel}:${v.line}  [${v.rule}]`);
    console.error(`    ${v.text}`);
  }
  console.error(
    "\n请改用 src/styles/theme/ 下的 CSS 变量 / 映射 token（新增 token 需在 index.css @theme inline 同步映射）。",
  );
  process.exit(1);
}

console.log(`design-token-guard: ${files.length} 个文件，无硬编码设计 token ✓`);
