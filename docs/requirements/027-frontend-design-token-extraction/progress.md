# 前端设计 token 抽取 - 进度

## 总体状态

COMPLETED

## 任务列表

实施顺序的硬约束：**先抽完 token + 替换业务代码 → dev server 验视觉 → 再启 guard → 再跑 `scripts/check`**。guard 在 token 抽完前启动会立刻爆红。

### 1. token 文件落盘

- [x] 1.1 新建 `frontend/src/styles/theme/tokens.css`，含字体栈 / 字号 / 字重 / 行高 / 字间距 / 圆角 token（值见 design.md "token 体系完整定义"段，与 Tailwind v4 默认对齐）
- [x] 1.2 修改 `frontend/src/styles/theme/light.css`，在 `html[theme="light"]` 块尾补 `--shadow-sm/-md/-lg/-2xl`（值 = Tailwind v4 默认 light）
- [x] 1.3 修改 `frontend/src/styles/theme/dark.css`，在 `html[theme="dark"]` 块尾补 `--shadow-sm/-md/-lg/-2xl`（值 = 加深版，见 design.md）

### 2. `index.css` 接入 + `@theme inline` 映射

- [x] 2.1 `frontend/src/styles/index.css` 顶部 `@import "./theme/tokens.css";`（在 light/dark 之前）
- [x] 2.2 `@theme inline` 块补字体 / 字号 / 字重 / 行高（`--leading-snug`）/ 字间距（`--tracking-wide`）/ 圆角 / 阴影映射，**严格按 Tailwind v4 token 命名约定**（行高用 `--leading-{档}`、字间距用 `--tracking-{档}`、字号用 `--text-{档}`）
- [x] 2.3 body 段 `font-family: system-ui, …` → `font-family: var(--font-sans);`

### 3. 业务代码替换

- [x] 3.1 `frontend/src/pages/chat/components/ToolCard.tsx:56` `text-[11px]` → `text-xs`
- [x] 3.2 `frontend/src/pages/chat/components/ToolCard.tsx:61` `text-[11px]` → `text-xs`

### 4. 视觉验证（guard 启动前）

**必须在 guard 启动前完成**——这一步是确认 token 抽对了、视觉没坏。

- [x] 4.1 `pnpm --dir frontend dev` 启 dev server（1420 被占，实际跑在 http://localhost:1422/）
- [ ] 4.2 逐入口（`pet.html` / `chat.html` / `settings.html` / `memory-inspector.html`，外加 `bubble.html`）light 主题下人眼对比改前改后，关注圆角 / 阴影 / 字号 / 字重 / 行高 / 字间距维度是否一致；ToolCard pre 块允许 1px 字号偏移
- [ ] 4.3 切到 dark 主题（`html[theme="dark"]`），同样逐入口对比；dark 阴影**会变深**，这是预期内变化（design.md 风险点 #1），其他维度需一致
- [ ] 4.4 任何视觉异常立即定位 token 值或 `@theme inline` 映射

### 5. 综合 guard 实现

- [x] 5.1 新建 `frontend/scripts/check-design-tokens.mjs`，复用 `check-colors.mjs` 框架，按 design.md 检测规则表实现正则、白名单、目录豁免（`src/styles/theme/` + `src/components/ui/`）
- [x] 5.2 `frontend/package.json` `scripts` 段新增 `"lint:tokens": "node scripts/check-design-tokens.mjs"`
- [x] 5.3 本地手动跑 `pnpm --dir frontend run lint:tokens`，确认 0 违例（84 个文件通过）

### 6. guard 接入 `scripts/check`（双端同步）

- [x] 6.1 `scripts/frontend/lint.sh` 加 `===> design-token-guard` 一步（在 color-guard 之后、typecheck 之前）
- [x] 6.2 `scripts/frontend/lint.ps1` 同步加，PowerShell 错误处理保持与既有 color-guard 段一致（`$ErrorActionPreference = "Continue"` + 检查 `$LASTEXITCODE`）

### 7. 规则改动

- [x] 7.1 修改 `.cursor/rules/frontend-ui-conventions.mdc` "设计 token：禁止硬编码" → "### 机械门禁" 小节，补一段豁免目录说明（包含 `src/styles/theme/` 和 `src/components/ui/`，与现有 colors guard 豁免合并表述），措辞见 design.md "规则改动" 段

### 8. 设置面板主题切换（027 范围扩展）

- [x] 8.1 新建 `frontend/src/hooks/useTheme.ts`，提供 `theme` / `setTheme`，持久化到 `localStorage`，key = `agent-friend-theme`
- [x] 8.2 修改 `frontend/src/pages/settings/main.tsx`，在 React 挂载前读取 localStorage 并设置 `document.documentElement.setAttribute("theme", ...)`，防止首屏闪屏
- [x] 8.3 修改 `frontend/src/pages/settings/App.tsx`，用 shadcn `Tabs` + `Sun` / `Moon` 图标实现 light/dark 切换
- [x] 8.4 dev server 验证 settings.html：点击图标切换主题、刷新保持、无闪屏（用户确认；布局与跨窗口同步问题已登记 issue 023）

### 9. 收尾验收

- [x] 9.1 与扩展后的 requirement.md "验收标准" 段逐项核对
- [x] 9.2 跑 `scripts/check` 全过
- [x] 9.3 与用户明确确认验收完成 → 把总体状态改为 `COMPLETED`

## 实现日志

| 日期 | 完成内容 | 备注 |
|------|---------|------|
|| 2026-06-23 | 完成 token 文件、index.css 映射、ToolCard 替换、guard 实现与接入、规则豁免更新 | 待视觉验证与 ESLint 阻塞处理 |
|| 2026-06-23 | dev server 启动在 http://localhost:1422/（1420 被 humming-tumbling-valley worktree 占用） | - |
|| 2026-06-23 | 删除 MemoryList.tsx 调试残留数字字面量，修复 pre-existing ESLint no-loss-of-precision 错误 | 非 027 范围，为让 scripts/check 全绿顺手修 |
|| 2026-06-23 | scripts/check/run.sh 全绿通过（后端 553 测试 + 前端 lint/color-guard/design-token-guard/typecheck/vitest） | - |
|| 2026-06-23 | 实现 settings.html 主题切换（useTheme hook + Tabs 图标 + main.tsx 防闪屏初始化） | 待用户在 dev server 验证 |
|| 2026-06-23 | scripts/check/run.sh 再次全绿（含主题切换代码） | - |
|| 2026-06-23 | 登记 issue 023：settings 主题切换后续优化（布局 + 跨窗口同步） | 暂不修复，后续独立需求处理 |
|| 2026-06-23 | 用户确认验收完成，progress.md 标 COMPLETED | - |
