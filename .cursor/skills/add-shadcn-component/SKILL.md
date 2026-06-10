---
name: add-shadcn-component
description: 在 agent-friend 前端（frontend/src/components/ui/）通过 shadcn CLI 引入通用 UI 组件的标准流程。当需要新增通用纯 UI 件、引入/添加 shadcn 组件、或在 frontend 里新建按钮/输入/弹层/列表/对话框等交互件时使用。强制走 CLI 拉源码，禁止手写后声称是 shadcn 源码。
---

# 引入 shadcn 组件（frontend/src/components/ui/）

把一个**通用纯 UI 件**正确地引入到 `frontend/src/components/ui/`。核心铁律：**shadcn 源码只能通过 CLI 拉，禁止手抄 / 手写后声称是 shadcn 源码**。

## 适用范围

- 只管**通用纯 UI 件**（无业务、无 store、只吃 props：button / input / select / dialog / collapsible / scroll-area 等）。
- **页面私有、带业务的组件不走本流程**，按既有约定放 `pages/<页>/components/`（它们本就不是 shadcn）。
- 动手前先读 `.cursor/rules/frontend-ui-conventions.mdc`（护栏：交互件一律用封装件 + 颜色走 token）。
- Node 要求 ≥ 22.13：CLI / pnpm 操作前先 `nvm use 22`。
- **根 `tsconfig.json` 必须有 `compilerOptions.paths`（`"@/*": ["src/*"]`）**：CLI 从根 tsconfig 解析 `@` 别名，缺了会把文件写进字面目录 `frontend/@/` 而不是 `src/`。本仓库已配好；若发现 `@/` 目录被创建，即是此项缺失。

## Step 1 · 确定要哪个组件（别凭记忆猜）

1. 把需求落成**交互形态**：常驻 inline 列表 / 弹出菜单 / 浮层对话框 / 表单控件 / 反馈提示 / 数据展示 …
2. 用 **context7 查 `/shadcn-ui/ui`**（或官方 docs `ui.shadcn.com`）把形态映射到组件，**不靠记忆**。
3. 当心**同名陷阱**：`Menubar` / `NavigationMenu` / `DropdownMenu` 都是「trigger + 弹出 content」的**弹出式**菜单；常驻竖向可选中列表是 `Sidebar` 区块（`SidebarMenu` / `SidebarMenuItem` / `SidebarMenuButton`）。
4. 三种判定：
   - **有直接对应** → 进 Step 2。
   - **只有偏重 block**（如 `Sidebar`，自带 provider、一批级联依赖、自有 `--sidebar-*` token）→ **先和用户确认**值不值得引入（依赖 / token / 改造成本）。注意 block 会**连带拉一批内部依赖**（`Sidebar` 实测连带 `button`/`input`/`separator`/`sheet`/`skeleton`/`tooltip` + `use-mobile` hook）；若用精简模式（如 `collapsible="none"`），部分依赖只是**静态死引用**（编译需要、运行时走不到），这些可保留官方原样、只适配真正会渲染的部分。
   - **shadcn 没有合适的** → 才允许**自写**项目纯 UI 件（`cva` + `cn` + 项目 token），且**文件头注释写明「非 shadcn，自写」**，不得谎称 shadcn 源码。

## Step 2 · 用 CLI 拉源码

- 单个：`pnpm dlx shadcn@latest add <component>`
- 多个：`pnpm dlx shadcn@latest add button collapsible`
- 底层依赖（`radix-ui` 等）CLI 会自动装。**禁止手抄源码、禁止手写后当作 CLI 产物**。
- `pnpm dlx` 的缓存目录在工作区外（`~/Library/Caches/pnpm/…`）；若 coding-agent 跑在沙箱里被 EPERM 挡住，需在沙箱外运行（`required_permissions: ["all"]`）。
- **重复拉 / 连带依赖会重建平铺文件**：CLI 对 registryDependency 一律按平铺 `ui/<name>.tsx` 生成，**不认我们已挪进 `ui/<name>/index.tsx` 的文件夹版**。于是会冒出一个未适配的平铺 `ui/<name>.tsx`，在 bundler 解析里**优先级高于** `<name>/index.tsx`、把适配版**盖掉**。拉完务必检查并**删掉这种平铺重复文件**，保留适配过的文件夹版。

## Step 3 · 适配项目约定（拉完必做）

1. **颜色 token**：官方源码用 `--primary` / `--background` / `--ring` / `--destructive` 等；本项目用 `--accent` / `--accent-fg` / `--surface` / `--fg` / `--muted` / `--border` / `--success` / `--danger` / `--warning`（见 `src/styles/theme/` 与 `src/styles/index.css` 的 `@theme inline`）。把 class 改成项目 token；若需新颜色，按 `frontend-ui-conventions` 先在**所有主题文件**补同名变量再用。
2. **清理 CLI 往 `index.css` 的注入**：带 `cssVars` 的组件（如 `Sidebar` 的 `--sidebar-*`）CLI 会**直接写硬编码颜色进 `src/styles/index.css`**——它塞 `:root { --x: hsl(...) }` / `.dark { ... }` 两个块。两个问题：① 硬编码 `hsl()` 会**挂 color-guard**；② `:root`/`.dark` 选择器跟本项目 `html[theme=...]` 换肤模型不匹配。处理：**删掉这些 `:root`/`.dark` 原始变量块**，把 `@theme inline` 里新增的 `--color-xxx` 别名**直接映射到项目已有语义 token**（如 `--color-sidebar: var(--surface)`），这样既过 color-guard 又随 `html[theme]` 换肤，组件源码可保持官方原样。CLI 顺带注入的 `@custom-variant dark (&:is(.dark *))` 可保留——本项目从不加 `.dark` class，等于让 shadcn 源码里的 `dark:` 一律 inert。
3. **目录 + barrel**：CLI 默认写平铺 `ui/<name>.tsx` → 挪进 `ui/<name>/index.tsx`；在 `ui/index.ts` 补一行 `export * from "./<name>"`。页面统一从 `@/components/ui` 导入，不直接 import 子目录。CLI 顺带生成的 hook（如 `use-mobile`）按 `components.json` 的 alias 落在 `src/hooks/`，无需挪。
4. **cn / 别名**：`components.json` 已把 `utils` 指到 `@/utils/cn`，无需改。

## Step 4 · 验证

跑 `./scripts/check`（lint + typecheck + color-guard + test）**全绿**才算完成。

## 回报

说清三件事：**哪些是 CLI 原样拉的**、**做了哪些 token / 目录适配**、**新增了哪些依赖**。
