# 014 · `components/ui/` 多个 shadcn 组件未适配项目颜色 token

- **状态**：open
- **严重度**：low
- **发现日期**：2026-06-16
- **关联需求**：[019-pet-actionbar-rework-and-settings-shell](../../requirements/019-pet-actionbar-rework-and-settings-shell/)（实施期 M6.3 手测 tooltip 透明问题暴露）

## 现象

`components/ui/` 下多个 shadcn-add 拉取的组件源码里使用了 **项目主题文件不存在的 token**（`--foreground` / `--background` / `--primary` / `--primary-foreground` / `--input` / `--ring` / `--ring-offset` / `--destructive` / `--muted-foreground` / `--secondary` 等），导致这些组件在项目内**渲染时颜色 fallback 到透明 / 浏览器默认值**。

项目主题（`src/styles/theme/light.css` / `dark.css`）只定义了 9 个语义 token：`--bg / --surface / --fg / --muted / --accent / --accent-fg / --border / --success / --danger / --warning`。`src/styles/index.css` 把它们通过 `@theme inline` 映射成 `--color-bg / --color-surface / ...` 供 tailwind 工具类引用。**没有 `--foreground` / `--background` / `--primary` 等 shadcn 默认 token**。

### 已发现的未适配组件

| 组件 | 未适配的 token / 类名 | 备注 |
|---|---|---|
| `tooltip/index.tsx` | `bg-foreground` / `text-background` / `bg-foreground fill-foreground` | **019 已修**（M6.3 验收触发，直接用项目 `bg-fg` / `text-bg` / `fill-fg` 替换） |
| `input/index.tsx` | `border-input` / `bg-primary` / `text-primary-foreground` / `text-foreground` / `placeholder:text-muted-foreground` / `border-destructive` / `ring-destructive` / `dark:bg-input/30` | 项目里目前没实际渲染暴露 |
| `sheet/index.tsx` | `bg-background` / `text-foreground` / `text-muted-foreground` / `ring-offset-background` / `ring-ring` / `bg-secondary` | 项目里目前没实际渲染暴露 |

`sidebar/index.tsx` 用了 `--sidebar-*` 一族 token，**已通过 `@theme inline` 显式映射**（`--color-sidebar / --color-sidebar-foreground / --color-sidebar-primary / ...`）→ 对齐项目语义 token，**不在此 issue 范围内**。

`button/index.tsx` 已做了项目改造（用 `--accent / --danger / --surface / --fg`），属于 shadcn add 后**有做适配**的范例。

## 根因初判

shadcn 组件通过 `pnpm dlx shadcn@latest add <name>` 拉取后，源码默认使用 shadcn 内置的 token 命名空间（`--foreground` / `--background` / `--primary` / 等）。项目自己用的是**精简语义 token** 系（`--bg` / `--surface` / `--fg` / `--accent`），需要**每个新拉的件**都做一次"颜色 token 改用项目语义 token"的人工适配（沿 button/index.tsx 顶部 docstring 的"适配点 ①"）。

历史上 17a 时期 shadcn add tooltip / sheet / input 拉取后**未做这套适配** → 一直潜伏。`colors guard`（`frontend/scripts/check-colors.mjs`）只扫硬编码颜色（hex / rgb / tailwind 色板类），**不扫"用了项目主题不存在的 token"** —— 所以机械门禁也抓不到。

## 影响与判断

- **tooltip**：本期 019 引入大规模使用（ActionBar 每颗按钮都套 tooltip），M6.3 实测暴露 → 已修
- **sheet / input**：项目里目前还没有实际渲染场景使用（chat / pet / bubble / settings 都没用到 sheet 形态、没有独立 input 组件渲染）→ **不阻塞**任何当前 AC
- **未来风险**：任何后续需求只要 import 这两个件 + 渲染到页面 → 立刻暴露同款"透明 / 默认色"问题

**严重度 low**：当前**无可见缺陷**（潜伏未暴露）；但属于需要清理的系统性技术债，避免下一个使用者再次撞坑。

## 可选修复方向

1. **逐件做项目 token 适配**（沿 button/index.tsx 模式）：
   - `input`：`border-input` → `border-border`、`bg-primary` → `bg-accent`、`text-primary-foreground` → `text-accent-fg`、`text-foreground` → `text-fg`、`placeholder:text-muted-foreground` → `placeholder:text-muted`、`border-destructive` → `border-danger`、`ring-destructive` → `ring-danger`、删除 `dark:bg-input/30`
   - `sheet`：`bg-background` → `bg-bg`、`text-foreground` → `text-fg`、`text-muted-foreground` → `text-muted`、`ring-offset-background` → `ring-offset-bg`、`ring-ring` → `ring-accent` 或 `ring-border`、`bg-secondary` → `bg-surface`
   - 每个文件顶部补一段跟 button/index.tsx 同款的 "shadcn 拉取 + 已按项目约定适配" 注释
2. **`@theme inline` 增加 shadcn 默认 token 映射兜底**：在 `styles/index.css` 加上 `--color-foreground: var(--fg); --color-background: var(--bg); --color-primary: var(--accent); ...` 等，让 shadcn 默认源码**直接 work**，不用手适配。trade-off：源码失去"看就是项目风格"的清晰度，混了一套"shadcn 命名空间"
3. **加机械门禁**：扩展 `frontend/scripts/check-colors.mjs`（或单独的 `check-tokens.mjs`），对 `components/ui/` 内 import 的颜色类名做白名单校验，未在项目 token 列表里的报错。trade-off：写起来更复杂；shadcn add 后的"未适配"立即被门禁拦下，但需要工具知道项目 token 全集

实施期建议先做 **1**（最小修复 + 跟 button 风格统一）+ **3**（防止未来 shadcn add 又漏适配）；**2** 留作架构选择，未必采纳。

## 备忘

- 与 [docs/explorations/desktop-completeness/](../../explorations/desktop-completeness/) 无直接关联；属于纯前端工程清理
- 修复后此 issue → resolved，列被修复的件列表 + 关联 commit
- 与 [frontend-ui-conventions.mdc](../../../.cursor/rules/frontend-ui-conventions.mdc) §颜色的精神一致（"颜色一律走主题文件 token / 变量"），只是当前规则原文没明确"shadcn add 后必须适配 token"，可在修复时一并补一句
