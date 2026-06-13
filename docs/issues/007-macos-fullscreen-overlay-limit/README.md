# 007 · 桌宠在 macOS fullscreen Space 下不浮起（NSWindow 系统级限制）

- **状态**：open
- **严重度**：low
- **发现日期**：2026-06-13
- **关联需求**：[015-desktop-pet-bubble-and-conversation-owner](../../requirements/015-desktop-pet-bubble-and-conversation-owner/) AC-7

## 现象

015 M15.8 真跑：
- ✅ 跨 user Space（三指扫 / Ctrl+左右 / Mission Control 切 Space）：桌宠正确跟随到新 Space
- ❌ macOS "绿色按钮 fullscreen mode"（点击 app 标题栏左上角绿色按钮、app 进入独立 fullscreen space）：桌宠**不在该 fullscreen space 中出现**

NSWindow 的 `setLevel: NSScreenSaverWindowLevel(1000)` + `setCollectionBehavior: CanJoinAllSpaces | FullScreenAuxiliary` 都通过 verify 日志确认真正生效（实时读回值匹配 0x101）—— 但 macOS 行为仍如上。

## 根因初判

macOS fullscreen Space 的设计语义：

1. **macOS 绿色按钮 fullscreen** 会把目标 app 推入**独立的 fullscreen Space**（连 menu bar 都默认隐藏）。
2. 来自**其他 app** 的 window，即便 `level=1000` + `CanJoinAllSpaces` 设上，**仍然显示在原 Space**——它们 NOT IN THE FULLSCREEN SPACE。
3. `NSWindowCollectionBehaviorFullScreenAuxiliary` 的本意是 "**The window is allowed to participate in the full-screen space of another window**"，但**前提是：fullscreen 的那个 window 必须是同一 app**。它不让你的 window 加入**别的 app** 的 fullscreen space。
4. 不存在公开的 `CanJoinAllApplications` 标志。

业界桌宠生态（NekoAI / clawd-on-desk / TinyRoommate 等）普遍承认这是 **macOS 平台限制**，不是任何项目的 bug。用户切到 fullscreen app 时桌宠就"看不到了"是 macOS 默认行为。

## 影响与判断

- **影响范围**：macOS 用户使用绿色按钮 fullscreen 模式（Safari/Chrome/Quicktime/Final Cut/Xcode 等）时，桌宠暂时不可见；切回非 fullscreen 即恢复
- **使用模式上**：桌宠主要场景是"工作 + 待机伴随"，fullscreen 多见于看视频 / 演示 / 写代码全屏，此时用户也不期望桌宠打扰；体验降级可接受
- **不违反 015 AC-7**：跨 Space 跟随主轴已通；fullscreen 浮属于平台限制
- 因此**本期不修**，登 issue 后续选做（取决于 NSPanel 改造成本 vs 实际用户痛点）

## 可选修复方向

**方向 A · NSWindow → NSPanel 改造（中等成本）**

NSPanel 是 NSWindow 子类，专为 "utility window" 设计，有特殊的 spaces / fullscreen 行为：
- 默认更倾向于 "float across spaces" 语义
- 配合 `becomesKeyOnlyIfNeeded` / `floatingPanel` 等属性能在更多 layer 浮起

实现：Tauri 创建的是 NSWindow，需要在 Rust 端拿到 ns_window 后通过 objc runtime 把它"转换"成 NSPanel（修改 class）。

- 风险：NSWindow 已分配状态可能不兼容 NSPanel 期望；可能破坏既有透明 / 拖拽 / 穿透行为
- 工作量：1-2 天 spike + 验证回归

**方向 B · 半私有 API（高成本 + 风险）**

Bartender / Magnet 这类系统级 overlay 工具用 CGS（Core Graphics private API）实现"真正全局 overlay"。Tauri 项目用私有 API 会带来：
- App Store 上架风险
- macOS 升级时 API 可能变更
- 项目 / 代码维护成本高

不推荐除非确认是核心需求。

**方向 C · 改入产品决策"勿扰模式"**（最务实）

如果"fullscreen 下不浮"本质上跟"用户在专注做事不该打扰"语义一致，就让它顺其自然：
- 用户全屏看视频 / 演示 → 不打扰
- 用户切回非 fullscreen → 桌宠回来
- 这跟未来 `desktop-completeness` Tier 2 "环境感知 + 专注模式静默" 是一致体验

如果走这条路：方向 A / B 都不做，留作平台限制接受。

我倾向 C —— 等"产品级勿扰 / 专注模式静默"落地后再回头看这个 issue 是否还需要 A 处理。

## 备忘

修复时一并复核：
- `frontend/src-tauri/src/lib.rs::mac_apply_pet_window_level`（当前 collectionBehavior = 0x101）
- `015 requirement.md` R-4.7.2 / AC-7（如改成 NSPanel 应同步更新文案）
