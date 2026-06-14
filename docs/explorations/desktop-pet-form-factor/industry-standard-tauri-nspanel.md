# 业界标准：macOS Tauri overlay 走 NSPanel 而非 NSWindow

> 探索记录（事实记录），非决策、非需求。第二轮调研沉淀——把 spike 1 / 2 / 3 撞墙后才发现的"业界其实有成熟标准方案"沉淀下来，作为后续 spike 与 ADR 的事实基础。
>
> 本篇**只描述事实**，不立 ADR、不抢 [`overlay-vs-windowed.md`](./overlay-vs-windowed.md) §6 的方向倾向决策权。

- **创建时间**：2026-06-14
- **关联**：本目录 [`overlay-vs-windowed.md`](./overlay-vs-windowed.md) §2 业界主流做法调研（本文补 §2 没覆盖的"macOS Tauri overlay 通用解"层）/ §6 方向倾向 / §7 spike 清单、[`spike-olv-source-reading.md`](./spike-olv-source-reading.md)（OLV Electron 路径）、[`spike-tauri-overlay-cross-platform.md`](./spike-tauri-overlay-cross-platform.md)（NSWindow 路线 macOS pass-with-caveats）、[`spike-alpha-hittest-perf.md`](./spike-alpha-hittest-perf.md) §5.2 PixiJS render blocker
- **下游 spike**：[`spike-tauri-nspanel-pet-window.md`](./spike-tauri-nspanel-pet-window.md)（验证业界标准方案能否解 render blocker）
- **本篇不做**：不立 ADR；不验证"NSPanel + WebGL"是否能跑（留给下游 spike）；不替 [`overlay-vs-windowed.md`](./overlay-vs-windowed.md) §6 的方向倾向做判定

---

## 1. 为什么需要补这块调研

[`overlay-vs-windowed.md`](./overlay-vs-windowed.md) §2 调研了 4 个 Live2D 项目（OLV / Copiwaifu / Live2DPet / pixi-live2d-display），定位是**Live2D 渲染库 + 产品对标**层。但 macOS Tauri overlay 还有更底层的"**用什么 macOS 窗口类型承载 overlay**"这一层，§2 没覆盖。

spike 1 / 2 / 3 沿"NSWindow + NSScreenSaverWindowLevel + 整屏 setBounds + setIgnoreCursorEvents(true)"路径走，[`spike-alpha-hittest-perf.md`](./spike-alpha-hittest-perf.md) §5.2 撞墙——PixiJS canvas 在该组合下 rAF 停转。撞墙后第二轮调研发现：

**业界做 macOS Tauri overlay 的事实标准是 NSPanel（不是 NSWindow），用 [`tauri-nspanel`](https://github.com/ahkohd/tauri-nspanel) 把窗转成 NSPanel + nonactivating panel style mask + Floating window level + collectionBehavior 配齐 fullScreenAuxiliary | canJoinAllSpaces。** 你们 spike 走的"NSWindow + ScreenSaverLevel + 整屏"组合在业界**没有先例**。

本文沉淀这块认知，给后续 spike / ADR 提供事实底座。

---

## 2. tauri-nspanel 生态地图

[`tauri-nspanel`](https://github.com/ahkohd/tauri-nspanel)（作者 ahkohd）是 macOS-only 的 Tauri 插件，把普通 Tauri WebviewWindow 转成 AppKit `NSPanel`。事实标准的实证有三层：

### 2.1 高 star showcase 项目（来自 tauri-nspanel README）

| 项目 | 类别 | 说明 |
|---|---|---|
| **Cap** | 屏幕录制 | tauri-nspanel showcase |
| **Screenpipe** | AI 屏幕录制 | tauri-nspanel showcase |
| **EcoPaste** | 剪贴板管理 | tauri-nspanel showcase |
| **Hyprnote** | AI 笔记 | tauri-nspanel showcase |
| **BongoCat** ⭐21.4k | 桌宠 | **本项目最对口的实证** —— 同样是 Tauri + 跨平台桌宠 |
| **Coco** | AI 搜索助手 | tauri-nspanel showcase |
| **Overlayed** | 屏幕 overlay | tauri-nspanel showcase（按名字最对口） |
| **Verve** | launcher | tauri-nspanel showcase |

### 2.2 BongoCat 实证（21.4k stars 桌宠）

`src-tauri/Cargo.toml` 直证：

```toml
[target."cfg(target_os = \"macos\")".dependencies]
tauri-nspanel.workspace = true
```

`src-tauri/tauri.conf.json` 主窗：

```jsonc
{
  "label": "main",
  "title": "BongoCat",
  "shadow": false,
  "alwaysOnTop": true,
  "transparent": true,
  "decorations": false,
  "skipTaskbar": true
  // 注：未在 conf 设 width/height/fullscreen——动态在 Rust 侧设
}
```

意义：21.4k stars 的桌宠 macOS 端走 NSPanel 路线 + Vue（DOM）渲染，不是 NSWindow + ScreenSaverLevel。

### 2.3 production-ready 模板

社区已有专门做这条线的模板：

- [`dannysmith/tauri-template`](https://github.com/dannysmith/tauri-template) —— "Uses native NSPanel on macOS for proper fullscreen overlay behavior"
- [`mautops/tauri2-template`](https://github.com/mautops/tauri2-template) —— "Native NSPanel on macOS for fullscreen overlay"

两个模板都把 NSPanel 列为 macOS overlay 的"native"路径——不是某种 hack。

---

## 3. manasight 2026 实战博文（覆盖 macOS Tahoe / 26）

[博文链接](https://blog.manasight.gg/why-i-chose-tauri-v2-for-a-desktop-overlay/) —— "Why I Chose Tauri v2 for a Desktop Overlay in 2026"。MTG Arena overlay，作者明确报告：

### 3.1 验证平台覆盖

- **Windows 10 + 11**
- **macOS Sonoma + Sequoia + Tahoe**（= macOS 26，跟你们开发机匹配）
- 30+ test case 协议、5 阶段、20 GitHub issues

### 3.2 关键实现选择（每条都是事实，非推断）

- **click-through 实现**：Rust 60fps polling cursor + toggle `setIgnoreCursorEvents` ——业界**没有 per-pixel hit-test**。这跟 [Johnixr/peeky](https://github.com/Johnixr/peeky) 的 60ms polling、Copiwaifu / itxve 路线高度同构。
- **跨 macOS fullscreen Space**：用"a third-party plugin that overrides the window type"——即 tauri-nspanel。
- **渲染层**：HTML/CSS（DOM），**没用 WebGL/canvas/PixiJS**。
- **`macOSPrivateApi: true`** 是透明窗的硬要求—— **不能上 Mac App Store**。
- **`ActivationPolicy::Accessory`** 解 Dock / Cmd+Tab 出现的问题。

### 3.3 已知坑

- macOS Tahoe（macOS 26）有 **RAM regression**：Sequoia 29 MB → Tahoe **110 MB**（同代码 / 同硬件）。OS 层 webview regression。
- `backdrop-filter: blur()` 在 transparent 下坏（cosmetic）。
- 作者明确表态 "no throttling per se" —— 但他**没用 WebGL**，所以这条结论不能直接套到 PixiJS 场景上。

### 3.4 实测性能

- Win 11: **14 MB RAM, <1% CPU**
- macOS Sequoia: 20–29 MB
- macOS Sonoma: 66 MB
- macOS Tahoe: 110 MB（CPU 15.6% 临界）

---

## 4. NSPanel vs NSWindow：关键差异

### 4.1 NSPanel 是 NSWindow 的 AppKit 子类

NSPanel 是专门为"辅助/工具/HUD"设计的窗口类型，AppKit 内置以下能力：

| 能力 | NSWindow | NSPanel |
|---|---|---|
| `nonactivatingPanel` style mask（不抢 key/main 焦点） | ❌ | ✅ |
| `becomesKeyOnlyIfNeeded` | ❌ | ✅ |
| 默认参与 floating panel level 语义 | ❌ | ✅ |
| `set_hides_on_deactivate(false)` 行为 | 部分 | ✅ |
| AppKit 把它当 utility 而非 main app window | ❌ | ✅ |

**对你们 spike 卡点的可能意义**：你们 spike 用 NSWindow + setIgnoreCursorEvents(true)，鼠标事件不到 webview，window 长期不是 key/main → WKWebView 可能判为 inactive → rAF throttle。NSPanel + nonactivating 设计上就是**永远不抢 key 状态**，AppKit 行为不同——WKWebView 是否仍判 inactive 是个**待验证的关键假设**（[`spike-tauri-nspanel-pet-window.md`](./spike-tauri-nspanel-pet-window.md) 验）。

### 4.2 collection_behavior 5 种 flag（来自 tauri-nspanel `collection_behavior.rs` example）

| Builder method | NSWindowCollectionBehavior flag | 用途 |
|---|---|---|
| `.can_join_all_spaces()` | canJoinAllSpaces | 跨所有 Spaces 可见（016 已用 NSWindow 版本落地） |
| `.full_screen_auxiliary()` | fullScreenAuxiliary | **浮在 fullscreen app 上层** —— 解 [issue 007](../../issues/007-macos-fullscreen-overlay-limit/) 的关键；但要求 panel non-activating |
| `.ignores_cycle()` | ignoresCycle | 不出现在 Cmd+Tab |
| `.stationary()` | stationary | Spaces 切换时不动 |
| `.transient()` | transient | 临时 UI（Mission Control 不持久化） |

### 4.3 tauri-nspanel `fullscreen` example 可直接照搬的配方

来自 [`examples/fullscreen`](https://github.com/ahkohd/tauri-nspanel/tree/v2.1/examples/fullscreen)：

```rust
// 1. 全 app activation policy
.setup(|app| {
    app.set_activation_policy(tauri::ActivationPolicy::Accessory);
    init(app.app_handle());
    Ok(())
})

// 2. 主窗转 NSPanel
panel.set_level(PanelLevel::Floating.value());

// 3. 关键：non-activating + resizable
panel.set_style_mask(
    StyleMask::empty()
        .nonactivating_panel()
        .resizable()
        .into()
);

// 4. 跨 Spaces + 浮 fullscreen
panel.set_collection_behavior(
    CollectionBehavior::new()
        .full_screen_auxiliary()
        .can_join_all_spaces()
        .into()
);

panel.set_hides_on_deactivate(false);
```

example README 强调：**panel 必须 non-activating 才能渲染在 fullscreen app 之上**——这正是 NSWindow 路径解不了 [issue 007](../../issues/007-macos-fullscreen-overlay-limit/) 的根因。

---

## 5. 跟你们 spike 1–3 的对照

| 维度 | 你们 spike 1–3 走的 | 业界标准（manasight + BongoCat + tauri-nspanel） |
|---|---|---|
| 窗类型 | NSWindow | **NSPanel** |
| Window level | NSScreenSaverWindowLevel（016 落地的 1000） | **PanelLevel::Floating** |
| Style mask | 默认 | **nonactivating + resizable** |
| Activation policy | 默认（regular） | **Accessory**（不上 Dock / Cmd+Tab） |
| 尺寸策略 | 整屏（monitors union） | **固定窗**（业界全是固定窗，没有整屏 NSPanel 实证） |
| Click-through | setIgnoreCursorEvents + 60Hz Rust polling | 同（业界三个独立项目殊途同归） |
| 渲染层 | PixiJS / WebGL | **DOM / HTML / SVG**（manasight + peeky 都是 DOM） |
| fullscreen Space 浮层 | NSWindow `fullScreenAuxiliary` 不生效 ([issue 007](../../issues/007-macos-fullscreen-overlay-limit/)) | **NSPanel + nonactivating + fullScreenAuxiliary** 在 [`tauri-nspanel/fullscreen`](https://github.com/ahkohd/tauri-nspanel/tree/v2.1/examples/fullscreen) example 明示能 |

### 5.1 一个核心待验证假设

**假设**：把 NSWindow 切成 NSPanel + nonactivating panel + Floating level 后：
- WKWebView 不再被 AppKit 判为 inactive（因为 panel 设计就不抢 key 状态、AppKit 不当它"backgrounded"）
- → `WKInactiveSchedulingPolicy` 不被触发
- → PixiJS rAF 恢复

**风险**：这个假设没有任何业界先例（业界用 NSPanel 的项目全是 DOM 渲染，没人在 NSPanel 里跑 PixiJS/WebGL）。是 spike 的核心待答问题。

### 5.2 `backgroundThrottling: "disabled"` 配置无效已确认

[wry PR #1445](https://github.com/tauri-apps/wry/pull/1445) 评论实锤：作者 bastiankistner 把 `inactiveSchedulingPolicy` 当**hidden tab + WebSocket suspend** 问题来解决，举的例子是连接保持，**不是 rAF 暂停**。26 条评论里没人测过 transparent / alwaysOnTop / ignoreMouseEvents / 非 key window 下的 rAF 行为。

→ 你们 spike 3 加 `backgroundThrottling: "disabled"` 后仍不渲染**不是配置漏了**，是这条 API 解的根本不是同一个问题。

---

## 6. macOS 26（Tahoe）已知 OS 层 regression（不分窗类型）

无论走 NSWindow 还是 NSPanel，macOS 26 上都有以下已知问题：

- **[tauri#15471](https://github.com/tauri-apps/tauri/issues/15471)（2026-06-04 报告，open / upstream-blocked）**：环境匹配你们（Apple Silicon + macOS 26.6 + Tauri 2.11.2 + wry 0.55.1）。`transparent: true` 让 WindowServer 对整窗每帧 alpha-composite，**GPU 8× 功耗**。窗口越大代价越大——整屏 transparent overlay 把代价放大到整屏尺寸。
- **[wry#1636](https://github.com/tauri-apps/wry/issues/1636)（macOS 26.1，Closed 但无可见解决）**：transparent 在 macOS 26 下 regression。
- **manasight 实测 macOS Tahoe RAM 翻倍**：29 MB → 110 MB。

→ 即便 NSPanel 解了 rAF blocker，**整屏 transparent overlay 在 macOS 26 上仍有 GPU/RAM 代价**。固定窗形态把代价限制在小区域内，是业界普遍走固定窗的间接驱动力之一。

---

## 7. Windows / Linux 边界

### 7.1 Windows 实证强度梯度

| 形态 | Win 实证 |
|---|---|
| 固定窗 transparent + alwaysOnTop + click-through | ✅ manasight 实测 14MB <1% CPU + Win 业界惯例 |
| **整屏** overlay（setBounds 到 monitors union） | **未验证** —— 业界 Tauri Win 应用做 overlay 全是固定窗，没人 setBounds 整屏 |
| 整屏 overlay + WebGL canvas | **业界空白 + 反向证据**：[tauri#9373](https://github.com/tauri-apps/tauri/issues/9373) 实证 Win 10 上 canvas 拉满窗就 choppy，Tauri 直接复现 |

→ Win 上"transparent + alwaysOnTop + click-through 这个形态"已 verified；但 "**整屏** overlay + WebGL"在 Win 上跟 mac 一样是空白，且有反向证据。

### 7.2 NSPanel 是 macOS-only 概念

Win 没有 NSPanel 等价物。Win 透明 overlay 的标准路径是 `WS_EX_TRANSPARENT | WS_EX_LAYERED` 扩展样式 + `alwaysOnTop` —— 走普通 NSWindow + cfg 不变即可。Linux X11 / Wayland 各自有 input shape region API（[`spike-tauri-overlay-cross-platform.md`](./spike-tauri-overlay-cross-platform.md) §5.1 已记）。

→ 走 NSPanel 路线的代码改动**用 `#[cfg(target_os = "macos")]` 隔离**，Win/Linux 现有 NSWindow 路径不受影响。

### 7.3 沿现有边界

- Win/Linux 真机端到端：沿 [需求 016 R-4.7.4](../../requirements/016-pet-bubble-independent-window/requirement.md) + [`spike-tauri-overlay-cross-platform.md`](./spike-tauri-overlay-cross-platform.md) §2 边界，本期不真验
- Win 整屏 overlay + WebGL 的可工程化：与 macOS NSPanel 路线**正交独立**，需后续跨平台 spike 单独承接

---

## 8. 不在本文档作答的问题

以下问题**显式留给下游 spike 或后续调研**，本文不作判定：

- **NSPanel + WebGL/PixiJS 渲染是否能跑通**：[`spike-tauri-nspanel-pet-window.md`](./spike-tauri-nspanel-pet-window.md) §1 验证目标 1.1（固定窗段）+ 1.2（整屏段）
- **NSPanel + 整屏 setBounds 是否触发 [tauri#15471](https://github.com/tauri-apps/tauri/issues/15471) GPU 8x 问题**：上述 spike §1 验证目标 1.2 一并验
- **macOS Sonoma / Sequoia / Tahoe 跨版本 fps 差异**：沿 [`spike-alpha-hittest-perf.md`](./spike-alpha-hittest-perf.md) §3 blocked 分支边界，本期开发机只覆盖 macOS 26.6
- **Win 整屏 overlay + WebGL 是否可工程化**：与 macOS NSPanel 路线正交，需后续跨平台 spike
- **是否有 Cubism / Live2D 渲染特异性**：本文档以 PixiJS / WebGL 为锚——具体 Live2D 库选型（pixi-live2d-display vs easy-live2d）跟"窗类型"正交，由真做 Live2D 接入需求时 design 决定

---

## 9. 跟 [`overlay-vs-windowed.md`](./overlay-vs-windowed.md) 与 ADR 的关系

### 9.1 不修订 [`overlay-vs-windowed.md`](./overlay-vs-windowed.md) §6 方向倾向

§6 倾向走路线 A（OLV 整屏 transparent overlay）的判断**仍然成立**——业界对"陪伴朋友"语义的对标（OLV 同构、桌宠哲学契合）没变。本文只修正**实现路径**：从"NSWindow + ScreenSaverLevel + 整屏"切到"NSPanel + Floating + nonactivating + 待验证整屏可行"。

### 9.2 回流 ADR 的触发条件细化

[`overlay-vs-windowed.md`](./overlay-vs-windowed.md) §8 已写"spike 全跑完后回流 ADR"。本文新增对 ADR 内容的细化建议：

- ADR 应锁定**实现路径**为 NSPanel（如下游 spike pass）
- ADR 应把 `tauri-nspanel` 列入项目级 macOS 依赖（首次新增非 crates.io git dep，触发铁律 3 的项目级技术栈登记）
- ADR 应把"固定窗 vs 整屏 NSPanel"按下游 spike 段 A / 段 B 结果分类锁定：
  - 段 A pass / 段 B fail → 锁"固定窗 NSPanel"（业界主流 + 工程稳）
  - 段 A pass / 段 B pass → 锁"整屏 NSPanel"（OLV 路线 Tauri 等价物，路线 A 完整落地）
  - 段 A fail → 触发 [`overlay-vs-windowed.md`](./overlay-vs-windowed.md) §8 反转条件，回退路线 B（Copiwaifu 固定 NSWindow + canvas aspect-fit）

---

## 10. 阅读资料汇总

| 资料 | 类型 | 链接 |
|---|---|---|
| tauri-nspanel 主仓 | 工具 | https://github.com/ahkohd/tauri-nspanel |
| tauri-nspanel `fullscreen` example | 配方 | https://github.com/ahkohd/tauri-nspanel/tree/v2.1/examples/fullscreen |
| tauri-nspanel `collection_behavior` example | 配方 | https://github.com/ahkohd/tauri-nspanel/blob/v2.1/examples/collection_behavior.rs |
| BongoCat（21.4k 实证） | 项目 | https://github.com/ayangweb/BongoCat |
| dannysmith/tauri-template | 模板 | https://github.com/dannysmith/tauri-template |
| mautops/tauri2-template | 模板 | https://github.com/mautops/tauri2-template |
| manasight 博文 | 实战 | https://blog.manasight.gg/why-i-chose-tauri-v2-for-a-desktop-overlay/ |
| peeky（DOM/SVG 路线对照） | 项目 | https://github.com/Johnixr/peeky |
| tauri#15471 GPU 8x | issue | https://github.com/tauri-apps/tauri/issues/15471 |
| tauri#9373 Win canvas 拉满 choppy | issue | https://github.com/tauri-apps/tauri/issues/9373 |
| wry#1636 macOS 26 transparency | issue | https://github.com/tauri-apps/wry/issues/1636 |
| wry PR #1445 backgroundThrottling | PR | https://github.com/tauri-apps/wry/pull/1445 |
