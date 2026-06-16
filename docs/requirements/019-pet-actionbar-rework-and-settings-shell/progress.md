# 019 · 桌宠 ActionBar 容器重做 + TooltipButton + 设置窗口骨架 — 进度

## 总体状态

<!-- NOT_STARTED | IN_PROGRESS | COMPLETED | BLOCKED -->
COMPLETED

---

## 任务列表

### M0 · 副产品 issue 登记

- [x] M0.1 · 新建 `docs/issues/013-pet-recall-from-desktop/README.md`，状态 `open`、严重度 `medium`，列候选方案

### M1 · UI 装件层（独立可验证）

- [x] M1.1 · `pnpm dlx shadcn@latest add carousel` —— 装 embla + 落 `components/ui/carousel/index.tsx`（实施期发现 CLI 顺手装了 `button.tsx` 平级会顶替项目改造版 `button/index.tsx`，已删；`carousel.tsx` 移至 `carousel/index.tsx` 子目录对齐惯例）
- [x] M1.2 · 新建 `components/ui/tooltip-button/index.tsx` —— 按 design §2.2.1 落 API
- [x] M1.3 · ~~新建 `tooltip-button.test.tsx`~~ → **豁免**（变更 2026-06-16：项目 vitest 环境为 node + 未装 RTL/jsdom；TooltipButton 属 "纯 JSX 拼装 + props 透传"，沿 dev-workflow 豁免；分页逻辑改用 derivePageState 纯函数测，见 M2.0）
- [x] M1.4 · 更新 `components/ui/index.ts` barrel 出口（`tooltip-button` + `carousel`）

### M2 · ActionBar 重写

- [x] M2.0 · 新建 `pages/pet/actionBarPaging.ts` —— 抽 `derivePageState({buttonCount, pageSize, currentPage})` 纯函数 + `actionBarPaging.test.ts` 覆盖 ≤N / >N / 首末页箭头条件分支（12/12 通过 · 2026-06-16）
- [x] M2.1 · 重写 `pages/pet/ActionBar.tsx`：chip 容器 + 按钮数组 + carousel/flex 双路径 + 自定义箭头 + 动态 chipW/chipH；分页状态使用 M2.0 的 derivePageState
- [x] M2.2 · 调整 `pages/pet/computeActionBarPosition.ts`：~~移除内置 BAR_W/BAR_H_*~~ → **零改动**（实施期发现该文件已是参数化纯函数，barSize 一直由调用方传入）
- [x] M2.3 · 跑 `computeActionBarPosition.test.ts` —— **通过**（5/5；测试用任意 barSize 输入测算法，跟 ActionBar 实际尺寸解耦）

### M3 · 设置窗口骨架

- [x] M3.1 · 新建 `frontend/settings.html`
- [x] M3.2 · 新建 `frontend/src/pages/settings/main.tsx` + `App.tsx`（占位骨架）
- [x] M3.3 · `vite.config.ts` rollupOptions.input 追加 `settings`
- [x] M3.4 · `tauri.conf.json` windows 数组追加 settings 配置

### M4 · Rust invoke

- [x] M4.1 · `lib.rs` 抽象 `show_and_focus(app, label)`，删 `show_and_focus_chat`，`open_chat` + 托盘 "open_chat" 改用通用版
- [x] M4.2 · `lib.rs` 新增 `hide_pet` invoke（注释指向 issue 013）
- [x] M4.3 · `lib.rs` 新增 `open_settings` invoke
- [x] M4.4 · `lib.rs` `on_window_event` chat 拦截扩展为 `matches!(label, "chat" | "settings")`
- [x] M4.5 · `lib.rs` invoke handler 注册（dev / release 两段都加 `hide_pet` + `open_settings`）

### M5 · 集成

- [x] M5.1 · `pages/pet/App.tsx` 给 ActionBar 传 `onHidePet` / `onOpenSettings`（invoke 直连，含 issue 013 注释）

### M6 · 验收

- [x] M6.1 · `./scripts/check` 全绿（lint + typecheck + 前端单测 + Rust build + colors guard）—— Python 361 tests + Frontend 84 tests + eslint + color-guard (76 files) + tsc 全过
- [x] M6.2 · `cargo build` —— 通过（仅 1 个无关的预先存在 dead_code warning：`bubble_window::Screen.h`）
- [x] M6.3 · 手测 AC-1 ~ AC-10 通过（用户 dev 环境真跑：AC-1 chip 横向 + 背景 + 固定宽 ✓ / AC-2 carousel 分页箭头 ✓ / AC-3 tooltip 深色背景 + 按钮 hover 浅灰 ✓ / AC-4 隐藏桌宠 + 托盘唤回 ✓ / AC-5 设置窗口弹出 + 关闭即隐藏 ✓ / AC-6 dev gate 逻辑代码自检 ✓ / AC-7 17a hover gate 等价 ✓ / AC-8 既有路径零退化 ✓）
- [x] M6.4 · `lib.rs::hide_pet` 注释中已写明 `docs/issues/013-pet-recall-from-desktop/` 完整路径
- [x] M6.5 · `progress.md` 总体状态 → COMPLETED（用户验收通过 · 2026-06-16）

---

## 实现日志

| 日期 | 完成内容 | 备注 |
|------|---------|------|
| 2026-06-16 | Phase 1 / Phase 2 完成；分支 `feature/019-pet-actionbar-rework-and-settings-shell` 从 origin/main 切；requirement.md / design.md 双 CONFIRMED | — |
| 2026-06-16 | M0 + M1 + M2 + M3 + M4 + M5 + M6.1/2/4 完成（顺序按 M0→M1→M2→M3→M5→M4→M6.1/M6.2 实际执行；M2.0 derivePageState 单测 12/12，M2.3 computeActionBarPosition 单测 5/5）。`./scripts/check` 全绿。 | M2.2 实施期发现 computeActionBarPosition.ts 已是参数化纯函数（零改动）；M1.3 + AC-9 涉及变更见 requirement.md / design.md 变更记录。 |
| 2026-06-16 | 顺手 hotfix：`pages/pet/App.tsx:136` 的 `color:#4f46e5` 硬编码颜色（commit `4bf33f48` 18b dev observability log 引入、跟 019 无关，但阻塞 AC-9 colors guard）—— 删 `color:...` 仅保留 `font-weight:bold`。**非 019 范围**，commit message 单独注明。 | 18b 合入时 colors guard 漏过（可能 17a 落地 + 17b 落地之间这条门禁还没全打开/未跑全套），019 触发完整 check 后暴露。 |
| 2026-06-16 | M6.3 手测期发现两个问题并修复：① **tooltip 透明** —— 根因 shadcn add 拉的 `tooltip/index.tsx` 用了项目主题不存在的 `bg-foreground` / `text-background` / `fill-foreground`，改用 `bg-fg` / `text-bg` / `fill-fg`（在 tooltip 件本体修，未来所有 tooltip 使用者受益）；② **icon button hover 不变灰** —— 根因 chip 容器底色 `bg-surface/95` 跟 button ghost hover `bg-surface` 几乎同色，chip 容器改 `bg-bg/95`（白底）。同时登 [`issue 014`](../../issues/014-shadcn-ui-tokens-unadapted/) 跟踪 `input/sheet` 等同款未适配 token（项目级技术债，超出 019 范围）。 | 临时加的 5 个 carousel 探针按钮（Bug/FlaskConical/TestTube/Beaker/Sparkles）+ 对应 lucide import 已删，恢复 design 设计的 prod=3 / dev=5 状态。 |
| 2026-06-16 | **019 验收通过 → COMPLETED**。用户手测 AC-1 ~ AC-8 + AC-10 全过；AC-9 `./scripts/check` 自动验证全绿（Python 361 tests + Frontend 84 tests + eslint + color-guard 76 文件 + tsc）。落地范围：ActionBar 横向 chip + carousel 分页 + TooltipButton 件 + 隐藏桌宠按钮 + 打开设置按钮 + 设置窗口骨架；副产品：tooltip 件项目 token 适配 + 18b colors hotfix；登 issue 013（桌面唤回桌宠缺口） + issue 014（input/sheet 未适配 token）。 | — |
