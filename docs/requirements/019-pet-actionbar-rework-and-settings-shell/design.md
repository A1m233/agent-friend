# 019 · 桌宠 ActionBar 容器重做 + TooltipButton + 设置窗口骨架 — 技术方案

> Pet ActionBar Rework & Settings Window Shell — Technical Design

---

## 状态

<!-- 草稿（Draft） | 已确认（Confirmed） -->
已确认（Confirmed）

---

## 需求文档

→ [`requirement.md`](./requirement.md)

---

## 1. 现状分析

### 1.1 涉及代码（改动前）

| 文件 | 现状概要 |
| --- | --- |
| `frontend/src/pages/pet/ActionBar.tsx` | 17a 落地的浮动 DOM：垂直 flex / 文字按钮 / 无背景 / `import.meta.env.DEV` gate dev inject 按钮 / hover gate 由 `visible` prop 控制 / `BAR_W=160` `BAR_H_BASE=40` `BAR_H_DEV=124` 三个尺寸常量 |
| `frontend/src/pages/pet/App.tsx` | 集成 ActionBar，传入 `spriteScreen` / `visible` / `onMouseEnter` / `onMouseLeave` / `onOpenChat` / `onInjectShort` / `onInjectLong` |
| `frontend/src/pages/pet/computeActionBarPosition.ts` | sprite-relative 锚算：默认上方居中、屏顶贴墙翻下方；输入 `spriteScreen` + `barSize` 输出 `{left, top}` |
| `frontend/src/pages/pet/computeActionBarPosition.test.ts` | 单测覆盖锚算分支（默认 / 屏顶翻转 / 屏边贴墙等） |
| `frontend/src/components/ui/{button,tooltip,index.ts}` | shadcn 标准件：`Button` 含 `icon-sm` size（32×32）/ `Tooltip` 已拆 `Provider`+`Root`+`Trigger`+`Content`，barrel 出口齐 |
| `frontend/src-tauri/src/lib.rs` | `open_chat` invoke + `show_and_focus_chat`（含 macOS NSApp.activate 加料）+ 托盘 `toggle_pet` menu event handler（hide/show 切换）+ `on_window_event` 仅拦截 chat 窗 CloseRequested |
| `frontend/src-tauri/tauri.conf.json` | windows: pet / bubble / chat 三个；chat 720×640、`visible:false` |
| `frontend/vite.config.ts` | rollupOptions.input: index / pet / chat / bubble 四入口 |
| `frontend/{index,pet,chat,bubble}.html` | 4 个 HTML 入口；mount 自 `src/pages/<page>/main.tsx` |
| `frontend/package.json` | 已装：`lucide-react@1.17.0`（官方 lucide-icons/lucide）、`radix-ui` / shadcn 基础栈；**未装** `embla-carousel-react`（shadcn carousel 依赖） |

### 1.2 不动的接缝

17a / 17b 既有路径**完全不动**：
- ActionBar 浮动定位算法（`computeActionBarPosition` 函数体）
- ActionBar 显隐机制（PIXI sprite hover bridge → `visible` prop → opacity 切换）
- `[data-hit]` 命中机制 & `usePetPassthrough` DOM hit-test
- PIXI / sprite world position / Live2DModel / Codex push 兼容 / lip-sync
- chat 窗 `open_chat` invoke / 托盘菜单 `toggle_pet` menu handler / bubble window
- 015 push subscriber / owner / policy / store / sessionProjection

---

## 2. 方案设计

### 2.1 文件改动清单

| 文件 | 改动类型 | 说明 |
| --- | --- | --- |
| `frontend/src/components/ui/tooltip-button/index.tsx` | **新增** | `TooltipButton` 通用件本体 |
| `frontend/src/components/ui/tooltip-button/tooltip-button.test.tsx` | **新增** | 单测：renders icon + tooltip + onClick |
| `frontend/src/components/ui/carousel/index.tsx` | **新增**（shadcn CLI 拉取） | shadcn `carousel` 标准源码（`pnpm dlx shadcn@latest add carousel`），不手改 |
| `frontend/src/components/ui/index.ts` | 修改 | barrel 出口追加 `tooltip-button` / `carousel` re-export |
| `frontend/src/pages/pet/ActionBar.tsx` | **重写** | 横向 chip + 按钮数组 + carousel/flex 双路径 + 自定义圆形箭头 |
| `frontend/src/pages/pet/computeActionBarPosition.ts` | 修改（仅常量） | 移除 `BAR_W` / `BAR_H_*`；改由 ActionBar 调用方按钮数纯计算传入 |
| `frontend/src/pages/pet/computeActionBarPosition.test.ts` | 必要时同步 | 若仅尺寸常量名/值变化导致断言挂，按值更新；若挂的是算法分支，停下评估 |
| `frontend/src/pages/pet/App.tsx` | 修改 | 追加 `onHidePet` / `onOpenSettings` handler（invoke `hide_pet` / `open_settings`） |
| `frontend/src/pages/settings/main.tsx` | **新增** | React 入口（参 `pages/chat/main.tsx`） |
| `frontend/src/pages/settings/App.tsx` | **新增** | 占位骨架：标题 + 一行 placeholder |
| `frontend/settings.html` | **新增** | HTML 入口（参 `chat.html`） |
| `frontend/vite.config.ts` | 修改 | rollupOptions.input 追加 `settings` |
| `frontend/src-tauri/tauri.conf.json` | 修改 | windows 数组追加 `settings` |
| `frontend/src-tauri/src/lib.rs` | 修改 | 新增 `hide_pet` / `open_settings` invoke + 抽象 `show_and_focus(label)` + `on_window_event` 扩展 chat→matches!(chat\|settings) + invoke handler 注册 |
| `frontend/package.json` / `pnpm-lock.yaml` | 修改（CLI 自动） | shadcn add carousel 顺带装 `embla-carousel-react` |

**不改**：`computeActionBarPosition.ts` 的算法体；17a/17b 其他模块；015/016 任何文件。

---

### 2.2 TooltipButton 通用件

#### 2.2.1 API

```tsx
// frontend/src/components/ui/tooltip-button/index.tsx
import * as React from "react";
import {
  Button,
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui";

type ButtonProps = React.ComponentProps<typeof Button>;

export interface TooltipButtonProps
  extends Omit<ButtonProps, "children"> {
  /** lucide icon 节点（必填） */
  icon: React.ReactNode;
  /** tooltip 文案（必填） */
  tooltip: string;
  /** tooltip 出现方向，默认 "top" */
  tooltipSide?: "top" | "right" | "bottom" | "left";
  /** tooltip 触发延迟（ms），默认沿 TooltipProvider 全局值 0 */
  tooltipDelayMs?: number;
}

export function TooltipButton({
  icon,
  tooltip,
  tooltipSide = "top",
  tooltipDelayMs,
  size = "icon-sm",
  variant = "ghost",
  ...buttonProps
}: TooltipButtonProps) {
  return (
    <Tooltip delayDuration={tooltipDelayMs}>
      <TooltipTrigger asChild>
        <Button size={size} variant={variant} {...buttonProps}>
          {icon}
        </Button>
      </TooltipTrigger>
      <TooltipContent side={tooltipSide}>{tooltip}</TooltipContent>
    </Tooltip>
  );
}
```

要点：

- **默认 `size="icon-sm"` + `variant="ghost"`**：32×32 ghost 按钮，与 chip 容器视觉密度匹配。
- **`className` 透传**：Button 内部用 `cn()`，调用方传 `className="rounded-full"` 可覆盖默认 `rounded-md` —— 用于箭头按钮的圆形差异化。
- **`tooltipDelayMs` 可选 prop**：箭头按钮传 `tooltipDelayMs={500}` 避免频繁滚动时 tooltip 闪烁；功能按钮不传、沿全局 0。
- **不暴露 `shape` 变体**：避免为单个特例引入二级变体；圆形差异由 className 完成。
- **`children` Omit**：`icon` 已是 children 的语义化替代，避免两路传值歧义。
- **`TooltipProvider` 不在件内部嵌套**：调用方在 ActionBar 外层包一次（与 sidebar / sheet 等其他 UI 件用法一致）。

#### 2.2.2 barrel 出口更新

```ts
// frontend/src/components/ui/index.ts —— 末尾追加
export * from "./tooltip-button";
export * from "./carousel";
```

#### 2.2.3 单测豁免（变更 2026-06-16）

实施期 M1.3 发现项目当前 vitest 环境为 `node` + 未装 `@testing-library/react` / `jsdom`、`components/ui/` 既有惯例**无单测先例**。TooltipButton 属于"纯 JSX 拼装 + 已封装件 props 透传"，沿 [`dev-workflow`](../../../.cursor/rules/dev-workflow.mdc) "纯机械改动可豁免单测"豁免；不引入 RTL/jsdom 基建（项目级 testing 基建调整不在 019 范围）。

替代验证：TypeScript 类型检查（icon / tooltip / size / variant 等 props 强制） + ActionBar 集成后**手测**（M6.3 AC-3 验证 hover tooltip 出现、点击触发 onClick）。

真有断言价值的 ActionBar **分页判定逻辑**抽离为纯函数 `derivePageState`（见 §2.4.x），在 node 环境下跑 `.test.ts`，覆盖 ≤N / >N / 首末页箭头条件分支。

---

### 2.3 shadcn carousel 拉取与 ActionBar 适配

#### 2.3.1 拉取命令

```bash
pnpm dlx shadcn@latest add carousel
```

副作用：自动装 `embla-carousel-react`、生成 `frontend/src/components/ui/carousel/index.tsx`（shadcn 官方源码）。**不手改源码**（沿 frontend-ui-conventions 约束：shadcn 件不手抄）。

#### 2.3.2 不直接用 shadcn 自带箭头

shadcn `CarouselPrevious` / `CarouselNext` 在首末位是 `disabled` 不是 `unmount`，违反 R-4.2.3 "首末页箭头不渲染"。解法：**ActionBar 内部不渲染 shadcn 自带箭头**，自己用 `setApi` 拿 embla API + `canScrollPrev` / `canScrollNext` 条件渲染圆形 `TooltipButton`。

#### 2.3.3 ActionBar 内部 carousel 用法

```tsx
// 关键片段
const [api, setApi] = React.useState<CarouselApi>();
const [canPrev, setCanPrev] = React.useState(false);
const [canNext, setCanNext] = React.useState(false);

React.useEffect(() => {
  if (!api) return;
  const update = () => {
    setCanPrev(api.canScrollPrev());
    setCanNext(api.canScrollNext());
  };
  update();
  api.on("select", update);
  api.on("reInit", update);
  return () => { api.off("select", update); api.off("reInit", update); };
}, [api]);

// JSX
<Carousel
  opts={{ slidesToScroll: PAGE_SIZE, align: "start", loop: false }}
  setApi={setApi}
  className="overflow-hidden"
>
  <CarouselContent className="-ml-1">
    {buttons.map((btn, i) => (
      <CarouselItem key={i} className={`pl-1 basis-1/${PAGE_SIZE}`}>
        {btn}
      </CarouselItem>
    ))}
  </CarouselContent>
</Carousel>
```

要点：

- `slidesToScroll: PAGE_SIZE`（=6）→ 每点一次箭头滚一页（embla 内置支持）。
- `loop: false` → 禁用循环（沿 R-4.2.3 滚到头停）。
- `basis-1/6`（动态由 PAGE_SIZE 推出）→ 每个 item 占容器 1/6 宽度，正好 6 个一行。
- 自定义箭头不在 `<Carousel>` 内部，而是在 `<Carousel>` 外层 chip 容器里：左箭头条件 `canPrev` 渲染、右箭头条件 `canNext` 渲染。

---

### 2.4 ActionBar 重写

#### 2.4.1 接口扩展

```tsx
interface Props {
  // 17a 既有，保留
  spriteScreen: { x: number; y: number; w: number; h: number };
  visible: boolean;
  onMouseEnter: MouseEventHandler<HTMLDivElement>;
  onMouseLeave: MouseEventHandler<HTMLDivElement>;
  onOpenChat: () => void;
  onInjectShort: () => void;
  onInjectLong: () => void;
  // 新增
  onHidePet: () => void;
  onOpenSettings: () => void;
}
```

#### 2.4.2 按钮数组构建

```tsx
import { MessageSquare, EyeOff, Settings, MessageSquareDashed,
         ScrollText, ChevronLeft, ChevronRight } from "lucide-react";

const PAGE_SIZE = 6;
const ICON_BTN = 32;   // size-8 = 32px
const GAP = 4;          // gap-1
const PAD_X = 8;        // px-2
const PAD_Y = 6;        // py-1.5
const ARROW_AREA = ICON_BTN + GAP; // 圆箭头 32 + 4 间距

interface BtnDef { icon: ReactNode; tooltip: string; onClick: () => void; }

const buttons: BtnDef[] = [
  { icon: <MessageSquare />, tooltip: "打开对话", onClick: onOpenChat },
  { icon: <EyeOff />,        tooltip: "隐藏桌宠", onClick: onHidePet },
  { icon: <Settings />,      tooltip: "打开设置", onClick: onOpenSettings },
];

if (import.meta.env.DEV) {
  buttons.push(
    { icon: <MessageSquareDashed />, tooltip: "注入短气泡", onClick: onInjectShort },
    { icon: <ScrollText />,           tooltip: "注入长气泡", onClick: onInjectLong },
  );
}

const needsCarousel = buttons.length > PAGE_SIZE;
```

prod 总数 = 3，dev 总数 = 5 —— 当前**两端都 ≤ PAGE_SIZE**，初始环境下不会触发 carousel；carousel 路径是为后续按钮扩展（如 020 唤回 / 形象状态机相关入口等）预留的。**单测必须覆盖 buttons.length > PAGE_SIZE 的分支**（构造 mock 8 个按钮验证）。

#### 2.4.3 容器宽度（两段宽语义 · 决策点）

按 declare 锁定的方案①：

```tsx
const chipW = needsCarousel
  ? PAD_X * 2 + ARROW_AREA * 2 + PAGE_SIZE * ICON_BTN + (PAGE_SIZE - 1) * GAP
  : PAD_X * 2 + buttons.length * ICON_BTN + Math.max(0, buttons.length - 1) * GAP;
const chipH = PAD_Y * 2 + ICON_BTN;  // = 44px
```

代入：

| 场景 | 按钮数 | needsCarousel | chipW |
| --- | --- | --- | --- |
| prod 默认 | 3 | false | 8+3×32+2×4+8 = **120px** |
| dev 默认 | 5 | false | 8+5×32+4×4+8 = **192px** |
| 后续扩展（>6） | 8 | true | 8+32+4+6×32+5×4+4+32+8 = **300px** |

`computeActionBarPosition` 接受 `barSize: { w, h }` 输入不变，调用方传 `{ w: chipW, h: chipH }`；算法层（默认上方居中 / 屏顶贴墙翻下方）保持不变。

#### 2.4.4 JSX 整体结构

```tsx
return (
  <TooltipProvider delayDuration={0}>
    <div
      data-hit
      style={style}
      className={`flex items-center gap-1 rounded-2xl bg-surface/95 border border-border shadow-lg px-2 py-1.5 transition-opacity ${visibleCls}`}
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
    >
      {needsCarousel && canPrev && (
        <TooltipButton
          icon={<ChevronLeft />}
          tooltip="上一页"
          tooltipDelayMs={500}
          className="rounded-full"
          onClick={() => api?.scrollPrev()}
          data-hit
        />
      )}

      {needsCarousel ? (
        <Carousel opts={{ slidesToScroll: PAGE_SIZE, align: "start", loop: false }} setApi={setApi}>
          <CarouselContent className="-ml-1">
            {buttons.map((b, i) => (
              <CarouselItem key={i} className={`pl-1 basis-1/${PAGE_SIZE}`}>
                <TooltipButton icon={b.icon} tooltip={b.tooltip} onClick={b.onClick} data-hit />
              </CarouselItem>
            ))}
          </CarouselContent>
        </Carousel>
      ) : (
        <div className="flex items-center gap-1">
          {buttons.map((b, i) => (
            <TooltipButton key={i} icon={b.icon} tooltip={b.tooltip} onClick={b.onClick} data-hit />
          ))}
        </div>
      )}

      {needsCarousel && canNext && (
        <TooltipButton
          icon={<ChevronRight />}
          tooltip="下一页"
          tooltipDelayMs={500}
          className="rounded-full"
          onClick={() => api?.scrollNext()}
          data-hit
        />
      )}
    </div>
  </TooltipProvider>
);
```

要点：

- **`data-hit` 在容器 + 每颗按钮**：DOM hit-test 优先承担命中（沿 17a R-4.5）。
- **chip 颜色**：`bg-surface/95` / `border-border` / `shadow-lg` 全 token，颜色 guard 通过。
- **`rounded-2xl`**：chip 整体大圆角；箭头按钮 `rounded-full` 进一步圆形（差异化）。
- **`transition-opacity` + `visibleCls`**：沿 17a 显隐机制（`opacity-100 pointer-events-auto` ↔ `opacity-0 pointer-events-none`）。

#### 2.4.5 `App.tsx` 集成

```tsx
// pages/pet/App.tsx —— 在 ActionBar 调用处追加 handler
import { invoke } from "@tauri-apps/api/core";

<ActionBar
  // ...既有 props
  onHidePet={() => invoke("hide_pet")}
  onOpenSettings={() => invoke("open_settings")}
/>
```

#### 2.4.6 分页判定纯函数 `derivePageState`（变更 2026-06-16 新增）

抽离 ActionBar 内部"按钮数 + 当前页 → 渲染哪些箭头"的判定逻辑为**纯函数**（不依赖 embla / React），独立 node `.test.ts` 单测覆盖。

```ts
// frontend/src/pages/pet/actionBarPaging.ts
export interface PageState {
  /** 按钮总数 > pageSize 时为 true，需启用 carousel；否则纯 flex 平铺 */
  needsCarousel: boolean;
  /** 是否渲染"上一页"箭头（首页或不需要 carousel 时为 false） */
  showPrev: boolean;
  /** 是否渲染"下一页"箭头（末页或不需要 carousel 时为 false） */
  showNext: boolean;
  /** 总页数（≤ pageSize 时为 1） */
  totalPages: number;
}

export function derivePageState(params: {
  buttonCount: number;
  pageSize: number;
  /** 当前页索引（0-based）；needsCarousel=false 时被忽略 */
  currentPage: number;
}): PageState {
  const { buttonCount, pageSize, currentPage } = params;
  if (buttonCount <= pageSize) {
    return { needsCarousel: false, showPrev: false, showNext: false, totalPages: 1 };
  }
  const totalPages = Math.ceil(buttonCount / pageSize);
  return {
    needsCarousel: true,
    showPrev: currentPage > 0,
    showNext: currentPage < totalPages - 1,
    totalPages,
  };
}
```

ActionBar 内部使用：

```tsx
const [api, setApi] = React.useState<CarouselApi>();
const [currentPage, setCurrentPage] = React.useState(0);

React.useEffect(() => {
  if (!api) return;
  const update = () => setCurrentPage(api.selectedScrollSnap());
  update();
  api.on("select", update);
  api.on("reInit", update);
  return () => { api.off("select", update); api.off("reInit", update); };
}, [api]);

const { needsCarousel, showPrev, showNext } = derivePageState({
  buttonCount: buttons.length,
  pageSize: PAGE_SIZE,
  currentPage,
});
```

embla 的 `selectedScrollSnap()` 返回**当前可视的第一个 slide 索引**——但因为 `slidesToScroll: PAGE_SIZE` 每滚一页就跳 PAGE_SIZE 个 slide，所以 `currentPage = Math.floor(selectedSnap / PAGE_SIZE)`。

实测 embla 在 `slidesToScroll: N` 时 `scrollSnapList()` 已经聚合好分页位（不是逐 slide 一个 snap），可以直接：

```tsx
const update = () => setCurrentPage(api.selectedScrollSnap());
// scrollSnapList().length === totalPages（embla 自带聚合）
```

`derivePageState` 的 `currentPage` 直接对接 embla 的 `selectedScrollSnap()`。

---

### 2.5 settings 窗口骨架

#### 2.5.1 HTML 入口

```html
<!-- frontend/settings.html —— 参 chat.html -->
<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>agent-friend · 设置</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/pages/settings/main.tsx"></script>
  </body>
</html>
```

#### 2.5.2 React 入口

```tsx
// frontend/src/pages/settings/main.tsx
import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "@/styles/global.css";  // 沿用项目主题 / token

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
```

#### 2.5.3 占位骨架

```tsx
// frontend/src/pages/settings/App.tsx
export default function App() {
  return (
    <div className="min-h-screen flex flex-col items-center justify-center gap-2 bg-bg text-fg">
      <h1 className="text-xl font-semibold">设置</h1>
      <p className="text-sm text-muted-fg">设置项后续开放</p>
    </div>
  );
}
```

颜色 token：`bg-bg` / `text-fg` / `text-muted-fg`（如未在主题文件中定义则补；颜色 guard 强制走 token）。

#### 2.5.4 Vite 入口注册

```ts
// frontend/vite.config.ts —— rollupOptions.input 追加
input: {
  index: resolve(__dirname, "index.html"),
  pet: resolve(__dirname, "pet.html"),
  chat: resolve(__dirname, "chat.html"),
  bubble: resolve(__dirname, "bubble.html"),
  settings: resolve(__dirname, "settings.html"),  // 新增
},
```

#### 2.5.5 Tauri 窗口注册

```jsonc
// frontend/src-tauri/tauri.conf.json windows 数组追加
{
  "label": "settings",
  "url": "settings.html",
  "title": "agent-friend · 设置",
  "width": 720,
  "height": 640,
  "resizable": true,
  "visible": false,    // 启动不显示，由 invoke open_settings 拉起
  "fullscreen": false
}
```

参 chat 窗约定（常规窗口、有装饰、不透明、resizable）。

---

### 2.6 Rust invoke

#### 2.6.1 抽象 `show_and_focus(label)`

把现有 `show_and_focus_chat` 抽象为 `show_and_focus(app, label)`：

```rust
/// 显示并聚焦指定 label 的 webview window（chat / settings 复用）。
///
/// macOS 下额外调 NSApp.activateIgnoringOtherApps:true，让 app 在 Accessory
/// activation policy（17a 设的）下也能 activate → 目标窗成 key window 收键盘事件。
/// 不切 policy（避免 17a 副作用复发：临时切 Regular 触发 macOS window reorder）。
fn show_and_focus(app: &tauri::AppHandle, label: &'static str) -> Result<(), String> {
    let app_clone = app.clone();
    app.run_on_main_thread(move || {
        if let Some(w) = app_clone.get_webview_window(label) {
            #[cfg(target_os = "macos")]
            {
                use objc2::{class, msg_send, runtime::AnyObject};
                let nsapp_class = class!(NSApplication);
                let nsapp: *mut AnyObject = unsafe { msg_send![nsapp_class, sharedApplication] };
                if !nsapp.is_null() {
                    unsafe { let _: () = msg_send![nsapp, activateIgnoringOtherApps: true]; }
                }
            }
            let _ = w.show();
            let _ = w.set_focus();
        }
    })
    .map_err(|e| e.to_string())?;
    Ok(())
}

#[tauri::command]
fn open_chat(app: tauri::AppHandle) -> Result<(), String> {
    show_and_focus(&app, "chat")
}

#[tauri::command]
fn open_settings(app: tauri::AppHandle) -> Result<(), String> {
    show_and_focus(&app, "settings")
}
```

旧 `show_and_focus_chat` 删除；其内部对 chat 窗硬编码的 macOS 加料逻辑迁移到通用 `show_and_focus`，对 chat / settings 都生效。**托盘 menu handler `"open_chat"` 分支也跟着改用 `show_and_focus(app, "chat")`**（一行改动）。

#### 2.6.2 `hide_pet` invoke

```rust
/// 桌宠 ActionBar "隐藏桌宠" 按钮调用。语义：单向隐藏（不切换）。
///
/// **唤回路径**：本期唯一 = 系统托盘菜单 "显示/隐藏桌宠"（toggle_pet）。
/// 从桌面直接唤回桌宠是 [issue NNN] 跟踪的独立缺口，独立立项。
#[tauri::command]
fn hide_pet(app: tauri::AppHandle) -> Result<(), String> {
    if let Some(w) = app.get_webview_window("pet") {
        let _ = w.hide();
    }
    Ok(())
}
```

issue 编号在 019 实施期立 issue 后回填。

#### 2.6.3 invoke handler 注册

`run()` 内 dev / release 两段 `generate_handler!` 各加 `hide_pet` / `open_settings`。**dev 段** 同时保留 `inject_test_envelope`（17a 既有）。

#### 2.6.4 `on_window_event` 关闭即隐藏扩展

```rust
.on_window_event(|window, event| {
    if matches!(window.label(), "chat" | "settings") {
        if let tauri::WindowEvent::CloseRequested { api, .. } = event {
            api.prevent_close();
            let _ = window.hide();
        }
    }
})
```

---

### 2.7 icon 选型（lucide-react）

| 用途 | icon | 备注 |
| --- | --- | --- |
| 打开对话 | `MessageSquare` | 标准对话气泡 |
| 隐藏桌宠 | `EyeOff` | "看不见"语义直观 |
| 打开设置 | `Settings` | 标准齿轮 |
| dev · 注入短气泡 | `MessageSquareDashed` | 与正式对话气泡区分 |
| dev · 注入长气泡 | `ScrollText` | 长文本 / 卷轴感 |
| 上一页 | `ChevronLeft` | 圆形按钮 |
| 下一页 | `ChevronRight` | 圆形按钮 |

如实施期发现某 icon 视觉不符直觉，可在 020 之前不开 PR 就地换 lucide 同语义 icon（不计入需求变更，但记 progress.md 实现日志）。

---

### 2.8 测试策略

| 测试位置 | 覆盖 |
| --- | --- |
| `pages/pet/actionBarPaging.test.ts`（新） | `derivePageState({buttonCount, pageSize, currentPage})` 纯函数：`buttonCount ≤ pageSize` → `needsCarousel=false`、`showPrev=false`、`showNext=false`；`> pageSize` → `needsCarousel=true`、首页（currentPage=0）`showPrev=false`、末页 `showNext=false`、中间页两边都 true；`totalPages` 计算正确 |
| ~~`tooltip-button.test.tsx`~~ | 豁免（变更 2026-06-16，见 §2.2.3）：项目 vitest 环境 node + 未装 RTL/jsdom；TooltipButton 属 "纯 JSX 拼装 + props 透传"，沿 dev-workflow 豁免。手测在 M6.3 AC-3 覆盖 |
| `computeActionBarPosition.test.ts` | 跑现有断言 → 通过则不动，挂了停下评估（§3.3 风险 2） |
| 整体 `./scripts/check` | lint + typecheck + 前端单测 + Rust build + colors guard 全绿 |

**carousel 内部行为**（embla scrollSnapList、动画时序等）不补单测，相信 embla 上游测试。
**ActionBar 整体行为**（chip 容器渲染、icon 显示、按钮点击）由 M6.3 手测覆盖（项目无 RTL 基建）。

---

## 3. 影响分析

### 3.1 既有路径影响

| 路径 | 影响 | 处理 |
| --- | --- | --- |
| 17a ActionBar `visible` / hover gate / `[data-hit]` | 完全不动 | 直接复用，AC-7 等价通过 |
| 17a `computeActionBarPosition` 算法 | 算法不动，输入 barSize 改 | 现有单测断言层已抽象为输入参数；常量值变化不影响算法分支 |
| chat 窗 `open_chat` invoke 行为 | 内部抽象 `show_and_focus` 后等价 | 无可观察行为差异；托盘 "open_chat" 同步改通用版 |
| 托盘 "显示/隐藏桌宠" `toggle_pet` | 完全不动 | 与新 `hide_pet` 是两条独立路径，通过同一个 `pet.hide()` / `pet.show()` 操作 pet 窗 |
| 015 / 016 / 17a / 17b 其他模块 | 零相交 | 不需回归 |

### 3.2 跨平台影响

- **macOS**：抽象 `show_and_focus` 含原 chat 窗的 NSApp.activate 加料；settings 窗也享受同款（避免 17a Accessory policy 副作用复发）。
- **Win / Linux**：`show_and_focus` 在 non-macOS 退化为 `show()` + `set_focus()`，与 chat 窗当前行为完全一致。
- **新增 settings 窗口**：常规窗口、不透明、有装饰、不 alwaysOnTop、不 skipTaskbar——与 17a 整屏 transparent overlay 路径完全无关、不受 NSPanel / fullScreenAuxiliary 等加料影响；跨平台一致。

### 3.3 风险（不阻塞验收）

| # | 风险 | 处理 |
| --- | --- | --- |
| 1 | shadcn carousel CLI 拉取时若 shadcn registry 网络受限失败 | 回退方案：手动 `pnpm add embla-carousel-react` + 从 shadcn 官方 docs 复制 carousel 源码到 `components/ui/carousel/index.tsx`（shadcn 件源码就是给"复制粘贴"用的，但应优先走 CLI 沿 frontend-ui-conventions） |
| 2 | `computeActionBarPosition.test.ts` 现有断言可能因 barSize 期望值绑定失效 | 跑测后**先看挂的是哪条断言** —— 若是输入数据本身（barSize.w/h 期望值变了），同步改测；若是算法分支判定（如屏顶翻转触发条件），停下评估算法是否被破坏 |
| 3 | embla `slidesToScroll: 6` + `basis-1/6` 在某些容器宽度下可能出现亚像素错位 | 实施期手测；如视觉错位，design 阶段不解决，在 progress.md 标"已知小问题"，本期不阻塞 AC |
| 4 | settings 窗口主题（深 / 浅）在不与 chat / pet 同步切换时可能视觉割裂 | 本期不引入跨窗口主题同步，settings 窗在初次创建时读 system / 默认 token；后续主题需求统一处理 |
| 5 | "从桌面直接唤回桌宠"缺口仍在 | 本期 §4 后续工作 → 立 issue 跟踪、独立立项；本期 hide_pet 注释里指向 issue |

### 3.4 性能 / 安全 / 资源

- **新增依赖体积**：`embla-carousel-react` ~14kb gzipped，本期可接受。
- **运行时开销**：carousel 仅在 buttons.length > PAGE_SIZE 时渲染；当前 prod / dev 都不触发，零运行时影响。
- **Rust 抽象成本**：`show_and_focus(label)` 净行数减少（去掉 `show_and_focus_chat`，加 `show_and_focus`）。

---

## 4. 后续工作（不在本期范围）

### 4.1 桌面唤回桌宠（issue → 020）

declare 阶段确认：本期 hide_pet 后唤回路径仅托盘菜单。"从桌面直接唤回"是真实产品缺口（用户确认想要、但交互未定型），独立立项。**实施期落 progress.md M0 之前先登记 issue**：

- 路径：`docs/issues/<NNN>-pet-recall-from-desktop/README.md`（编号在 issues 内连续递增）
- 内容：现状（隐藏后无法直接从桌面唤回）/ 候选方案（屏幕边角小尾巴 / 全局热键 / 点托盘图标直接唤起 / 屏幕角落驻留小图标）/ 标 "待立项" 状态
- 在 `lib.rs` `hide_pet` 注释中指向该 issue

### 4.2 设置窗口内容填充

settings 窗口骨架完成后，后续设置项（主题切换、persona 配置、API key、模型选择等）按需独立立项，依次往骨架里加。本期不预留任何具体设置项接口。

### 4.3 ActionBar 后续按钮

形象状态机入口 / 右键菜单内容 / 双击反应 / 跨 Space 浮动开关等 desktop-completeness Tier 1/2/3 项目落地时，按钮直接加入 buttons 数组；当总数 > PAGE_SIZE 时 carousel 自动接管（本期已为这个扩展铺好路径）。

---

## 5. 变更记录

| 日期 | 变更内容 | 是否需要重新实现 |
|------|---------|----------------|
| 2026-06-16 | M1.3 实施期发现项目 vitest 环境为 node + 未装 RTL/jsdom，`components/ui/` 既有惯例无单测。TooltipButton 单测豁免（沿 dev-workflow "纯 JSX 拼装 + props 透传"）；ActionBar 分页判定改抽离为 `derivePageState` 纯函数（新增 §2.4.6 设计），单测覆盖 ≤N / >N / 首末页箭头分支。§2.2.3 / §2.8 同步更新；同步 requirement.md AC-9。 | 否（不重做已有实现，新增 derivePageState 设计） |

---

## 文档元信息

- **状态**：已确认（Confirmed）
- **创建时间**：2026-06-16
- **确认时间**：2026-06-16
- **关联需求**：[`requirement.md`](./requirement.md)
- **下一步**：本文档确认后撰写同目录 [`progress.md`](./progress.md)（实施进度）。
