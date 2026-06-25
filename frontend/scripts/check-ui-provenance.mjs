#!/usr/bin/env node
/**
 * components/ui 来源门禁。
 *
 * 配合 .cursor/rules/frontend-ui-conventions.mdc 与 add-shadcn-component skill：
 * - shadcn 有对应组件时，必须通过 `pnpm dlx shadcn@latest add <name>` 拉取后适配。
 * - shadcn 没有合适组件时，才允许自写，并在文件头标明「非 shadcn，自写」。
 *
 * 这个脚本不尝试证明源码真的来自 CLI；它强制留下可审计的来源标记，让 review 和
 * agent 自检不再只靠记忆。
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const FRONTEND = fileURLToPath(new URL("..", import.meta.url));
const UI_DIR = join(FRONTEND, "src", "components", "ui");
const HEADER_LINES = 12;

const EXEMPT_FILES = new Set([".gitkeep", "index.ts"]);
const GRANDFATHERED_COMPONENTS = new Set([
  // 这些是门禁建立前已有的 components/ui 目录。后续自然触达时再补来源头，
  // 新增组件不要加入这个列表。
  "badge",
  "button",
  "carousel",
  "collapsible",
  "input",
  "scroll-area",
  "select",
  "separator",
  "sheet",
  "sidebar",
  "skeleton",
  "sonner",
  "status-dot",
  "tabs",
  "text-ellipsis",
  "tooltip",
  "tooltip-button",
]);

function hasValidProvenance(header) {
  const shadcn = /shadcn\/ui|shadcn ui/i.test(header);
  const cli = /pnpm\s+dlx\s+shadcn@latest\s+add/i.test(header);
  const custom = /非\s*shadcn[，,、\s]*自写/i.test(header);
  return (shadcn && cli) || custom;
}

const violations = [];
for (const name of readdirSync(UI_DIR)) {
  if (EXEMPT_FILES.has(name)) continue;
  const path = join(UI_DIR, name);
  const stat = statSync(path);

  if (stat.isFile() && name.endsWith(".tsx")) {
    violations.push({
      file: relative(FRONTEND, path),
      message: "禁止保留平铺 UI 文件；请挪到同名目录 index.tsx，并删除 CLI 生成的平铺残留",
    });
    continue;
  }

  if (!stat.isDirectory() || GRANDFATHERED_COMPONENTS.has(name)) continue;

  const entry = join(path, "index.tsx");
  try {
    const header = readFileSync(entry, "utf8").split("\n").slice(0, HEADER_LINES).join("\n");
    if (!hasValidProvenance(header)) {
      violations.push({
        file: relative(FRONTEND, entry),
        message:
          "缺少 shadcn CLI 来源标记，或缺少「非 shadcn，自写」说明",
      });
    }
  } catch {
    violations.push({
      file: relative(FRONTEND, entry),
      message: "缺少 index.tsx",
    });
  }
}

if (violations.length > 0) {
  console.error("✗ components/ui 来源门禁失败：\n");
  for (const v of violations) {
    console.error(`  ${v.file}`);
    console.error(`    ${v.message}`);
  }
  console.error(
    "\n新增通用 UI 件请走 .cursor/skills/add-shadcn-component/SKILL.md；若确为自写件，文件头必须标明「非 shadcn，自写」及原因。",
  );
  process.exit(1);
}

console.log(
  "ui-provenance-guard: components/ui 来源标记完整 ✓",
);
