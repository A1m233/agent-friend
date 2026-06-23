# 前端设计 token 抽取 - 进度

## 总体状态

NOT_STARTED

## 任务列表

实施顺序的硬约束：**先抽完 token + 替换业务代码 → dev server 验视觉 → 再启 guard → 再跑 `scripts/check`**。guard 在 token 抽完前启动会立刻爆红。

### 1. token 文件落盘

- [ ] 1.1 新建 `frontend/src/styles/theme/tokens.css`，含字体栈 / 字号 / 字重 / 行高 / 字间距 / 圆角 token（值见 design.md "token 体系完整定义"段，与 Tailwind v4 默认对齐）
- [ ] 1.2 修改 `frontend/src/styles/theme/light.css`，在 `html[theme="light"]` 块尾补 `--shadow-sm/-md/-lg/-2xl`（值 = Tailwind v4 默认 light）
- [ ] 1.3 修改 `frontend/src/styles/theme/dark.css`，在 `html[theme="dark"]` 块尾补 `--shadow-sm/-md/-lg/-2xl`（值 = 加深版，见 design.md）

### 2. `index.css` 接入 + `@theme inline` 映射

- [ ] 2.1 `frontend/src/styles/index.css` 顶部 `@import "./theme/tokens.css";`（在 light/dark 之前）
- [ ] 2.2 `@theme inline` 块补字体 / 字号 / 字重 / 行高（`--leading-snug`）/ 字间距（`--tracking-wide`）/ 圆角 / 阴影映射，**严格按 Tailwind v4 token 命名约定**（行高用 `--leading-{档}`、字间距用 `--tracking-{档}`、字号用 `--text-{档}`）
- [ ] 2.3 body 段 `font-family: system-ui, …` → `font-family: var(--font-sans);`

### 3. 业务代码替换

- [ ] 3.1 `frontend/src/pages/chat/components/ToolCard.tsx:56` `text-[11px]` → `text-xs`
- [ ] 3.2 `frontend/src/pages/chat/components/ToolCard.tsx:61` `text-[11px]` → `text-xs`

### 4. 视觉验证（guard 启动前）

**必须在 guard 启动前完成**——这一步是确认 token 抽对了、视觉没坏。

- [ ] 4.1 `pnpm --dir frontend dev` 启 dev server
- [ ] 4.2 逐入口（`pet.html` / `chat.html` / `settings.html` / `memory-inspector.html`，外加 `bubble.html`）light 主题下人眼对比改前改后，关注圆角 / 阴影 / 字号 / 字重 / 行高 / 字间距维度是否一致；ToolCard pre 块允许 1px 字号偏移
- [ ] 4.3 切到 dark 主题（`html[theme="dark"]`），同样逐入口对比；dark 阴影**会变深**，这是预期内变化（design.md 风险点 #1），其他维度需一致
- [ ] 4.4 任何视觉异常立即定位 token 值或 `@theme inline` 映射

### 5. 综合 guard 实现

- [ ] 5.1 新建 `frontend/scripts/check-design-tokens.mjs`，复用 `check-colors.mjs` 框架，按 design.md 检测规则表实现正则、白名单、目录豁免（`src/styles/theme/` + `src/components/ui/`）
- [ ] 5.2 `frontend/package.json` `scripts` 段新增 `"lint:tokens": "node scripts/check-design-tokens.mjs"`
- [ ] 5.3 本地手动跑 `pnpm --dir frontend run lint:tokens`，确认 0 违例（如有违例说明 token 没抽净或白名单漏了，回到对应任务修复）

### 6. guard 接入 `scripts/check`（双端同步）

- [ ] 6.1 `scripts/frontend/lint.sh` 加 `===> design-token-guard` 一步（在 color-guard 之后、typecheck 之前）
- [ ] 6.2 `scripts/frontend/lint.ps1` 同步加，PowerShell 错误处理保持与既有 color-guard 段一致（`$ErrorActionPreference = "Continue"` + 检查 `$LASTEXITCODE`）

### 7. 规则改动

- [ ] 7.1 修改 `.cursor/rules/frontend-ui-conventions.mdc` "设计 token：禁止硬编码" → "### 机械门禁" 小节，补一段豁免目录说明（包含 `src/styles/theme/` 和 `src/components/ui/`，与现有 colors guard 豁免合并表述），措辞见 design.md "规则改动" 段

### 8. 收尾验收

- [ ] 8.1 跑 `scripts/check`（macOS/Linux）或 `scripts/check.ps1`（Windows）全过：ESLint + colors guard + design-token guard + typecheck
- [ ] 8.2 与 requirement.md "验收标准" 段逐项核对
- [ ] 8.3 与用户明确确认验收完成 → 把总体状态改为 `COMPLETED`

## 实现日志

| 日期 | 完成内容 | 备注 |
|------|---------|------|
