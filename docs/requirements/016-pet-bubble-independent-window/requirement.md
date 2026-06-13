# 016 · 桌宠气泡独立窗承载（pet bubble independent window）

> Pet Bubble Independent Window
>
> 把 015 真跑暴露的"气泡装不进 pet 主窗"问题，从凑合 hot-fix（加大主窗 480×460）升级到承载形态层修复：**气泡走独立 bubble window**，pet 主窗回归 240×320，只承载形象本身。本期承接 [issue 005](../../issues/005-pet-bubble-window-sizing/)，是 015 之后桌宠承载形态的体验层封口。

---

## 状态

<!-- 草稿（Draft） | 已确认（Confirmed） -->
已确认（Confirmed）

---

## 1. 背景

### 1.1 现状

[需求 015](../015-desktop-pet-bubble-and-conversation-owner/) 已经把桌宠气泡机制层端到端落地（push 通道 → owner → policy → store → `<PetBubble />` 组件），9 条 AC 全部跑通、`COMPLETED`。但 M15.8 真跑暴露一个承载形态层缺陷：

- 015 design.md §6.1 一开始就定 "pet 窗 240×320 + 气泡 `max-w-[280px]`" —— 280 > 240 必然超界，气泡左右被 webview 边界裁切。
- 当前 hot-fix（[`4e8ac2d`](../../../) commit）把 pet 主窗加大到 480×460 让气泡能完整渲染。

详细分析见 [issue 005](../../issues/005-pet-bubble-window-sizing/)；本需求**不复述**。

### 1.2 Hot-fix 的真问题

issue 005 已列：

- **浪费桌面占地**：即便透明，window 仍在 macOS 占空间，影响 mission control / 拖动其他 app 时的窗口感知。
- **长气泡仍可能超界**：expanded 全文超过 460px 高度时仍被裁。
- **不能动态适配**：短文本时 480 太空、长文本时不够。
- **形象拖拽区与窗口范围错位**：pet 形象 hit-test 还是 160px 圆形，但 window 480 → 拖空白边时语义模糊。
- **违反"window 应匹配内容大小"的设计原则**。

### 1.3 这次要做什么

按 issue 005 倾向的方案 A、并结合本期调研对工业级实现路径的核对（参考 BongoCat 21.4k stars / Hyprnote / tauri-nspanel 等生产用例），落地"**独立 bubble window**"承载形态：

- **Pet 主窗回归 240×320**，只承载形象本身（含拖拽 / hover 操作栏 / 透明区穿透）。
- **新增独立 bubble window**：transparent + alwaysOnTop + 无边框 + 不进任务栏，按需 `show()/hide()`，按文本长度动态调整 size，跟随 pet 主窗位置移动。
- **气泡渲染逻辑保留**：015 已经写好的 `<PetBubble />` UI / `usePetBubbleStore` 状态机 / policy 路由全部**保留**，改造点是把组件从 pet 窗内 absolute 定位挂到 bubble 窗 root。
- **跨平台 first-class**：macOS + Windows + Linux 都要 work，不接受"macOS-only"方案。

### 1.4 与 015 的关系

015 是机制层 Tier 0 封口（**信息从哪儿来、按什么规则到哪儿去**），AC 全部成立、所有代码保留。
016 是承载形态层修复（**信息以什么物理形态呈现**）。

不开新通路、不改 push 协议、不动 sessionProjection / policy / push subscriber。**改的只是"气泡装在哪个 window 里"那一层**。015 的 9 条 AC 在本期完成后必须仍然全部成立（**回归测试** 验收）。

### 1.5 与 issue 005 的关系

issue 005 已经把现象、根因、可选修复方向、备忘清单都登记齐了。016 是它的落地：完成后回到 issue 005 把状态置 `resolved` + 写修复指向。

### 1.6 跨平台定位

agent-friend 是跨平台桌面应用，**Windows 是 first-class 平台、不是 fallback**。

本期承载形态选择已锁定走 **Tauri 跨平台 WebviewWindow API 一份代码** 路径（建 window / `show`/`hide` / `set_size` / `set_position` / 透明 / alwaysOnTop / `outer_position` 都是 Tauri 帮三端 wrap 好的 API）——不接受 macOS-only 的实现路径（如纯 NSPanel 方案）。

**唯一的平台分支**：015 已有的"跨 Space + 浮全屏 app 上层"用 `#[cfg(target_os = "macos")]` 调 NSWindow `setLevel` 的小段加料，本期在 bubble window 上**复用同一段**——Win / Linux 上这段是 no-op，bubble window 在那两个平台用 Tauri 原生 alwaysOnTop 兜底。

**本期 AC 验证范围**：macOS 端到端必过；Win / Linux 没有开发机做手工真跑 AC 验证，**真跑验证留下个需求**（沿用 015 AC-7 边界）——但 cargo build / typecheck / 单测 / lint 在三平台 CI 上都要全绿。

---

## 2. 本期范围（In Scope）

| 模块 | 目标 |
| --- | --- |
| **Pet 主窗尺寸回归** | pet 主窗物理尺寸从当前 hot-fix 的 480×460 回到 240×320；只承载形象 + 拖拽 + 操作栏 |
| **独立 bubble window** | 新增第二个 webview window（label="bubble"），transparent + alwaysOnTop + 无边框 + skipTaskbar；初始 hidden；接收主动轮气泡内容时 show、自动消失 / 用户关闭时 hide |
| **气泡 window 尺寸适配** | bubble window 按当前气泡文本长度动态调整 size（短文本紧凑、长文本展开 / expanded 状态自适应），不裁切 |
| **气泡 window 位置跟随** | bubble window 在 pet 主窗位置变化时同步位移；用户拖 pet 形象 → 气泡跟着移动，二者视觉上始终保持稳定相对位置 |
| **跨 Space / 全屏浮动** | bubble window 跟随用户切 Space、悬浮全屏 app 之上（沿用 015 R-4.7 的 macOS 实现路径，加在 bubble 窗上） |
| **气泡 UI 组件保留** | `<PetBubble />` / `usePetBubbleStore` / `petBubblePolicy` / push subscriber 全部保留；改造点仅在"挂载位置"和"size 上报给 Rust" |
| **跨平台机制层覆盖** | bubble window 的建/显隐/size/位置跟随/透明/置顶/不进任务栏**用 Tauri 跨平台 API 一份代码**搞定；只有跨 Space + 浮全屏的 `#[cfg(target_os = "macos")]` 小段加料沿用 015 现有做法 |
| **回归测试** | 015 全部 9 条 AC 在 016 完成后仍然通过 |

---

## 3. 非目标（Out of Scope）

以下本期**明确不做**，避免范围蔓延：

- **macOS NSPanel 路径**：调研已证 NSPanel 是 macOS-only，与本期"跨平台 first-class"约束冲突；未来 macOS 体验优化想叠 NSPanel，留下个 macOS 专项需求。
- **Windows / Linux 端到端真跑验证**：本期没有 Win / Linux 开发机的硬约束，端到端验证留下个跨平台 spike 需求（沿用 015 边界）。机制层代码要存在 + 经 CI typecheck/lint 即可。
- **气泡内输入框（用户在桌宠头上回复 agent）**：与 IME 焦点 / 键盘事件耦合，超本期承载形态范围，留下个需求。
- **气泡形象联动（speaking 表情 / 动画）**：与气泡 window 形态正交，沿用 015 出 scope 时的判断。
- **bubble window 的产品层细节**：背景毛玻璃 / 阴影 / hover 互动 / 多气泡排队动画等"气泡好看不好看"的判断不在本期；本期是承载形态升级，UI 表现沿用 015 现状。
- **形象的尺寸 / 位置持久化**：与本期机制正交，沿用 015 出 scope 时的判断。
- **bridge / engine / 015 已有的 owner / policy / push subscriber 任何改动**：本期只动承载层。
- **issue 006（bedtime prompt 文案被 history 拽偏）+ issue 007（macOS fullscreen overlay 限制）**：与本期形态正交，不顺手处理。

---

## 4. 核心需求详述

以下需求点（R-级）只讲"做什么 / 做到什么效果"；具体接口形态、位置跟随机制、size 控制细节、透明窗渲染 workaround 等由 [`design.md`](./design.md) 决定。

### 4.1 Pet 主窗回归

- **R-4.1.1 物理尺寸**：pet 主窗 width × height = 240 × 320（恢复 015 design.md §6.1 原始尺寸，干掉 hot-fix 的 480×460）。
- **R-4.1.2 形象 hit-test 与窗口范围一致**：形象拖拽 / hover 操作栏 / 透明区穿透行为零改动；窗内不再承载气泡，因此不再有"形象 hit-test 范围与 window 大小错位"的语义模糊问题。

### 4.2 独立 bubble window

- **R-4.2.1 配置形态**：第二个 webview window（label="bubble"），transparent + alwaysOnTop + decorations:false + skipTaskbar:true + shadow:false。
- **R-4.2.2 默认 hidden**：应用启动时 bubble window 不可见；没有气泡内容时也不可见。
- **R-4.2.3 显隐时机**：当 015 的 `usePetBubbleStore` 进入 `showing` / `expanded` 状态时 bubble window show；进入 `idle` 状态时 hide。show/hide 与 store 状态严格同步。
- **R-4.2.4 不抢焦点**：bubble window show 时**不**抢键盘焦点；用户当前在 chat 窗 / 其他 app 输入时弹气泡不被打断。
- **R-4.2.5 透明区穿透**：bubble window 透明区不阻挡桌面交互（点击空白能落到下方 app）；气泡实心区（卡片 + 按钮）正常接收点击。
- **R-4.2.6 内容承载**：bubble window 渲染 015 已有的 `<PetBubble />` 组件 + `usePetBubbleStore`；组件代码 / store / policy / push subscriber 路由不变。

### 4.3 Bubble window 尺寸适配

- **R-4.3.1 size 随内容**：bubble window 在显示气泡时按当前文本长度调整 size——短文本紧凑、长文本（含 expanded 状态全文）按内容高度自适应。
- **R-4.3.2 不裁切**：任意长度文本（含 expanded 全文）能完整渲染，不被 window 边界裁掉。
- **R-4.3.3 size 上限**：定一个合理上限（如 `max-h-[480px]` + 内部滚动），防止极端长文本把屏幕挤满；具体值由 `design.md` 决定。
- **R-4.3.4 切换内容时无视觉撕裂**：从一条气泡切到下一条（store seq+1）时，size 变化平滑、不出现透明窗白闪 / 残影（依赖透明窗渲染 workaround，由 `design.md` 决定）。

### 4.4 Bubble window 位置跟随

- **R-4.4.1 跟随 pet 主窗**：用户拖 pet 主窗（形象）时，bubble window 位置同步移动；二者视觉上保持稳定相对定位（默认贴 pet 形象上方，参数化的相对偏移见 `design.md`）。
- **R-4.4.2 跟随精度**：拖拽过程中视觉上感知不到明显错位（具体延迟上限 / 帧率由 `design.md` 决定，跨平台等价）。
- **R-4.4.3 屏顶贴墙翻转**：bubble window 贴顶 / 贴底时自动翻转到 pet 形象的另一侧，保证气泡始终可见；翻转判定与 015 现有 `flipBelow` 逻辑等价。
- **R-4.4.4 跨平台机制等价**：位置跟随在 macOS / Windows / Linux 上行为等价；机制层不留 macOS-only 路径（具体实现见 `design.md`，调研已锁定走 Rust 侧轮询主窗坐标的路径）。

### 4.5 跨 Space / 全屏浮动

- **R-4.5.1 跨虚拟桌面**：macOS 上 bubble window 跟随用户切 Space（沿用 015 R-4.7.1 在 pet 窗上的做法，加在 bubble 窗上）。
- **R-4.5.2 悬浮全屏 app 之上**：macOS 上 bubble window window level 高于全屏 app 层级（沿用 015 R-4.7.2 + issue 007 已记的限制）。
- **R-4.5.3 macOS 优先**：本期跨 Space / 全屏只 spike macOS；Win / Linux 行为差异沿用 015 R-4.7.3。

### 4.6 015 既有路径回归

- **R-4.6.1 015 全部 9 条 AC 仍然成立**：本期完成后 015 AC-1 ~ AC-9 必须全部通过（本期 AC 单列一条回归项）。
- **R-4.6.2 既有数据通路零改动**：015 的 push subscriber / owner / policy / store / sessionProjection / chat 窗对话流端到端**完全不动**；本期改动面只在"气泡如何呈现"那一层。
- **R-4.6.3 既有 PetBubble 组件代码最小改动**：组件 JSX / 截断 / expand / dismiss 逻辑保留；只去掉与 absolute 定位 / `flipBelow` 相关的窗内布局代码（这些由 bubble window 本身的 position / size 承担）。

### 4.7 跨平台覆盖

- **R-4.7.1 一份代码三端通跑**：bubble window 的建 / 显隐 / size 适配 / 位置跟随 / 透明 / alwaysOnTop / skipTaskbar 全部用 Tauri 跨平台 WebviewWindow API，**不写"按端分支"的多套代码**——Tauri 底层在 macOS / Windows / Linux 各自调原生实现。
- **R-4.7.2 唯一的 macOS 加料**：跨 Space + 浮全屏 app 上层用 `#[cfg(target_os = "macos")]` 调 NSWindow `setLevel`——015 已在 pet 主窗写了同样的小段加料，本期 bubble window **复用同一段**。Win / Linux 上这段是 no-op，bubble window 在那两个平台用 Tauri 原生 alwaysOnTop 兜底。
- **R-4.7.3 macOS 端到端必过**：本期 AC 验证 macOS 全部跑通。
- **R-4.7.4 Win / Linux 真跑验证留下个需求**：没有开发机的硬约束，本期 Win / Linux 端到端不阻塞 AC；但 typecheck / lint / 单测 / cargo build 三平台都要全绿（CI 门槛由 dev-workflow 把守）。Win / Linux 真机 spike 沿用 015 AC-7 边界。

### 4.8 向后兼容

- **R-4.8.1 既有 chat 窗对话流零变更**：user 在 chat 窗发起对话的端到端行为完全不变。
- **R-4.8.2 既有 pet 窗形象 / 拖拽 / 透明区穿透 / 操作栏行为零变更**。
- **R-4.8.3 bridge / engine / 015 任何接口零改动**。
- **R-4.8.4 sessionProjection 既有投影行为零变更**：本期不动 projection 代码。

---

## 5. 使用约束

- **沿用 015 协议契约**：消费 014 push channel 协议时不改协议字段；发现 014 / 015 协议层有缺漏先回头对齐再走。
- **跨平台开发脚本约定**：本期如新增"非写代码"开发操作（含新 dev 启动入口、调试脚本），按 [`cross-platform-dev`](../../../.cursor/rules/cross-platform-dev.mdc) 双端落地（`run.sh` + `run.ps1`），并在 `scripts/README.md` 登记。
- **frontend-ui-conventions 约束**：本期新增 UI 配置 / Tauri window 配置 / Rust 模块遵循 [`frontend-ui-conventions`](../../../.cursor/rules/frontend-ui-conventions.mdc)。
- **真实 LLM 调用授权**：本期 AC-9 dev CLI 端到端验证会经由 015 / 014 触发 LLM 调用，跑前需获用户授权；本期前端**不引入额外维度的 LLM 调用**。

---

## 6. 验收标准

> 本节 AC 全部是**机制层面的过程性标准**——验证"承载形态升级机制能跑通、015 既有路径不退化"，**不含任何"气泡好不好看 / 跟随手感如何"的产品层判断**。

- **AC-1 Pet 主窗物理尺寸回归 240×320**：`tauri.conf.json` pet window 配置 width=240 / height=320；构建出的 app 在 macOS 上 pet 主窗实际尺寸断言相等。
- **AC-2 气泡走独立 window**：触发一条主动轮气泡 → bubble window show（一个新的、独立的 OS 级 window 出现，不是 pet 主窗内 DOM）；气泡消失 / 用户关闭 → bubble window hide。
- **AC-3 长文本不裁切**：用一段长文本（>400 字）触发气泡 + 进入 expanded 状态，全文完整可见、不被 window 边界裁掉。
- **AC-4 跟随 pet 主窗**：拖动 pet 形象在屏幕内移动 → bubble window 同步跟随；二者视觉上保持稳定相对定位（手感上感知不到明显错位）。
- **AC-5 不抢焦点**：当 chat 窗 / 其他 app 处于输入态时，主动轮触发气泡冒出 → 当前焦点 / 光标 / 输入不被打断。
- **AC-6 跨 Space / 全屏浮动**：macOS 上切 Space 后 bubble window 仍可见；启动全屏 app（如 QuickTime 全屏播放）→ bubble window 仍悬浮其上（边界沿用 issue 007 已记限制）。
- **AC-7 透明区穿透**：bubble window 透明区不阻挡下方桌面 / app 交互（点击空白能落到下方）；气泡实心区（卡片 + 关闭按钮）正常响应点击。
- **AC-8 015 全部 9 条 AC 仍然成立**：015 AC-1 ~ AC-9 在本期完成后全部回归通过（含 AC-1 owner 抽象、AC-4 主动轮分流端到端、AC-5 silent turn 丢弃、AC-6 sessionProjection 兼容、AC-7 跨 Space / 全屏、AC-8 既有路径零退化、AC-9 dev CLI 端到端）。
- **AC-9 既有 chat / pet 行为零退化**：user 在 chat 窗输入对话全流程行为不变；pet 窗左键拖拽 / 透明区穿透 / 托盘菜单 / hover 操作栏行为不变。
- **AC-10 一份代码 + cross-build 全绿**：核心 bubble window 控制代码不带 `#[cfg(...)]` 分支（除 015 已有的跨 Space / 全屏 macOS 加料）；`./scripts/check`（lint + typecheck + 单测）全绿；`cargo build` 在 macOS 上跑通。Win / Linux cross-build / 真跑由 CI / 跨平台 spike 需求承接，不阻塞本期合入。
- **AC-11 Dev CLI 端到端**：复用 015 dev CLI 触发链，在本期实现下跑出来——BedtimeSource 触发 → 独立 bubble window 冒出气泡（一个新 OS 级 window）→ chat 窗 MessageList 不出现该消息；IdleReflectionSource 触发 → bubble window 不显（沿用 015 AC-9 行为）。
- **AC-12 issue 005 关闭**：本期完成后 issue 005 状态置 `resolved`、补一行修复指向（commit / 本需求）。

---

## 7. 变更记录

| 日期 | 变更内容 | 影响范围 |
|------|---------|---------|

---

## 文档元信息

- **状态**：已确认（Confirmed）
- **创建时间**：2026-06-13
- **确认时间**：2026-06-13
- **关联 issue**：[`docs/issues/005-pet-bubble-window-sizing/`](../../issues/005-pet-bubble-window-sizing/)
- **承接**：[需求 015](../015-desktop-pet-bubble-and-conversation-owner/) Tier 0 之后的承载形态层封口
- **关联调研**：本期方案 A（独立 bubble window + Rust 侧轮询主窗坐标做跟随）的工业级参考路径已对齐 Hyprnote `tauri-plugin-overlay`（50ms tick + `outer_position`）等生产用例，详见 `design.md`
- **下一步**：本文档确认后撰写同目录 [`design.md`](./design.md)（技术方案）
