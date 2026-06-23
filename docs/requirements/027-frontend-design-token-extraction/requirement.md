# 前端设计 token 抽取（规则一致性补齐）

## 状态

CONFIRMED

## 背景

main 上 commit `76edc7e chore(frontend): 扩前端 UI 规则到设计 token 维度…` 把 `.cursor/rules/frontend-ui-conventions.mdc` 从"颜色禁硬编码"升级到「**所有视觉 token**（颜色 / 字体栈 / 字号 / 字重 / 行高 / 字间距 / 间距 / 圆角 / 阴影 / 动效）一律走 CSS 变量」。

但 frontend 现存代码是旧规则下写的：

- **只有颜色做了变量化**：`src/styles/theme/light.css` & `dark.css` 已建好色板 token，`frontend/scripts/check-colors.mjs` 接入 `scripts/check` 做机械门禁。
- **其他维度（字体栈 / 字号 / 间距 / 圆角 / 阴影 等）大量硬编码**：散在 Tailwind arbitrary value（`text-[11px]` / `max-w-[360px]` / `rounded-[2px]` 等）和零星内联 CSS 里。

规则升级后，新代码会被 review 约束，但**存量代码出现规则一致性长尾**，且**其他维度暂未机械门禁兜底**，回潮风险高。本次需求把这块差距补齐，让规则在全维度上"被代码遵守"。

## 目标

把 frontend 现存代码里**已经在用但硬编码**的设计 token 抽到 CSS 变量，补一道综合 guard 防回潮。**只做规则一致性，不重新设计视觉**。

可衡量：

- frontend 业务代码（即 `src/components/ui/` shadcn 区以外）无硬编码设计 token，全部走项目 CSS 变量 / 映射 Tailwind token。
- 综合 guard（spacing / font / radius / shadow 维度合一）上线、接入 `scripts/check`，扫 `src/` 通过。
- shadcn 豁免规则正式补入 `.cursor/rules/frontend-ui-conventions.mdc`，对本次抽取与未来 guard 同源生效。
- 视觉与改前**基本一致**：关键页面（桌宠 / 对话窗 / 设置 / Memory Inspector）启动 dev server 人眼对比，允许"就近映射"造成的 1-2px 级偏移，**不接受**结构 / 比例 / 颜色变化。

## 范围

### 包含

**抽 token**：

- 抽取范围 = frontend `src/` 下**业务代码**（`src/components/`（除 `ui/`） / `src/pages/` / `src/styles/`），处理硬编码的：
  - **字体栈**（`font-family` 裸字面值）
  - **字号 / 字重 / 行高 / 字间距**（`text-[Npx]` / `font-[N]` / `leading-[N]` / `tracking-[N]` arbitrary value + CSS 裸字面值）
  - **间距**（`p-[Npx]` / `m-[Npx]` / `gap-[Npx]` arbitrary value + CSS 裸字面值）
  - **圆角**（`rounded-[Npx]` arbitrary value + CSS 裸字面值）
  - **阴影**（业务代码现状几乎都吃 Tailwind 默认 `shadow-sm/-md/-lg`，本次把默认值固化为项目 token；dark 主题阴影色独立给一套）
- token 分两类放：
  - **不随主题变**（字体栈 / 字号 / 字重 / 行高 / 字间距 / 间距 / 圆角）→ 新建 `src/styles/theme/tokens.css`，挂 `:root`
  - **随主题变**（阴影）→ 补进现有 `theme/light.css` / `theme/dark.css`，同名、值不同
- `src/styles/index.css` 的 `@theme inline` 块同步映射新 token 成 Tailwind token（如 `--text-xs: var(--font-size-xs)`、`--radius-md: var(--radius-md)`），让 `text-xs` / `rounded-md` 这类工具类直接走变量。
- 命名按规则约束：语义优先、用 `-xs/-sm/-md/-lg/-xl` 层级（不足 5 档时纵向扩，如 `-2xs`，不跳号、不混数字风）。
- **就近映射策略**：现状零散值（如 `text-[11px]` / `text-[12px]`）就近映射到项目层级最近一档（如统一到 `text-xs`），可接受 1-2px 偏移；档位定义参考 Tailwind 默认尺度兼容性。Phase 2 盘点表里给每个就近映射标"现状值 → 映射值 → 偏移量"列。

**补 guard**：

- 仿照 `check-colors.mjs` 模式，新建综合 guard（spacing / font / radius / shadow 维度合一）：
  - 扫描范围：`frontend/src/`
  - 检测项：上述维度的 Tailwind arbitrary value 与 CSS 裸字面值
  - **白名单**：含 `calc(...)` / `var(--xxx)` / `%` / `1px` 边界修正 等几何计算性 arbitrary value 不报
  - **目录豁免**：`src/styles/theme/`（token 源头）与 `src/components/ui/`（shadcn vendored）整体豁免
- 接入 `frontend/scripts/frontend/lint.{sh,ps1}` → `scripts/check`，与 colors guard 并列。

**改规则**：

- 在 `.cursor/rules/frontend-ui-conventions.mdc` 的"设计 token：禁止硬编码"段补一段 **shadcn 豁免说明**：`src/components/ui/` 作为 shadcn vendored 区域，整体豁免本节约束与未来所有 token guard（与 `src/styles/theme/` 一并作为豁免源）。理由：vendored CLI 源码改动会与上游冲突；shadcn arbitrary value 多为几何 calc 与组件专属 magic，不是项目级设计常量。

**设置面板主题切换（027 范围扩展）**：

- 在 `settings.html` 内提供 light / dark 主题切换入口，验证 027 新建的主题 token（颜色 + 阴影）可被实际换肤使用。
- 使用 shadcn `Tabs` 组件，tab trigger 内仅放图标（`Sun` / `Moon`，来自 `lucide-react`）。
- 主题状态持久化到 `localStorage`，key = `agent-friend-theme`，默认 `light`；首屏在 React 挂载前应用，避免闪屏。
- 状态逻辑抽成 `useTheme` hook，放在 `src/hooks/`，方便后续其他入口复用。
- 本次不做系统 `prefers-color-scheme` 跟随，留作后续独立需求。

### 不包含

- **不动 shadcn 区**：`src/components/ui/` 整体豁免。组件内部 arbitrary value（`rounded-[2px]` / `min-w-[8rem]` / `top-[50%]` / `w-[calc(...)]` 等）一律不抽。
- **不重新设计视觉**：值的就近映射可有 1-2px 偏移，但不引入新设计元素、不调结构 / 比例 / 颜色。
- **不扩展色板**：颜色 token 已建好，本次不动 `theme/light.css` / `dark.css` 中已有颜色变量，也不新增颜色。
- **不动动效**：transition / animation 时长 / 缓动维度，主项目目前没明显的动效硬编码模式，本次不扫不抽不补 guard。
- **不解决"设计稿真实性"问题**：本次只做规则一致性（代码遵守 token），不保证 token 值与设计稿一致。项目目前无正式设计稿，未来若引入 Figma 等设计稿 → token 同步机制（如 Design Tokens W3C / Style Dictionary），作为独立需求立项。
- **不扫 React inline style**：`style={{ padding: 12 }}` 这类 React 内联（值为 number 而非字符串），正则检测复杂、业务代码里几乎不用，本次不扫，留待 ESLint 规则补。
- **不扩 shadcn 豁免到非 shadcn 区**：业务代码（`components/im/`、`components/pet/`、`pages/*` 等）不享有豁免，必须抽净。

## 关键信息

- 触发 commit：`76edc7e chore(frontend): 扩前端 UI 规则到设计 token 维度…`（在 `origin/main`）
- 相关规则：`.cursor/rules/frontend-ui-conventions.mdc`
- 现有 token 文件：`frontend/src/styles/theme/light.css` & `dark.css`（颜色）；本次将新建 `frontend/src/styles/theme/tokens.css`（不随主题变维度）
- 现有 colors guard：`frontend/scripts/check-colors.mjs`，本次综合 guard 仿其模式
- 关键验证页面：桌宠（pet.html）/ 对话窗（chat.html）/ 设置（settings.html）/ Memory Inspector（memory-inspector.html）

## 验收标准

- [ ] frontend 业务代码（`src/components/`（除 `ui/`）/ `src/pages/` / `src/styles/`）无硬编码设计 token（字体栈 / 字号 / 字重 / 行高 / 字间距 / 间距 / 圆角 / 阴影 维度）
- [ ] 新建 `frontend/src/styles/theme/tokens.css`，包含上述不随主题变维度的 token
- [ ] `theme/light.css` & `dark.css` 补阴影 token，同名、值不同
- [ ] `styles/index.css` 的 `@theme inline` 块同步映射出 Tailwind token
- [ ] 综合 guard（spacing / font / radius / shadow）接入 `scripts/check`，扫 `src/` 通过
- [ ] `.cursor/rules/frontend-ui-conventions.mdc` 补 shadcn 豁免说明
- [ ] colors guard 仍通过
- [ ] 四个关键页面 dev server 跑起来人眼对比改前改后，仅"就近映射"造成的 1-2px 级偏移，无结构 / 比例 / 颜色变化
- [ ] `settings.html` 里能用 Tabs 图标切换 light / dark 主题，切换后页面整体换肤且刷新后保持
- [ ] `scripts/check` 全过

## 变更记录

| 日期 | 变更内容 | 影响范围 |
|------|---------|---------|
| 2026-06-23 | 创建需求文档（CONFIRMED） | - |
|| 2026-06-23 | 扩展范围：settings.html 增加 light/dark 主题切换（Tabs 图标 + useTheme hook） | AC、范围、关键信息 |
