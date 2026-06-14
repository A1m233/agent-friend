# 决策 0004 · 桌宠承载形态与渲染路径

> Pet Overlay Route
>
> 本文档锁定 `agent-friend` 桌宠承载形态的项目级路线选择（**整屏 transparent overlay**）以及 macOS 端的实现路径（**NSPanel + nonactivating + PixiJS / WebGL**）。
> 本文档只决"项目级方向 + 实现路径骨架"——具体到某个需求的实现细节（Live2D 库选型、状态机、sprite drag 算法、bubble 跟随同步频率等），由对应需求的 `design.md` 决定，不在本文档范围内。

---

## 0. 元信息

- **状态**：草稿（Draft）
- **创建时间**：2026-06-14
- **影响范围**：全项目（前端形态层及其后续所有需求；含未来 Live2D 接入需求 / 桌宠状态机 / 桌宠主动行为通道等）
- **关联**：
  - [`0001-product-vision-and-roadmap`](../0001-product-vision-and-roadmap/README.md)（Phase 1 桌宠形态期 / "在桌面上活着的实体存在感" vision）
  - [`0002-incubation-tech-stack`](../0002-incubation-tech-stack/README.md)（§3.1 目标平台 Win + Mac 不做 Linux / §3.6 Tauri 2）
  - [`0003-frontend-stack-and-phase1-kickoff`](../0003-frontend-stack-and-phase1-kickoff/README.md)（前端框架 React + Tailwind + Vite）
  - [`docs/explorations/desktop-pet-form-factor/overlay-vs-windowed.md`](../../explorations/desktop-pet-form-factor/overlay-vs-windowed.md)（路线选择探索 §6 方向倾向 / §8 回流 ADR 触发条件）
  - [`docs/explorations/desktop-pet-form-factor/industry-standard-tauri-nspanel.md`](../../explorations/desktop-pet-form-factor/industry-standard-tauri-nspanel.md)（业界 Tauri overlay 标准方案沉淀）
  - 5 项前置 spike：[`spike-olv-source-reading.md`](../../explorations/desktop-pet-form-factor/spike-olv-source-reading.md)、[`spike-tauri-overlay-cross-platform.md`](../../explorations/desktop-pet-form-factor/spike-tauri-overlay-cross-platform.md)、[`spike-alpha-hittest-perf.md`](../../explorations/desktop-pet-form-factor/spike-alpha-hittest-perf.md)、[`spike-tauri-nspanel-pet-window.md`](../../explorations/desktop-pet-form-factor/spike-tauri-nspanel-pet-window.md)
  - [需求 016](../../requirements/016-pet-bubble-independent-window/requirement.md) R-4.7.4（跨平台真验边界）
  - [issue 007](../../issues/007-macos-fullscreen-overlay-limit/)（macOS fullscreen 浮层限制；本决策的 NSPanel + fullScreenAuxiliary 路径解此 issue）
- **重新评估触发条件**：见第 9 节

---

## 1. 背景与决策范围

### 1.1 背景

[需求 010](../../requirements/010-desktop-shell-and-chat-ui/) 落地的 240×320 固定 pet 主窗 + [需求 015](../../requirements/015-desktop-pet-bubble-and-conversation-owner/) / [016](../../requirements/016-pet-bubble-independent-window/) 落地的独立 bubble window，已经把"形象 + 气泡"这条最小骨架打通。但准备立 Live2D 接入需求时，浮出一个之前 010/015/016 都没意识到的硬约束：**全身人形 Live2D 模型 9:16 aspect 装不进 240×320 容器**。这把"Live2D canvas 多大"这个工程层问题，推回到**桌宠承载形态本身的产品选择**——是带边界的小窗口，还是屏幕上自由游走的 sprite？

[`overlay-vs-windowed.md`](../../explorations/desktop-pet-form-factor/overlay-vs-windowed.md) §6 已记录方向倾向（路线 A · OLV 整屏 transparent overlay），并在 §7 列出 5 项前置 spike，§8 写明"前置 spike 全跑完且无 spike 级阻塞 → 回流 `docs/decisions/` 立 ADR"。

5 项 spike 现已全部完成（[`spike-tauri-nspanel-pet-window.md`](../../explorations/desktop-pet-form-factor/spike-tauri-nspanel-pet-window.md) §6.3 触发条件已满足）：

| Spike | 状态 | 关键产出 |
|---|---|---|
| §7.5 OLV 源码深读 | done(pass) | Electron→Tauri API 等价表 + 5 条链路速记 |
| §7.1 Tauri overlay 跨平台 | done(pass-with-caveats) | macOS 4 场景过 + `set_ignore_cursor_events` 三端实现已读穿 |
| §7.4 alpha hitTest 性能 | done(pass-with-caveats) | readPixels < 1μs + 副产品发现 PixiJS render blocker |
| **§7.6 NSPanel 路径解 render blocker** | **done(pass-with-caveats)** | **段 A 解 spike 3 撞墙的 fps=0 + 段 B 整屏 NSPanel + PixiJS 30fps 稳态 + GPU 409 mW** |

[`industry-standard-tauri-nspanel.md`](../../explorations/desktop-pet-form-factor/industry-standard-tauri-nspanel.md) 沉淀了业界 macOS Tauri overlay 标准方案（NSPanel + nonactivating + PanelLevel::Floating + collectionBehavior），并实证 BongoCat 21k stars 等 8 个 showcase 项目走的就是这条路。

→ 本 ADR 据此正式锁定项目级路线。

### 1.2 决策范围（In Scope）

- 桌宠承载形态的项目级路线（整屏 transparent overlay vs 固定窗）
- macOS 端的实现路径骨架（窗口类型、style mask、collectionBehavior、activation policy）
- 跨平台方向承诺（Win 是否同样走整屏 overlay）
- 新增项目级技术栈（`tauri-nspanel` git 依赖 + `macOSPrivateApi: true` 项目硬约束）
- 接受的 trade-off 显式登记（4 项）
- 路线反转触发条件

### 1.3 不在范围（Out of Scope，归对应需求 `design.md` 或后续 spike）

- **Live2D 库选型**（pixi-live2d-display vs easy-live2d）→ 真做 Live2D 接入需求时 design 决定
- **形象状态机**（idle / thinking / speaking / error 4 态最小集 + 后续扩展）→ 跟"窗类型"正交；详见 `docs/explorations/desktop-completeness/` §3 Tier 1
- **sprite drag + Rust 同步细节** → [`spike-pixijs-sprite-drag-rust-sync.md`](../../explorations/desktop-pet-form-factor/spike-pixijs-sprite-drag-rust-sync.md) 范畴
- **bubble 跟 sprite world position 的事件通道** → [`spike-bubble-follow-sprite.md`](../../explorations/desktop-pet-form-factor/spike-bubble-follow-sprite.md) 范畴
- **alpha 阈值参数化 / Live2D 模型 alpha 通道渲染细节** → 真做 Live2D 接入需求时 design 决定
- **Win 端整屏 overlay + WebGL 的真机验证 + 平台特化代码细节** → 后续 Win spike 承接
- **macOS Sonoma / Sequoia 跨版本 fps + RAM 实测** → 沿 spike 3 / spike §7.6 边界，本期开发机仅覆盖 macOS 26.6
- **打包 / 签名 / 自动更新** → 沿 0002 §4 暂缓清单，Phase 1 启动前再立

---

## 2. 决策

### 2.1 决策清单速查表

| # | 项 | 决策 |
|---|---|---|
| 1 | 桌宠承载形态项目级路线 | **路线 A · 整屏 transparent overlay**（OLV 路线） |
| 2 | macOS 实现路径 · 窗口类型 | **NSPanel**（不是 NSWindow） |
| 3 | macOS 实现路径 · style mask | `nonactivating_panel`（panel 永不抢 key/main 焦点） |
| 4 | macOS 实现路径 · 窗口 level | `PanelLevel::Floating` |
| 5 | macOS 实现路径 · collection behavior | `fullScreenAuxiliary | canJoinAllSpaces` |
| 6 | macOS 实现路径 · activation policy | `ActivationPolicy::Accessory`（不上 Dock / Cmd+Tab） |
| 7 | macOS 实现路径 · 窗尺寸 | 整屏（monitors union）；Rust 侧 setBounds 后再 to_panel |
| 8 | macOS 实现路径 · 渲染层 | PixiJS / WebGL（业界从未公开实证过的组合，本项目首个 data point） |
| 9 | macOS 实现路径 · cursor passthrough | Rust 60Hz polling cursor + setIgnoreCursorEvents toggle（业界三个独立项目殊途同归） |
| 10 | Win 端方向 | **同样走整屏 overlay 形态**；技术实现细节留 Win spike 承接 |
| 11 | Linux | **不在产品支持范围**（沿 [`0002`](../0002-incubation-tech-stack/README.md) §3.1） |
| 12 | 项目级新增依赖 · macOS only | `tauri-nspanel = { git = "https://github.com/ahkohd/tauri-nspanel", branch = "v2.1" }` |
| 13 | 项目级新增硬约束 | `macOSPrivateApi: true`（透明窗硬要求）→ **不上 Mac App Store** |
| 14 | 是否走"小固定窗 + Web 全屏页"备选 | **否**（vision 锁定的"在桌面上活着"是路线 A 的核心语义；详见 §7） |

### 2.2 路线 A 的产品语义

桌宠 = **一个屏幕上自由游走的 sprite**，不是"一个小窗口里的形象"。"窗口"这个概念对用户消失，只剩形象本身——本质是**"在桌面上活着的东西"**，而不是"app 窗口里的形象"。

这跟 [`0001`](../0001-product-vision-and-roadmap/README.md) §1.1 的"做一个真正像朋友的虚拟陪伴体——而不是一个长得像朋友的工具"vision 直接对齐：朋友 = 实体存在感；工具 = 窗口里的程序。

---

## 3. 决策依据

### 3.1 spike 全链路实证

5 项前置 spike 全部完成、无 spike 级阻塞：

- **spike §7.5 / OLV 源码深读** 确认 Electron 上 OLV 路线 plumbing 全跑通；Cubism Web SDK 跨平台、跟"窗类型"无关；macOS 加料清单（NSScreenSaverWindowLevel / setCollectionBehavior / setButtonHidden）有 016 已落地的部分可复用
- **spike §7.1 / Tauri overlay 跨平台** 确认 macOS NSWindow 路径下 4 场景过（多屏 / Mission Control / Space / fullscreen-with-caveats）、跟 016 已落定的 floating window level 完美叠加；同时发现 `set_ignore_cursor_events` 三端都不 forward mousemove
- **spike §7.4 / alpha hitTest 性能** 确认 readPixels < 1μs（远低于 1ms 阈值）+ 副产品发现 NSWindow + 整屏 + ignoreCursorEvents 路径下 PixiJS render blocker（rAF=0）
- **spike §7.6 / NSPanel 路径** 解了 spike §7.4 撞墙的 render blocker：
  - 段 A · 240×320 NSPanel + PixiJS 30fps 稳态 60+ 秒 + 解 [issue 007](../../issues/007-macos-fullscreen-overlay-limit/) fullscreen 浮层
  - 段 B · 整屏 (3456×2234) NSPanel + PixiJS 30fps 稳态 30+ 秒 + GPU 409 mW（**比 [tauri#15471](https://github.com/tauri-apps/tauri/issues/15471) 报告的 transparent baseline 还低 33%**）

### 3.2 业界标准方案对照

[`industry-standard-tauri-nspanel.md`](../../explorations/desktop-pet-form-factor/industry-standard-tauri-nspanel.md) 实证：

- **`tauri-nspanel` 是 macOS Tauri overlay 的事实标准**（Cap / Screenpipe / EcoPaste / Hyprnote / Coco / Verve / Overlayed / **BongoCat 21.4k stars** 8 个 showcase 项目）
- **manasight 2026 实战博文** 覆盖 macOS Sonoma / Sequoia / **Tahoe**（macOS 26）+ Win 10/11 全平台实测：click-through = Rust 60fps polling + setIgnoreCursorEvents（业界三个独立项目殊途同归 = peeky / Copiwaifu / manasight）
- **OLV 路线在 Electron 跑通**（这是产品对标层最强证据）；Tauri 的对应实现路径（NSPanel + nonactivating）现已实证可工程化

### 3.3 与 spike §7.6 之前 NSWindow 路径的对比

业界没人走"NSWindow + ScreenSaverWindowLevel + 整屏 setBounds + ignoreCursorEvents"组合（spike 1/2/3 撞墙路径），所有 macOS Tauri overlay 都是 NSPanel。本 ADR 锁定的 NSPanel 路径与业界主流对齐，spike §7.6 段 B 的实测数据进一步证明这条路在整屏 + WebGL 组合下也成立。

---

## 4. 接受的 trade-off（显式登记，4 项）

### 4.1 不上 Mac App Store

`macOSPrivateApi: true` 是 Tauri 透明窗的硬要求（[`0002`](../0002-incubation-tech-stack/README.md) §3.6 Tauri 2 决策的隐含代价之一），私有 API 不通过 MAS 审核。

**已接受**：分发渠道改走 DMG 直装 + 自建更新通道（`tauri-plugin-updater` 等，沿 0002 §4 暂缓项推到 Phase 1 启动前再细化）。这条 trade-off 在 manasight 2026 实战博文里也已踩过，业界共识。

### 4.2 macOS 端 fps 30fps 稳态，根因未充分诊断

spike §7.6 段 A / 段 B 实测稳态都是 30fps（p50 33ms 完美一帧 1/30s）。**根因没真正诊断**——可能的解释包括：

1. macOS NSPanel + nonactivating 状态下 WKWebView 主动 throttle
2. 非 key window 上 rAF 频率被限
3. transparent 整屏 alpha-composite 跟 WindowServer refresh 错位（撞 30fps 半拍同步）
4. 节能模式 / Low Power Mode 干扰

业界没人公开验过 NSPanel + WebGL 组合（manasight / BongoCat 用 DOM 不触发；OLV 用 Electron Chromium 完全不同 throttle 模型）——本项目是首个 data point。

**已接受**：桌宠 idle / 慢动作 30fps 业界共识够用（Codex Pets / Electron 同栈方案 PLAN_Desktop_Pet 都明示 idle 20-30fps + 动态降频）。如未来产品需要 60fps（高频交互动画 / 流畅滑动），立专项 spike 排查（试 panel style mask 配置 / set_focus / WKPreferences inactiveSchedulingPolicy::None 等），但**有可能 30fps 就是 OS-level 天花板不可绕**。

### 4.3 macOS 跨版本表现差异大

manasight 实测 RAM Sonoma 66 MB / Sequoia 29 MB / **Tahoe（macOS 26）110 MB**——OS 层面 webview regression，不可控。本 spike §7.6 仅覆盖 macOS 26.6，跨版本 fps 一致性 + RAM 跨版本表现没真验。

**已接受**：跨版本设备覆盖留给后续设备扩展 spike；如某个版本撞产品级阻塞（如 Tahoe 110 MB 影响低内存设备体验），单独立 spike 评估。

### 4.4 Win 整屏 overlay + WebGL 未真验

[tauri#9373](https://github.com/tauri-apps/tauri/issues/9373) 是 Win 反向证据（Win 10 实测 canvas 拉满窗 choppy）。Win 端整屏 overlay + WebGL 业界未公开实证（业界 Tauri Win 桌宠都是固定窗）。

**已接受 + 缓解路径**：

- vision 层面承诺 Win 同样走整屏 overlay（产品一致性 + Chromium WebView2 跟 OLV Electron 同内核理论可复制 OLV 路径）
- 技术实现 Win 路径暂定 NSWindow + `WS_EX_TRANSPARENT | WS_EX_LAYERED` + alwaysOnTop + setIgnoreCursorEvents（沿 manasight Win 11 实证 14MB <1% CPU 路径）
- **Win 端整屏可工程化必须独立 Win spike 验证**（本 ADR 不假设可行）；Win spike 沿 macOS spike §7.6 同样的"先 spike 后落地"流程
- 如 Win spike 撞 #9373 不可绕，参 §6 反转触发条件

---

## 5. 平台范围与边界

### 5.1 macOS（已实证 · 路径锁定）

| 项 | 实现 |
|---|---|
| 窗口类型 | NSPanel（用 [`tauri-nspanel`](https://github.com/ahkohd/tauri-nspanel) `WebviewWindowExt::to_panel<P>()` 转换） |
| panel macro | `tauri_panel! { panel!(PetPanel { config: { can_become_key_window: true, is_floating_panel: true } }) }` |
| level | `panel.set_level(PanelLevel::Floating.value())` |
| style mask | `StyleMask::empty().nonactivating_panel().into()` |
| collection behavior | `CollectionBehavior::new().full_screen_auxiliary().can_join_all_spaces().into()` |
| activation policy | setup hook 里 `app.set_activation_policy(ActivationPolicy::Accessory)` |
| 窗尺寸 | Rust 侧 `available_monitors()` 算并集 → `set_position` + `set_size` 撑整屏，**setBounds 顺序：先 setBounds → 再 to_panel** |
| 透明 | tauri.conf.json `transparent: true`（要求 `macOSPrivateApi: true`） + `decorations: false` + `shadow: false` + `skipTaskbar: true` |
| cursor passthrough | Rust 60Hz `cursor_position` emit `pet://cursor` → 前端 hit-test → `setIgnoreCursorEvents` toggle |
| 渲染 | PixiJS v8 + WebGL；canvas resizeTo viewport |

具体实施代码 listing 以 [`spike-tauri-nspanel-pet-window.md`](../../explorations/desktop-pet-form-factor/spike-tauri-nspanel-pet-window.md) §5.5 "可借鉴代码清单" 为准（含 Cargo.toml dep / panel 类型声明 / apply_pet_nspanel 函数 / Builder plugin 注册 / setup hook 整屏 + NSPanel 转换的完整 snippet + 关键顺序 + API 注意点；spike worktree 已 throwaway 清理，§5.5 是物理代码 ground truth）。

### 5.2 Windows（vision 锁定 · 实现待 spike 验证）

vision 层面：**同样走整屏 transparent overlay 形态**——跟 macOS 端语义一致（"形象在桌面上活着"），不接受 Win 走小固定窗这种导致两端用户感官割裂的方案。

技术实现暂定路径（未真验，需 Win spike 承接）：

| 项 | 实现 |
|---|---|
| 窗口类型 | 普通 NSWindow（Win 没 NSPanel 等价；用 `WS_EX_TRANSPARENT \| WS_EX_LAYERED` 扩展样式） |
| 透明 | tauri.conf.json `transparent: true`（Win 上无需私有 API） |
| 浮层 level | `alwaysOnTop: true`（Win 上 `HWND_TOPMOST`） |
| 窗尺寸 | 同 macOS：Rust 侧 setBounds 整屏 |
| cursor passthrough | Rust 60Hz polling + setIgnoreCursorEvents（与 macOS 同构） |
| 渲染 | 同 macOS · PixiJS v8 + WebGL（WebView2 Chromium 内核，跟 OLV Electron 同内核理论可复制） |

**Win spike 必须验证**：

1. Win 11 / Win 10 上整屏 setBounds + transparent + alwaysOnTop + setIgnoreCursorEvents 四件套是否稳定
2. PixiJS rAF 在整屏窗下是否 60fps（Win Chromium WebView2 不像 macOS WKWebView 有 NSPanel/key-window 概念，理论上不会 30fps cap）
3. 是否复现 [tauri#9373](https://github.com/tauri-apps/tauri/issues/9373) 的 Win 10 canvas 拉满 choppy 现象
4. 整屏 transparent 下任务栏 / WS_EX_LAYERED 是否触发 GDI 内存泄漏（参 [wry#1691](https://github.com/tauri-apps/wry/issues/1691)）

Win spike 通过条件、实施载体、worktree 命名沿 macOS spike §7.6 同样规范。

### 5.3 Linux

**不在产品支持范围**——沿 [`0002`](../0002-incubation-tech-stack/README.md) §3.1 "目标平台 = Win + Mac，不做 Linux"。本 ADR 不触发 0002 §3.1 重评。

---

## 6. 反转触发条件

按 docs-discipline 铁律 1（决策不就地推翻），以下情况不修改本 ADR，而是**新建编号更大的 ADR 覆盖**：

1. **macOS 上游废止 `macOSPrivateApi`**（`transparent: true` 不再可用）→ 路线 A 在 macOS 端不可工程化，必须重新评估
2. **business 决定必须上 Mac App Store**（与 §4.1 trade-off 冲突）→ 必须切回不依赖私有 API 的方案（如 Tauri 内置 transparent 不开私有 API 的子集 / 或者整体回退到固定窗 + canvas aspect-fit 路线）
3. **Win spike 实证整屏 overlay + WebGL 不可工程化**（撞 [tauri#9373](https://github.com/tauri-apps/tauri/issues/9373) 等不可绕）→ 三选一：
   - 切 Hyprnote / overlay-engine 路线（DLL injection + DirectComposition + D3D11 shared texture，技术栈巨大跳跃 + macOS-Win 实现完全异构，违反"统一渲染管线"目标）
   - 接受 Win 端走小固定窗 fallback（牺牲产品一致性）
   - 重新评估整体路线 A 是否值得保留（如果 Win 是主场景且不可走整屏，路线 A 失去主要应用场景）
4. **spike 4（sprite drag）/ spike 5（bubble follow sprite）真做时撞不可工程化阻塞**（如 PIXI sprite drag API 在 NSPanel 下行为异常 / bubble window outer_position 跟随源切换不可行）→ 重新评估 plumbing
5. **macOS 端 fps 30fps 在产品验证阶段被用户实测感知为不可接受 + 60fps 排查 spike 撞 OS 不可绕** → 重新评估"整屏 transparent overlay + WebGL"组合是否还该选

---

## 7. 为什么不走"小固定窗 + Web 全屏页承接"备选

PLAN_Desktop_Pet（Electron 同栈方案，参考 `Downloads/PLAN_Desktop_Pet_Product_And_Tech-20260331.md`）启发了一条备选路线：

- 桌面 overlay = 200×200 小固定窗（不抢屏幕、不干扰工作）
- "宠物公园" / 大场景需求 = Web 端全屏 page（社交、动态体型、知识库可视化）

把"需要大场景表达"的需求**从桌面 overlay 剥离**，让桌面 overlay 永远是小窗。技术上能直接绕过路线 A 的所有工程风险（macOS 26 GPU 8x、Win 整屏 canvas 反向证据、NSPanel + WebGL 业界空白），但**产品形态层面不接受**：

1. **vision 冲突**：[`0001`](../0001-product-vision-and-roadmap/README.md) §1.1 "真正像朋友的虚拟陪伴体" + §2.1 终极形态描述 "桌面上常驻一个**小形象**，可以一直陪着自己"——这里的"陪着"语义是**实体存在感**，不是"打开 app 才能看到"。"小固定窗 + 切到 Web 大场景"语义上是"app 窗口里的形象"，跟 vision 锁定的"在桌面上活着的东西"南辕北辙
2. **形态范式不可量性切换**：[`overlay-vs-windowed.md`](../../explorations/desktop-pet-form-factor/overlay-vs-windowed.md) §4 已明示：路线 A 与路线 B 不是"小+大"的连续光谱，是两种不同的桌宠哲学。备选路线本质是路线 B 的延伸（小固定窗 = B），加 Web 全屏页只是补"大场景"的产品化口子
3. **未来扩展性受限**：路线 A 让"形象走动 / 多 pet 同时出现 / 气泡贴近形象嘴边 / 形象跨屏漫游"等都自然成为 sprite 层面的事；备选路线把这些都关进固定窗的边界

**显式拒绝该备选**——技术上可行不等于产品上正确。

---

## 8. 产品形态心理转换说明（人为可读性）

本 ADR 落地后，相对 016 已实现的"240×320 固定 pet 主窗 + 独立 280×96 bubble 窗"形态，**用户感官层面有一次显性切换**：

| 维度 | 016 当前形态 | 本 ADR 锁定的路线 A 形态 |
|---|---|---|
| 用户视觉 | 屏幕上看到一个 240×320 的小窗 + bubble 窗，可拖整窗 | 屏幕上**看不到任何窗的边界**，只有形象本身（和气泡） |
| 拖拽语义 | 拖整个 pet 窗（Rust `startDragging`） | 拖 sprite（PIXI 内部 reposition + sprite world position 同步给 Rust） |
| pet 主窗 OS 视角 | 240×320 小窗 | 整屏 NSPanel（macOS）/ 整屏 NSWindow（Win） |
| 形象位置 | 拖 pet 窗就是改窗 outer_position | sprite 在整屏 NSPanel canvas 内自由移动；窗位置始终是 (0,0) 整屏 |
| bubble 跟随源 | 016 落地的 16ms tick 读 pet 窗 outer_position | 切到读 sprite world position（spike §7.6 §5.3 Finding 已实证此 plumbing 必修） |
| 操作栏 / 按钮 UI | 在 pet 窗内 flex 居中（form-factor 之内） | sprite-relative 浮动 UI 层（PIXI Container 或 DOM absolute）；或托盘菜单 + 右键菜单承接（业界桌宠标配） |
| 跨 fullscreen 应用 | macOS 上撞 [issue 007](../../issues/007-macos-fullscreen-overlay-limit/)（NSWindow `fullScreenAuxiliary` 不生效） | NSPanel + nonactivating + fullScreenAuxiliary **直接解此 issue** |

→ 真正的形态切换发生在 **真做 Live2D 接入需求时**——本 ADR 不直接拆 016 已实现的代码，但在 016 之上叠加"承载形态转向整屏"这件事会触动多个 plumbing。后续真做需求时 design.md 应当显式引用本节，承认形态切换是显性的。

---

## 9. 重新评估的触发条件

本 ADR 应在以下情形被重新审视（按 docs-discipline 铁律 1，重大方向变更走"立新 ADR"，本节列触发条件而非修改本文件）：

1. §6 列出的反转触发条件任意一条发生
2. spike 4（sprite drag + Rust 同步）/ spike 5（bubble follow sprite）跑通后，发现路线 A 的 plumbing 假设需要修正
3. Win spike 完成后，§5.2 暂定路径需要细化为锁定实现（这种情况下补一节 §5.2 "Win 实现已实证"，属于"补充细节、修正笔误、更新链接"范畴，可原地修订）
4. macOS / Win 真做 Live2D 接入需求并上线一段时间后，根据真实用户反馈的形态层问题，整体回看
5. [`0001`](../0001-product-vision-and-roadmap/README.md) vision 发生重大调整（路线 A 服从 0001 vision；vision 变本 ADR 必须重评）

每次重大修订通过 git 历史 + 立新 ADR 覆盖原决策保留即可，不修改本文件正文内容。
