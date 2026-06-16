# 019 · 桌宠 ActionBar 容器重做（横向 chip + carousel 分页）+ TooltipButton 封装 + 设置窗口骨架

> Pet ActionBar Rework & Settings Window Shell
>
> 把 017a 落地的"sprite-relative 浮动、垂直 flex、纯文字按钮、无背景"的 ActionBar 重做为"横向 chip 容器（固定宽、有背景、icon-only 按钮 + tooltip、按钮过多时左右箭头分页滚动）"；顺手新增"隐藏桌宠按钮"（复用托盘已有 `toggle_pet`）与"打开设置按钮"，并起一个**纯壳**的设置窗口作为后续设置项落地的容器。本期**不**实现任何具体设置项 / 不改 17a 的浮动定位算法 / 不引入新的桌宠唤回机制（继续走系统托盘）。

---

## 状态

<!-- 草稿（Draft） | 已确认（Confirmed） -->
已确认（Confirmed）

---

## 1. 背景

### 1.1 现状

- [需求 017a](../017-pet-overlay-form-switch/) 已落地 `frontend/src/pages/pet/ActionBar.tsx`：sprite-relative DOM 浮动 + hover gate + `computeActionBarPosition` 锚算（默认上方居中、屏顶贴墙翻下方）。
- 当前 ActionBar 布局：垂直 flex、无背景、按钮平铺；正式按钮 "打开对话界面"（文字 outline button），dev gate 两颗 inject 按钮（💬 短气泡 / 📜 长气泡）直接堆在主按钮下方。
- 桌宠隐藏：当前仅托盘菜单 `toggle_pet`（`src-tauri/src/lib.rs:330`），用户无法从桌宠本身一键隐藏。
- 设置窗口：当前完全不存在；已有窗口为 chat / pet / bubble / devhub 四个。
- UI kit 现状（`frontend/src/components/ui/`）：已封装 `button` / `tooltip` / `input` / `sheet` 等，**尚未有 `tooltip-button` 这样的组合件**；项目强制走 `components/ui/` + shadcn CLI + 颜色 CSS 变量（沿 [`frontend-ui-conventions`](../../../.cursor/rules/frontend-ui-conventions.mdc)）。
- 桌面端缺口已系统盘点于 [`docs/explorations/desktop-completeness/`](../../explorations/desktop-completeness/)（Tier 0~3）；本期不强行串入其中任何 Tier 项目。

### 1.2 这次要做什么

- **ActionBar 容器层重做**：垂直 flex → 横向 chip；新增背景 / 固定宽度（常量管理，初值按按钮 icon 尺寸 + 间距推出）；按钮全部 icon-only + hover tooltip。
- **carousel 分页滚动**：每页 N 个按钮（前端常量管理，初值 N=6）；总数 > N 时左右箭头出现，每点滚一页；总数 ≤ N 时箭头**不渲染**（非 disabled）；滚到首/末页时对应箭头**不渲染**。**优先复用 shadcn 的 `carousel` 组件**，仅当无法满足需求时自写；最终封装为纯 UI 件放入 `components/ui/`。
- **TooltipButton 通用件**：在 `components/ui/` 新建 `tooltip-button`，组合现有 `button` + `tooltip`；统一 icon 按钮的尺寸 / hover 行为 / tooltip 文案 props。ActionBar 内所有按钮（正式 + dev）一律走 `TooltipButton`。
- **隐藏桌宠按钮**：ActionBar 一颗 icon 按钮（tooltip "隐藏桌宠"），点击调一个 Tauri invoke，**复用现有托盘 `toggle_pet` 同款隐藏行为**；唤回机制不动，继续走系统托盘菜单。
- **打开设置按钮 + 设置窗口骨架**：ActionBar 一颗 icon 按钮（tooltip "打开设置"），点击 invoke `open_settings`（参照现有 `open_chat`）；新建第五个 Tauri 窗口 `settings`（`tauri.conf.json` windows 数组追加），承载 `pages/settings/`；窗口本体**只渲染一个占位骨架**（标题 + 一行 placeholder 文案），不实现任何具体设置项；关闭即隐藏不销毁（沿 chat 窗约定）。
- **dev inject 按钮 icon 化 + 位置调整**：💬 / 📜 改为 icon-only + tooltip，仍 `import.meta.env.DEV` gate；位置由"主按钮下方紧贴"改为 carousel 末尾。
- **icon 来源**：统一 `lucide-react`（项目 shadcn 默认）。

### 1.3 与 017a / 017b 的关系

017a 已落地：
- ActionBar sprite-relative DOM 浮动（`pages/pet/App.tsx` 集成、`pages/pet/ActionBar.tsx` 本体、`pages/pet/computeActionBarPosition.ts` + `.test.ts` 算法层）
- hover gate（[issue 008](../../issues/008-pet-action-bar-hover-gate/) 已闭）
- PIXI sprite hover bridge → ActionBar visible 显隐
- dev gate 两颗 inject 按钮

本期**不动**：
- sprite-relative 定位算法（`computeActionBarPosition`）只改常量、不改算法
- hover gate / visible prop / PIXI sprite hover bridge 信号链
- `[data-hit]` 命中机制
- `pages/pet/App.tsx` 对 ActionBar 的调用 / 17a/17b 任何其他模块

本期**仅动**：
- `ActionBar.tsx` 内部 JSX 与样式（容器布局、按钮形态、分页结构）
- 新增 `components/ui/tooltip-button` + 新增（或拉取）`components/ui/carousel`
- 新增 `pages/settings/` 入口、`tauri.conf.json` windows 配置追加、Rust 侧 `open_settings` invoke
- ActionBar 尺寸常量（`BAR_W` / `BAR_H_*`）—— `computeActionBarPosition` 的 "屏顶贴墙翻下方" 算法测试在常量变化后**继续通过**；不通过则停下来按 §5 测试约束处理。

17a 全部 AC（特别是 AC-6 操作栏 sprite-relative + hover gate）在本期完成后**保持等价、不退化**。

### 1.4 与 `desktop-completeness` 探索的关系

[`docs/explorations/desktop-completeness/`](../../explorations/desktop-completeness/) 把"桌宠标准交互"盘到 Tier 0~3，其中：
- 本期"隐藏桌宠按钮"属于 Tier 2 "右键菜单 + 双击反应"语义的子集，但用更简形态实现（直接进 ActionBar，不引入右键菜单）。
- 本期"ActionBar 容器层"是给后续 Tier 1/2/3 项目（形象状态机 / 右键菜单内容 / 托盘扩展等）预留**接入位**——后续模块只要往 ActionBar 加一颗 `TooltipButton` 即可。

本期**不顺手做**任何其他 Tier 项目（形象状态机 / 位置持久化 / 跨 Space 浮动 / bridge 连接连续性 / 右键菜单内容 / 双击反应等）；那些按 explorations 后续单独立项。

---

## 2. 本期范围（In Scope）

| 模块 | 目标 |
| --- | --- |
| **ActionBar 容器重做** | 横向布局 + 背景 + 固定宽（常量管理）+ icon-only 按钮；保留 17a 的 sprite-relative 浮动 + hover gate（仅常量调整、不改算法） |
| **carousel 分页** | 每页 N 个按钮（常量管理，初值 6）；> N 时左右箭头出现 + 每点滚一页；≤ N 时箭头不渲染；首/末页对应箭头不渲染。优先复用 shadcn `carousel`，必要时自写并封装到 `components/ui/` |
| **TooltipButton 封装** | `components/ui/tooltip-button` 新增；组合 button + tooltip；ActionBar 全部按钮统一用它 |
| **隐藏桌宠按钮** | ActionBar 一颗 icon 按钮 + tooltip "隐藏桌宠"；点击 invoke 复用现有 `toggle_pet` 同款隐藏行为；唤回继续走系统托盘 |
| **打开设置按钮 + 设置窗口骨架** | ActionBar 一颗 icon 按钮 + tooltip "打开设置"；点击 invoke `open_settings`（参照 `open_chat`）；新增第五个 Tauri 窗口 `settings` + `pages/settings/` 入口；窗口内容仅一个占位骨架 + "设置（占位）" 文案；关闭即隐藏不销毁 |
| **现有按钮 icon 化** | "打开对话界面" → icon + tooltip "打开对话"；dev inject 按钮（💬 / 📜）→ icon + tooltip，仍 dev gate；dev 按钮位置移至 carousel 末尾 |
| **icon 来源** | 统一 `lucide-react`；如未装则在本期落 `pnpm add lucide-react`（按 shadcn 默认） |
| **既有路径回归** | 17a 全部 AC（特别是 AC-6 ActionBar sprite-relative + hover gate）在本期完成后等价通过；17a/17b 其他模块零退化 |

---

## 3. 非目标（Out of Scope）

- **设置窗口内任何具体设置项**：主题切换 / persona 配置 / API key / 模型选择 / 偏好项 / 任何 store 接入等本期一律不做；窗口仅为后续设置需求预留容器。
- **新的桌宠唤回机制**：不引入屏幕边角驻留、不引入新的托盘菜单项、不引入右键唤起。隐藏后唯一唤回路径 = 现有托盘 `toggle_pet` 菜单。
- **桌宠标准交互的其他 Tier 项**：右键菜单 / 双击反应 / 形象状态机 / 跨 Space 浮动 / 位置持久化 / bridge 连接连续性 / 离线表情 / 主动 nudge 承接 / 环境感知 / Live2D 真接入 / 托盘菜单扩展 等不在本期范围。
- **ActionBar 触发逻辑改动**：继续 hover-only，不改为常驻 / 不改为点击切换。
- **ActionBar 定位算法改动**：`computeActionBarPosition` 算法本体不动，只改尺寸常量。
- **ActionBar 视觉之外的桌宠交互改动**：拖拽 / cursor passthrough / sprite world position 数据流 / bubble 跟随等 17a/17b 既有路径完全不动。
- **跨平台差异性 plumbing**：本期改动全在前端 + Tauri windows 配置 + Rust invoke 注册；macOS / Win 走同一份代码，无平台 cfg-gate 新增。
- **设置窗口的复杂窗口能力**：不做模态、不做多窗口同步、不做窗口位置持久化、不做窗口尺寸记忆。

---

## 4. 核心需求详述

以下需求点（R-级）只讲"做什么 / 做到什么效果"；具体实现选型（shadcn carousel 是否可直接用 / 容器尺寸常量取值 / icon 具体选哪个 / Tauri 窗口配置细节 / invoke 名称）由 [`design.md`](./design.md) 决定。

### 4.1 ActionBar 容器层

- **R-4.1.1 横向 chip 容器**：ActionBar 由垂直 flex 改为水平横向；视觉上呈"浮动 chip"（有背景、圆角、内边距），不是裸按钮列。
- **R-4.1.2 固定宽度**：容器宽度由常量推出（按按钮 icon 尺寸 × 每页 N 个 + 间距 + 左右箭头位 + padding），**不**随按钮增减自动撑大；按钮溢出时通过 carousel 滚动呈现而非容器变宽。
- **R-4.1.3 sprite-relative 浮动保留**：容器整体定位继续走 17a 的 `computeActionBarPosition`（sprite 锚 + 上方居中 + 屏顶贴墙翻下方）；只更新尺寸常量。
- **R-4.1.4 hover gate 保留**：容器整体显隐继续走 17a 的 PIXI sprite hover bridge + DOM 自身 `onMouseEnter` / `onMouseLeave` 双触发机制；mouse leave 渐隐沿 17a 行为不变。
- **R-4.1.5 命中机制保留**：容器与内部交互件继续标 `data-hit`，由 `usePetPassthrough` DOM hit-test 优先承担命中。
- **R-4.1.6 每页 N 由前端常量管理**：当前 N=6；常量集中管理（一处定义、推导出容器宽度），后续调整只改这一处。

### 4.2 carousel 分页滚动

- **R-4.2.1 按钮总数 ≤ N**：左右箭头**不渲染**，按钮直接横铺；不预留箭头位（容器宽 = 按钮位宽，无箭头 padding）。
- **R-4.2.2 按钮总数 > N**：左右箭头出现在容器两侧；箭头本身也是 `TooltipButton`（tooltip "上一页" / "下一页"）；每点一次水平滚动一页（一页 = N 个按钮的整体偏移）。
- **R-4.2.3 滚到头停**：当前为首页时左箭头**不渲染**；当前为末页时右箭头**不渲染**（不是 disabled、不是禁用样式）；不做循环滚动。
- **R-4.2.4 滚动动画**：分页切换有平滑过渡（具体动画时长 / 缓动函数由 design 决定）；动画期间用户的 hover 离开 → 容器渐隐，行为与 17a 一致。
- **R-4.2.5 shadcn carousel 优先复用**：design 阶段先验证 shadcn `carousel` 是否能满足 R-4.2.1 ~ R-4.2.4（API 是否支持每页 N 个步长 / 箭头按钮自定义渲染 / 滚到头隐藏箭头）；满足则走 `pnpm dlx shadcn@latest add carousel` 拉入、按需薄封装；不满足则自写并封装为 `components/ui/carousel`（命名与 shadcn 一致避免后续混淆）。
- **R-4.2.6 carousel 作为纯 UI 件**：carousel 本身**不感知** ActionBar 业务（不知 "隐藏桌宠" / "打开设置" 等按钮语义），只吃 children + N（每页数）+ 可选样式 props；ActionBar 把 `TooltipButton` 数组作为 children 喂进去。

### 4.3 TooltipButton 通用件

- **R-4.3.1 文件位置**：`frontend/src/components/ui/tooltip-button/`（与现有 button / tooltip 平级）；按现有 barrel 出口 (`components/ui/index.ts`) re-export。
- **R-4.3.2 API**：吃 `icon`（ReactNode，必填 / 通常是 lucide icon）、`tooltip`（string，必填）、`onClick`（必填）+ 透传 `button` 既有 props（variant / size / disabled / data-* 等）；其他 props 可选。
- **R-4.3.3 实现组合**：内部用现有 `Tooltip` + `Button`（`@/components/ui`），不绕开封装件直接 import shadcn 子件、不写原生 `<button>`。
- **R-4.3.4 hover 触发**：tooltip 在按钮 hover 时出现；触发延迟沿 shadcn `tooltip` 默认行为（不本期定制）。
- **R-4.3.5 ActionBar 全量替换**：ActionBar 内部按钮（正式 + dev）**全部**通过 `TooltipButton` 渲染；不掺其他 button 形态、不再出现裸 `<Button>` + 文字。

### 4.4 隐藏桌宠按钮

- **R-4.4.1 入口**：ActionBar 一颗 `TooltipButton`，icon = 适合"隐藏"语义的 lucide icon（design 阶段定具体 icon，如 `EyeOff` / `MinusCircle` 等候选），tooltip "隐藏桌宠"。
- **R-4.4.2 点击行为**：调一个 Tauri invoke（具体命名由 design 决定，倾向 `hide_pet` 或复用 `toggle_pet`）；执行的隐藏动作**与托盘菜单 `toggle_pet` 隐藏 pet 窗时同款**（同一 Rust 函数 / 同一窗口操作），不引入第二条隐藏路径。
- **R-4.4.3 唤回**：本期**不**新增唤回机制；隐藏后用户唯一可见的唤回路径 = 系统托盘"显示/隐藏桌宠"菜单项。
- **R-4.4.4 隐藏与 ActionBar 自身的关系**：点击后 pet 窗整体隐藏，ActionBar 随 pet 窗一起消失（pet 窗 hidden 后无 sprite hover 信号、无浮动 DOM 渲染）；不需要额外动画 / 不需要按钮变态。

### 4.5 打开设置按钮 + 设置窗口骨架

- **R-4.5.1 入口**：ActionBar 一颗 `TooltipButton`，icon = lucide `Settings`（或同语义 icon），tooltip "打开设置"。
- **R-4.5.2 点击行为**：调一个 Tauri invoke `open_settings`（参照现有 `open_chat`）；invoke 内部负责"若窗口未创建则创建、若已隐藏则显示并聚焦"的标准行为，沿 chat 窗约定。
- **R-4.5.3 新窗口注册**：`tauri.conf.json` `windows` 数组新增一项 `settings`（label = `"settings"`，url = `"index.html?app=settings"` 或同款形态，按当前多窗口约定）；窗口大小 / decorations / 是否常规窗口 / 是否 transparent 沿 chat 窗约定（常规窗口、有装饰、不透明）。
- **R-4.5.4 settings 页面入口**：新建 `frontend/src/pages/settings/`（与 chat / pet / bubble / devhub 同级）；入口文件挂载在 `vite.config.ts` 多入口配置（参照其他四个）；React 入口渲染一个最简骨架。
- **R-4.5.5 占位骨架内容**：settings 窗口只渲染：一个标题（如 "设置"）+ 一行 placeholder 文案（如"设置项后续开放"）；不引入 sidebar / 多 tab / 分类 / 任何具体设置项 UI。具体文案由 design 阶段定，但**不超出**这两个元素的范围。
- **R-4.5.6 关闭即隐藏**：用户点窗口关闭按钮，**隐藏不销毁**，下次 invoke `open_settings` 仍是同一窗口实例（沿 chat 窗约定 `WebviewWindow::hide` 而非 `close`）。
- **R-4.5.7 窗口能力最小化**：不引入跨窗口通信、不引入主题同步、不引入 store 接入、不引入路由系统。

### 4.6 现有按钮 icon 化 + dev 按钮位置

- **R-4.6.1 "打开对话"按钮**：原 `<Button variant="outline" size="pill">打开对话界面</Button>` → `<TooltipButton icon={<MessageSquare />} tooltip="打开对话" onClick={onOpenChat} />`（具体 icon 由 design 定）；视觉权重降级为与其他 icon 按钮一致（不保留 outline / 不放大 / 无 label）—— 用户接受。
- **R-4.6.2 dev inject 按钮 icon 化**：💬 短气泡 / 📜 长气泡 → 改为 lucide icon + tooltip "注入短气泡" / "注入长气泡"；仍 `import.meta.env.DEV` gate；prod 环境不渲染。
- **R-4.6.3 dev 按钮位置**：dev inject 按钮在 carousel children 顺序的**末尾**（位于所有正式按钮之后）；分页规则与正式按钮无差异（dev 也参与 N=6 计数）。
- **R-4.6.4 dev / 正式按钮无视觉分隔**：dev 按钮在 carousel 中与正式按钮共用同一容器、无 separator 元素；prod 环境直接消失，不留空位。

### 4.7 既有路径回归

- **R-4.7.1 17a AC-6 等价**：本期完成后 17a AC-6（操作栏 sprite-relative + hover gate）等价通过：操作栏以 sprite 位置为锚浮动 / 默认隐藏 / 鼠标进入 sprite + 操作栏整体 group 才显示 / mouse leave 渐隐。容器布局从垂直改横向后，AC-6 措辞**仍准确**（不需修订 17a 文档）。
- **R-4.7.2 17a 其他 AC 不退化**：AC-1 ~ AC-12 其他项与本期改动无相交，零退化。
- **R-4.7.3 17b 状态机 / Live2D 渲染零影响**：本期仅动 ActionBar 内部 + 新增 settings 窗口，不动 PIXI / sprite / Live2DModel / Codex 兼容 / lip-sync。
- **R-4.7.4 既有 chat / bubble / pet 行为零退化**：chat 窗对话流 / bubble 跟随 / pet 拖拽 / 托盘菜单本身行为不变。
- **R-4.7.5 `computeActionBarPosition.test.ts` 现状**：现有单测以输入 `spriteScreen` + `barSize` 算位置；常量调整后单测应继续通过（算法层不动）；若实际跑测有断言失效，按 §5 测试约束停下来评估。

---

## 5. 使用约束

- **frontend-ui-conventions 硬约束**：[frontend-ui-conventions.mdc](../../../.cursor/rules/frontend-ui-conventions.mdc) 所有规定本期照常生效——`components/ui/` 强制走封装件、新增 shadcn 组件走 `pnpm dlx shadcn@latest add` CLI、颜色全部走 CSS 变量（chip 容器背景 / tooltip / 箭头按钮的所有颜色都不得硬编码）。
- **dev-workflow 单测约束**：[dev-workflow.mdc](../../../.cursor/rules/dev-workflow.mdc) 要求新增/改动核心逻辑必须配单测——本期需要补的单测：carousel 分页计算（如有自写的分页函数）、按钮总数 ≤ N / > N / 首末页箭头渲染条件分支；纯 JSX 拼装 + 已封装件 props 透传不强求补测。
- **测试断言变化处理**：现有 `computeActionBarPosition.test.ts` 沿用；尺寸常量改动后跑测，断言失效则停下来评估（是常量值带出来的预期变化、还是算法被意外破坏），不静默改测。
- **新增依赖最小化**：本期只引入 `lucide-react`（若未装）+ shadcn carousel 拉的依赖（如 `embla-carousel-react`）；不引入额外动画库 / 不引入额外 icon 库。
- **Tauri windows 配置变更最小**：`tauri.conf.json` 仅追加 `settings` 一项；不动其他四个窗口配置。
- **Rust invoke 注册最小**：仅新增 `open_settings`（+ 隐藏桌宠如需新 invoke）；不动其他 invoke / 不动托盘菜单逻辑（继续复用 `toggle_pet`）。
- **跨平台开发脚本**：本期不涉及新增"非写代码"开发操作（无新 dev 启动入口 / 无调试脚本）；沿用现有 `run.sh` / `run.ps1`。
- **真实 LLM 调用授权**：本期不涉及 LLM 调用。

---

## 6. 验收标准

> 本节 AC 全部是**机制层 + UI 行为层**的可观察标准；UI 视觉权衡（chip 看起来好不好看 / 滚动动画顺不顺）由实施期手测把关，不列入 AC。

- **AC-1 ActionBar 横向 chip**：ActionBar 容器渲染为横向布局、有背景（用 CSS 变量、非硬编码颜色）、固定宽度（DevTools inspect 宽度恒定，不随按钮数变）；保留 17a sprite-relative 浮动定位（hover 桌宠形象时容器以 sprite 位置为锚出现）。
- **AC-2 carousel 分页行为**：构造 N=6 时：① 5 个按钮 → 无左右箭头、5 个按钮横铺；② 8 个按钮 → 出现左右箭头、初始仅显示前 6 个、首页时左箭头不渲染、点右箭头一次后显示后 2 个 + 首位 4 个 / 或纯后 2 个（具体一页步长由 design 定但行为可观察）、末页时右箭头不渲染。
- **AC-3 TooltipButton 封装**：`frontend/src/components/ui/tooltip-button/` 文件存在，从 `@/components/ui` re-export；ActionBar 内 grep 不到裸 `<Button>` 文字按钮（全部走 `TooltipButton`）；hover 按钮时显示 tooltip 文案。
- **AC-4 隐藏桌宠按钮**：ActionBar 一颗 icon 按钮（tooltip "隐藏桌宠"）；点击后 pet 窗整体隐藏（不可见 / 不响应 hover）；从系统托盘菜单"显示/隐藏桌宠"点击可恢复显示，桌宠位置 / 状态 / ActionBar 行为与隐藏前等价。
- **AC-5 打开设置按钮 + 设置窗口骨架**：ActionBar 一颗 icon 按钮（tooltip "打开设置"）；点击后弹出一个新窗口（第五个 Tauri 窗口，label `settings`），窗口内渲染占位骨架（标题 + 一行 placeholder 文案）；点击窗口关闭按钮窗口隐藏不销毁；再次点击 ActionBar 设置按钮显示同一窗口实例（不重新创建）。
- **AC-6 dev inject 按钮**：dev 环境（`pnpm dev`）下 ActionBar carousel 中可见 2 个 dev 注入按钮（icon + tooltip "注入短气泡" / "注入长气泡"），位于所有正式按钮之后；prod build（`pnpm build`）后 ActionBar 中不渲染 dev 按钮。
- **AC-7 17a AC-6 等价**：操作栏整体仍 sprite-relative 浮动 + hover gate 显隐 + mouse leave 渐隐；17a AC-6 验证步骤可直接复用（视觉布局从垂直变横向不影响 AC-6 措辞与判定）。
- **AC-8 既有路径零退化**：chat 窗对话流 / bubble 跟随 / pet 拖拽 / 托盘菜单"显示/隐藏桌宠" / "打开对话" / "退出" 三项均行为不变；17a / 17b 其他模块 AC 不需重跑。
- **AC-9 `./scripts/check` 全绿**：lint + typecheck + 前端单测 + Rust build 全绿；新增 `derivePageState` 纯函数单测覆盖 ≤N / >N / 首末页箭头条件分支，通过。
- **AC-10 颜色门禁通过**：本期新增的 ActionBar 容器背景 / chip 边框 / 箭头按钮 / settings 窗口骨架文案颜色全部走 `var(--xxx)` 或 token，`frontend/scripts/check-colors.mjs`（已并入 `scripts/check`）扫描通过。

---

## 7. 已知风险与监测项（不阻塞验收 / 不进 AC）

| # | 风险 / 监测项 | 处理 |
|---|---|---|
| 1 | shadcn `carousel` API 是否能直接满足 R-4.2.1 ~ R-4.2.4（每页 N 个步长 / 箭头按钮自定义 / 滚到头隐藏） | design 阶段先验证；不行则自写并封装到 `components/ui/`，本期不视为风险升级 |
| 2 | 每页 N=6 实测视觉可能局促 / 过宽 | 常量化管理；后续在实施期 / 验收时手测调整，不锁死 |
| 3 | 17a `computeActionBarPosition.test.ts` 在常量变化后断言可能失效 | 跑测发现后停下评估，不静默改测 |
| 4 | dev inject 按钮 + 正式按钮无视觉分隔，dev 环境下可能误点 | 已接受（dev 用户应当能识别 icon + tooltip 区分）；如真实环境碰到问题，后续单独立 |
| 5 | 设置窗口在 macOS / Win 关闭语义差异（关闭按钮在 mac 是左上、Win 是右上） | 沿 chat 窗已有约定，不在本期引入差异化处理 |

---

## 8. 变更记录

| 日期 | 变更内容 | 影响范围 |
|------|---------|---------|
| 2026-06-16 | 实施期 M1.3 发现项目当前 vitest 环境为 node + 未装 RTL/jsdom、`components/ui/` 既有惯例无单测先例。TooltipButton 属"纯 JSX 拼装 + 已封装件 props 透传"沿 [`dev-workflow`](../../../.cursor/rules/dev-workflow.mdc) "纯机械改动可豁免" 豁免单测；ActionBar 分页判定改为抽离纯函数 `derivePageState` 单测覆盖（≤N / >N / 首末页箭头条件分支）。不引入 RTL/jsdom 基建（项目级 testing 基建调整不在 019 范围）。 | §6 AC-9 措辞 |

---

## 文档元信息

- **状态**：已确认（Confirmed）
- **创建时间**：2026-06-16
- **确认时间**：2026-06-16
- **关联需求**：[需求 017](../017-pet-overlay-form-switch/)（ActionBar 当前实现来源）
- **关联探索**：[`desktop-completeness`](../../explorations/desktop-completeness/)（桌面端缺口盘点，本期是其中一小步）
- **关联规则**：[`frontend-ui-conventions`](../../../.cursor/rules/frontend-ui-conventions.mdc) / [`dev-workflow`](../../../.cursor/rules/dev-workflow.mdc) / [`docs-structure`](../../../.cursor/rules/docs-structure.mdc)
- **下一步**：本文档确认后撰写同目录 [`design.md`](./design.md)（技术方案）—— 含 shadcn carousel 可用性调研、TooltipButton API 终稿、容器尺寸常量计算、Tauri windows / Rust invoke 细节。
