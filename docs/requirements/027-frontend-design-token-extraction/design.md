# 前端设计 token 抽取 - 技术方案

## 状态

CONFIRMED

## 需求文档

→ [requirement.md](./requirement.md)

## 现状分析

### token 现状

| 文件 | 用途 | 现状 |
|---|---|---|
| `frontend/src/styles/index.css` | Tailwind v4 入口 + `@theme inline` 映射 + body 默认样式 | 颜色 token 已映射；body `font-family` 用裸字面值 |
| `frontend/src/styles/theme/light.css` | light 主题颜色 token | 仅含颜色 |
| `frontend/src/styles/theme/dark.css` | dark 主题颜色 token | 仅含颜色 |
| `frontend/scripts/check-colors.mjs` | 颜色门禁 | 已上线，接入 `scripts/check` |

不随主题变的尺度型 token（字体栈 / 字号 / 圆角 等）目前**无文件承载**。

### 业务代码硬编码盘点表

抽取范围 = `frontend/src/` 业务代码（已排除 `src/components/ui/` shadcn vendored 区与 `src/styles/theme/` token 源头）。

#### A. Tailwind arbitrary value `[...]`

| 文件:行 | 当前形式 | 维度 | 当前值 | 处理 |
|---|---|---|---|---|
| `src/pages/chat/components/ToolCard.tsx:56` | `text-[11px]` | 字号 | 11px | → `text-xs` (12px，+1px 偏移) |
| `src/pages/chat/components/ToolCard.tsx:61` | `text-[11px]` | 字号 | 11px | → `text-xs` (12px，+1px 偏移) |
| `src/pages/chat/components/ToolCard.tsx:43` | `max-w-[85%]` | 尺寸 | 85% | **不改**（guard 白名单：`%`） |
| `src/components/pet/PetBubble.tsx:85` | `max-w-[360px]` | 尺寸 | 360px | **不改**（guard 白名单：尺寸维度 arbitrary） |
| `src/components/pet/PetBubble.tsx:85` | `max-h-[440px]` | 尺寸 | 440px | **不改**（guard 白名单：尺寸维度 arbitrary） |
| `src/components/im/IMConnectDialog.tsx:164` | `w-[min(420px,calc(100vw-2rem))]` | 尺寸 | calc | **不改**（guard 白名单：含 `calc` / `min`） |

#### B. CSS / 内联裸字面值

| 文件:行 | 当前形式 | 维度 | 处理 |
|---|---|---|---|
| `src/styles/index.css:51` | `font-family: system-ui, -apple-system, "Segoe UI", "PingFang SC", sans-serif;` | 字体栈 | → `font-family: var(--font-sans);` |
| `src/styles/index.css:47` | `margin: 0;` | - | **不改**（body reset，非设计 token） |
| `src/components/im/IMConnectDialog.tsx:93` | `QRCode.toDataURL(..., { margin: 1, width: 240 })` | - | **不改**（QRCode 库 API 参数） |
| `src/pages/pet/App.tsx:185` | `"font-weight:bold"` | - | **不改**（`console.log` CSS 样式） |

#### C. Tailwind 默认工具类（业务代码当前使用情况）

业务代码已经在用、本次需要覆盖默认值的：

| 维度 | 使用的档 | 用途处数 |
|---|---|---|
| 字号 | `text-xs`, `text-sm`, `text-lg`, `text-xl` | 多处 |
| 字重 | `font-medium`, `font-semibold` | 8 |
| 行高 | `leading-snug` | 2 |
| 字间距 | `tracking-wide` | 2 |
| 圆角 | `rounded`, `rounded-md`, `rounded-lg`, `rounded-2xl`, `rounded-full` | 10+ |
| 阴影 | `shadow-lg`, `shadow-2xl` | 3 |
| 间距 | `p-{1..4}`, `gap-{1..6}`, `px-{2..4}`, `py-{1..2}`, `mt-1` 等 | 大量（**不改**：已走 Tailwind v4 `--spacing` token，规则明确允许） |

未用到的档（如 `font-bold` / `leading-tight` / `tracking-tighter` / `rounded-3xl` / `shadow-sm` 等）**不预建**对应 token，避免过度设计。

## 方案设计

### 关键思路

利用 Tailwind v4 的 `@theme inline` 机制覆盖 Tailwind 默认 token 值：

```
tokens.css (不随主题) + light.css/dark.css (随主题)
        ↓ var(--xxx)
index.css @theme inline 映射成 Tailwind token
        ↓
业务代码的 `rounded-lg` / `text-sm` / `shadow-lg` 等工具类自动走项目变量
```

项目变量初始值 = Tailwind v4 默认值 → **视觉零变化**，规则达成。

业务代码改动极小：仅 3 个文件 / 4 行（`text-[11px]` × 2、`font-family` × 1，加 PetBubble / IMConnectDialog / ToolCard 的尺寸 arbitrary 走白名单不改）。

### 涉及文件

| 文件路径 | 改动类型 | 说明 |
|---|---|---|
| `frontend/src/styles/theme/tokens.css` | **新增** | 不随主题变维度 token（字体栈 / 字号 / 字重 / 行高 / 字间距 / 圆角） |
| `frontend/src/styles/theme/light.css` | 修改 | 补阴影 token（4 档） |
| `frontend/src/styles/theme/dark.css` | 修改 | 补阴影 token（4 档，更深更不透明） |
| `frontend/src/styles/index.css` | 修改 | 引入 `tokens.css`、`@theme inline` 补字体/字号/字重/行高/字间距/圆角/阴影映射、body `font-family` 替换 |
| `frontend/src/pages/chat/components/ToolCard.tsx` | 修改 | `text-[11px]` × 2 → `text-xs` |
| `frontend/scripts/check-design-tokens.mjs` | **新增** | 综合 guard（spacing / font / radius / shadow 维度） |
| `frontend/package.json` | 修改 | 新增 `lint:tokens` script |
| `scripts/frontend/lint.sh` | 修改 | 加 `===> design-token-guard` 一步 |
| `scripts/frontend/lint.ps1` | 修改 | 加 `===> design-token-guard` 一步（双端同步） |
| `.cursor/rules/frontend-ui-conventions.mdc` | 修改 | 设计 token 段补 shadcn 豁免说明 |

### token 体系完整定义

#### `theme/tokens.css`（新增，挂 `:root`，不随主题变）

```css
/* 不随主题变的尺度型 token：字体 / 字号 / 字重 / 行高 / 字间距 / 圆角。
 * 与 theme/light.css & dark.css 分工：那两个文件管随主题变的（颜色 / 阴影）。
 * 见 .cursor/rules/frontend-ui-conventions.mdc。 */
:root {
  /* 字体栈 */
  --font-sans: system-ui, -apple-system, "Segoe UI", "PingFang SC", sans-serif;

  /* 字号（值 = Tailwind v4 默认，保证视觉零变化） */
  --font-size-xs: 0.75rem;     /* 12px */
  --font-size-sm: 0.875rem;    /* 14px */
  --font-size-base: 1rem;      /* 16px */
  --font-size-lg: 1.125rem;    /* 18px */
  --font-size-xl: 1.25rem;     /* 20px */

  /* 字重 */
  --font-weight-medium: 500;
  --font-weight-semibold: 600;

  /* 行高 */
  --line-height-snug: 1.375;

  /* 字间距 */
  --letter-spacing-wide: 0.025em;

  /* 圆角 */
  --radius-sm: 0.125rem;       /* 2px */
  --radius-default: 0.25rem;   /* 4px，对应 `rounded` */
  --radius-md: 0.375rem;       /* 6px */
  --radius-lg: 0.5rem;         /* 8px */
  --radius-xl: 0.75rem;        /* 12px */
  --radius-2xl: 1rem;          /* 16px */
  --radius-full: 9999px;
}
```

#### `theme/light.css`（补阴影）

```css
html[theme="light"] {
  /* …已有颜色 token… */

  /* 阴影（值 = Tailwind v4 默认 light，保证视觉零变化） */
  --shadow-sm: 0 1px 2px 0 rgb(0 0 0 / 0.05);
  --shadow-md: 0 4px 6px -1px rgb(0 0 0 / 0.1), 0 2px 4px -2px rgb(0 0 0 / 0.1);
  --shadow-lg: 0 10px 15px -3px rgb(0 0 0 / 0.1), 0 4px 6px -4px rgb(0 0 0 / 0.1);
  --shadow-2xl: 0 25px 50px -12px rgb(0 0 0 / 0.25);
}
```

#### `theme/dark.css`（补阴影，加深）

```css
html[theme="dark"] {
  /* …已有颜色 token… */

  /* 阴影（dark 主题加深透明度，深色背景下更可见） */
  --shadow-sm: 0 1px 2px 0 rgb(0 0 0 / 0.3);
  --shadow-md: 0 4px 6px -1px rgb(0 0 0 / 0.4), 0 2px 4px -2px rgb(0 0 0 / 0.4);
  --shadow-lg: 0 10px 15px -3px rgb(0 0 0 / 0.4), 0 4px 6px -4px rgb(0 0 0 / 0.4);
  --shadow-2xl: 0 25px 50px -12px rgb(0 0 0 / 0.6);
}
```

#### `index.css` 的 `@theme inline` 块（补映射）

```css
@import "tailwindcss";
@import "./theme/tokens.css";  /* 新增 */
@import "./theme/light.css";
@import "./theme/dark.css";

@custom-variant dark (&:is(.dark *));

@theme inline {
  /* …已有颜色映射不动… */

  /* === 新增映射 === */

  /* 字体 */
  --font-sans: var(--font-sans);

  /* 字号 */
  --text-xs: var(--font-size-xs);
  --text-sm: var(--font-size-sm);
  --text-base: var(--font-size-base);
  --text-lg: var(--font-size-lg);
  --text-xl: var(--font-size-xl);

  /* 字重 */
  --font-weight-medium: var(--font-weight-medium);
  --font-weight-semibold: var(--font-weight-semibold);

  /* 行高（Tailwind token 命名是 --leading-{档}） */
  --leading-snug: var(--line-height-snug);

  /* 字间距（Tailwind token 命名是 --tracking-{档}） */
  --tracking-wide: var(--letter-spacing-wide);

  /* 圆角 */
  --radius-sm: var(--radius-sm);
  --radius: var(--radius-default);
  --radius-md: var(--radius-md);
  --radius-lg: var(--radius-lg);
  --radius-xl: var(--radius-xl);
  --radius-2xl: var(--radius-2xl);

  /* 阴影 */
  --shadow-sm: var(--shadow-sm);
  --shadow-md: var(--shadow-md);
  --shadow-lg: var(--shadow-lg);
  --shadow-2xl: var(--shadow-2xl);
}

/* body 段：font-family 由裸字面值改为 var */
body {
  margin: 0;
  background: transparent;
  color: var(--fg);
  font-family: var(--font-sans);  /* 改：原为 system-ui, ... 字面值 */
}
```

### 综合 guard 实现：`frontend/scripts/check-design-tokens.mjs`

复用 `check-colors.mjs` 框架。

**扫描范围**：`frontend/src/`，扩展名 `.ts` / `.tsx` / `.css`。

**豁免目录**：
- `frontend/src/styles/theme/`（token 源头，与 colors guard 一致）
- `frontend/src/components/ui/`（shadcn vendored 区，本次需求新增豁免；与 [frontend-ui-conventions.mdc 改动] 同步）

**检测规则**（命中任一报错并退出）：

| 维度 | 检测正则（伪代码） | 说明 |
|---|---|---|
| 字号 arbitrary | `\btext-\[(?!.*(?:calc\|var\|min\|max\|clamp\|%\|inherit))[^\]]+\]` | 排除几何性 arbitrary |
| 字重 arbitrary | `\bfont-\[\d+\]` | 数字字重 |
| 行高 arbitrary | `\bleading-\[(?!.*(?:calc\|var))[^\]]+\]` | |
| 字间距 arbitrary | `\btracking-\[(?!.*(?:calc\|var))[^\]]+\]` | |
| 间距 arbitrary | `\b(p\|m\|gap\|px\|py\|pt\|pr\|pb\|pl\|mx\|my\|mt\|mr\|mb\|ml\|space-x\|space-y)-\[(?!.*(?:calc\|var\|%))[^\]]+\]` | 排除几何 |
| 圆角 arbitrary | `\brounded(?:-[a-z]+)?-\[(?!.*(?:calc\|var))[^\]]+\]` | |
| 阴影 arbitrary | `\bshadow-\[[^\]]+\]` | shadow arbitrary 几乎无合理用法，不开白名单 |
| CSS 字体栈裸字面值 | `font-family\s*:\s*(?!.*var\()[^;]+;` | |
| CSS 字号 / 字重 / 行高 / 字间距 / 间距 / 圆角 / 阴影 裸字面值 | `(font-size\|font-weight\|line-height\|letter-spacing\|padding\|margin\|gap\|border-radius\|box-shadow)\s*:\s*(?!.*var\()[^;]*\d+(?:px\|rem\|em)` | 排除已用 var |

**白名单（不报错的 arbitrary value 内容）**：

- 含 `calc(` / `min(` / `max(` / `clamp(` —— 几何计算表达式
- 含 `var(--` —— 已是项目变量
- 单位 `%` / `vh` / `vw` / `dvh` —— 视口/百分比单位
- 字面 `inherit` / `auto` / `100%`

**尺寸维度整体豁免**：`\b(w|h|min-w|min-h|max-w|max-h|size)-\[` arbitrary value **不进入检测列表**（组件几何参数本质不是设计 token；详见 requirement.md "包含"段第二部分）。

**inline `style={{}}` 不扫**（React 内联，本次范围外，留 ESLint 规则补）。

**输出格式**：与 `check-colors.mjs` 一致——遍历完所有文件统一打印违例列表 `file:line | rule | text`，最后退出码 1。

### `package.json` script 与 `scripts/frontend/lint.{sh,ps1}` 接入

`frontend/package.json` 新增：

```json
"lint:tokens": "node scripts/check-design-tokens.mjs"
```

`scripts/frontend/lint.sh`：

```bash
# 现有：eslint / color-guard / typecheck
echo "===> design-token-guard"
pnpm run lint:tokens
```

`scripts/frontend/lint.ps1`（双端语义保持一致）：

```powershell
Write-Host "===> design-token-guard"
$oldEAP = $ErrorActionPreference
$ErrorActionPreference = "Continue"
pnpm run lint:tokens 2>&1 | ForEach-Object { Write-Host $_ }
$ErrorActionPreference = $oldEAP
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
```

### 规则改动：`.cursor/rules/frontend-ui-conventions.mdc`

在"设计 token：禁止硬编码"段的"### 机械门禁"小节中，补一段 shadcn 豁免（与现有 `src/styles/theme/` 豁免并列）：

> ### 豁免目录
>
> 以下目录对本节约束与所有 token 维度门禁（已上线的 `color-guard`、本次新增的 `design-token-guard`、未来按同款模式补的 guard）**永久豁免**：
>
> - `src/styles/theme/` —— token 定义源头，硬编码是必然
> - `src/components/ui/` —— shadcn vendored 区域，通过 `pnpm dlx shadcn@latest add` 拉的官方源码原样保留；该目录内的 arbitrary value 多为几何 calc 与组件专属 magic，不是项目级设计常量；改动会与上游 CLI 更新冲突。新增 shadcn 组件后无需为其内部 token 做适配，仅外层使用方按本规则约束

（具体措辞最终以落到文件里为准。）

### 业务代码改动清单

3 个文件 / 4 行：

| 文件 | 行 | 改动 |
|---|---|---|
| `src/pages/chat/components/ToolCard.tsx` | 56 | `text-[11px]` → `text-xs` |
| `src/pages/chat/components/ToolCard.tsx` | 61 | `text-[11px]` → `text-xs` |
| `src/styles/index.css` | 51 | `font-family: system-ui, …;` → `font-family: var(--font-sans);` |
| `src/hooks/useTheme.ts` | - | 新增：主题状态管理 hook |
| `src/pages/settings/App.tsx` | - | 新增 Tabs 图标主题切换 UI |
| `src/pages/settings/main.tsx` | - | 初始化 `html[theme]` 属性，避免首屏闪屏 |

## 影响分析

### 多入口影响

frontend 有 5 个 HTML 入口（`bubble.html` / `chat.html` / `pet.html` / `settings.html` / `memory-inspector.html`），各自挂 React 子应用。所有入口共享 `src/styles/index.css`（即 token 体系），改动对所有入口**统一生效**——这是优势，也意味着任一入口视觉异常都说明 token 值或映射出了问题。

### 跨平台影响

- `lint.sh` / `lint.ps1` 双端同步更新，否则 macOS/Linux 与 Windows 门禁不一致
- 新 `check-design-tokens.mjs` 与 `check-colors.mjs` 同框架，纯 node ESM，跨平台天然一致

### 上下游影响

- 不动 backend / desktop / engine —— 纯前端
- 不动 shadcn 组件 —— 未来 shadcn 重拉 / 更新不冲突
- colors guard 仍生效 —— 两个 guard 并列，各管各的维度

### 风险点

1. **dark 主题阴影会变深** —— 这是本次唯一一处实质视觉变化。light 视觉零变化、dark 阴影从 Tailwind 默认（`rgb(0 0 0 / 0.1)` 等）改为加深版（`rgb(0 0 0 / 0.4)` 等）。**风险等级**：低（dark 主题阴影本来就该深）；**缓解**：dev server 跑 dark 主题对四个入口人眼复核
2. **token 映射顺序错误导致 `@theme inline` 解析失败** —— Tailwind v4 要求 `@theme` 内 token 名与默认体系一致（如行高用 `--leading-{档}` 而不是 `--line-height-{档}`）。**缓解**：实现时按 Tailwind v4 文档严格对齐 token 命名；首次集成后启 dev server 立即查 console 报错
3. **guard 上线即 hard gate，未抽净的硬编码会让 CI 立刻爆红** —— **缓解**：Phase 3 实施顺序严格保证「先抽完 token + 替换业务代码 → 跑 dev server 验视觉 → 再启 guard → 跑 `scripts/check` 全过」。如果 guard 启动后扫出预期外违例，立即修
4. **`text-[11px]` → `text-xs` 1px 偏移在 ToolCard pre 块的可读性影响** —— **风险等级**：低（pre 块是工具调用 args/result 调试展示，12px 比 11px 更易读）；**缓解**：dev server 验视觉时关注 ToolCard
5. **shadcn 重拉时被 guard 误伤** —— 已通过目录豁免规避，无新风险
6. **设置面板主题切换增加首屏闪屏风险** —— 在 `main.tsx` 里、React 挂载前读取 `localStorage` 并设置 `html[theme]`，可消除闪屏。**风险等级**：低；**缓解**：dev server 验证 settings.html 首屏无闪白/闪黑

### 设置面板主题切换设计

**状态持久化**

- key：`agent-friend-theme`
- value：`"light" | "dark"`
- 默认值：`"light"`
- 读写：纯 `localStorage`，本次不引入后端 / Tauri 配置同步

**Hook：`useTheme`**

```ts
const { theme, setTheme } = useTheme();
```

- 内部读取 `localStorage`，监听 `storage` 事件实现多标签同步
- `setTheme` 更新 state、写 `localStorage`、同步 `document.documentElement.setAttribute("theme", theme)`
- 返回当前 theme 和 setter

**初始化防闪屏**

在 `settings/main.tsx` 中，React `createRoot` 之前执行：

```ts
const saved = localStorage.getItem("agent-friend-theme") ?? "light";
document.documentElement.setAttribute("theme", saved);
```

这样 CSS 的 `html[theme="dark"]` 在首帧即生效。

**UI：shadcn Tabs + 图标**

```tsx
<Tabs value={theme} onValueChange={(v) => setTheme(v as Theme)}>
  <TabsList>
    <TabsTrigger value="light" aria-label="浅色主题">
      <Sun />
    </TabsTrigger>
    <TabsTrigger value="dark" aria-label="深色主题">
      <Moon />
    </TabsTrigger>
  </TabsList>
</Tabs>
```

- Trigger 内只放图标，符合"其中是图标"要求
- `aria-label` 保证可访问性

## 验收方案

对应 requirement.md 验收标准的具体验证方法：

| AC | 验证方法 |
|---|---|
| 业务代码无硬编码设计 token | `pnpm run lint:tokens` 通过（扫 src/ 0 违例） |
| 新建 `tokens.css` 含上述维度 token | 文件存在 + 包含字体 / 字号 / 字重 / 行高 / 字间距 / 圆角 token |
| `theme/light.css` & `dark.css` 补阴影 token，同名值不同 | 两文件都含 `--shadow-sm/-md/-lg/-2xl`，值不同 |
| `index.css` `@theme inline` 补映射 | 文件 diff 包含字体 / 字号 / 字重 / 行高 / 字间距 / 圆角 / 阴影映射 |
| 综合 guard 接入 `scripts/check` | `scripts/frontend/lint.sh`/`.ps1` 含 `===> design-token-guard` 一步 |
| `frontend-ui-conventions.mdc` 补 shadcn 豁免 | 规则文件含豁免段 |
| colors guard 仍通过 | `pnpm run lint:colors` 通过 |
| 关键页面 dev server 视觉一致 | `pnpm dev` 跑起来，逐页（pet / chat / settings / memory-inspector）人眼对比改前改后，light + dark 各一遍。允许 ToolCard text-xs 1px 偏移、dark 阴影加深；不允许其他变化 |
| settings.html 主题切换可用 | 打开 settings.html，点击太阳/月亮图标切换 light/dark；刷新后保持所选主题；切换时无闪屏 |
| `scripts/check` 全过 | 等价 ESLint + colors guard + design-token guard + typecheck 全过 |

## 变更记录

| 日期 | 变更内容 | 是否需要重新实现 |
|------|---------|----------------|
| 2026-06-23 | 创建技术方案文档（CONFIRMED） | - |
|| 2026-06-23 | 扩展范围：settings.html 增加 light/dark 主题切换设计（useTheme hook + Tabs 图标） | 涉及文件、业务代码改动清单、验收方案、风险点 |
