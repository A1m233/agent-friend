# 010 · bubble window 在 fullscreen Space / 全屏 app 上不可见

- **状态**：open
- **严重度**：medium
- **发现日期**：2026-06-15
- **发现于**：[018-pet-live2d-state-and-lipsync](../../requirements/018-pet-live2d-state-and-lipsync/) M18.9 真跑
- **关联需求**：[016-pet-bubble-independent-window](../../requirements/016-pet-bubble-independent-window/)、[017-pet-overlay-form-switch](../../requirements/017-pet-overlay-form-switch/)
- **关联 ADR**：[0004-pet-overlay-route](../../decisions/0004-pet-overlay-route/README.md) §5.1 macOS NSPanel + fullScreenAuxiliary

## 现象

macOS 上当用户进 fullscreen Space（如 QuickTime / 浏览器 F11 / 任何 app 全屏）时，**Hiyori 形象（pet 主窗）正确浮现**（17a AC-7 已通过 = NSPanel + fullScreenAuxiliary 解），**但 bubble window 不出来**——主动轮气泡 / dev 注入气泡在 fullscreen 状态下完全看不见。

退出 fullscreen 回普通 Space 后 bubble 立刻可见。

## 根因初判

17a 只给 **pet 主窗** 加了 NSPanel + nonactivating + fullScreenAuxiliary + canJoinAllSpaces 配置（`apply_pet_nspanel` 在 `lib.rs` setup hook 内对 pet window label 调用）。

**bubble window** 是 016 引入的独立 webview（label="bubble"），由 `bubble_window.rs` 管理；其 window 类型仍是 macOS 默认普通 NSWindow，没经过 NSPanel 转换 / 没设 collection behavior。普通 NSWindow 不会跨 fullscreen Space 显示。

17a M17.9 验收时只跑了 pet 主窗的跨 Space / fullscreen 验证（AC-7 expected = pet 形象浮现），没单独验 bubble 在 fullscreen 是否可见——属 17a 漏验场景。18b 真跑时撞到。

## 影响与判断

- 影响产品核心体感：用户开 fullscreen 看视频 / 游戏 / 演示时桌宠"会出现但不会说话"——主动陪伴的语义被切一半。
- 不影响功能（bubble 状态机正常 ingest，store / window 显隐控制正常；只是 bubble window 物理上不在 fullscreen Space 渲染）。
- Win 端 alwaysOnTop 路径天然支持全屏浮层（17a Win spike §6.4.3 Bonus finding），此问题仅限 macOS。

## 可选修复方向

1. **给 bubble window 也走 NSPanel 转换**：在 setup hook 内对 bubble label 也调 `apply_pet_nspanel`（沿 ADR 0004 §5.1），加 fullScreenAuxiliary + canJoinAllSpaces。bubble window 用 NSPanel 时需评估 invoke `show_bubble` / `hide_bubble` / `set_bubble_size` 等 016 既有 plumbing 是否仍正确（NSPanel 与 NSWindow API 兼容性差异）。
2. **bubble 内容内嵌进 pet 主窗**：放弃 016 独立 window 方案，bubble 由 pet webview 自己渲染（DOM absolute）。需大改 016 / 撤掉独立 window 架构。
3. **当 fullscreen 时降级**：检测全屏态 → 降级为 pet 窗内 DOM bubble；非全屏走独立 window。复杂、双码维护成本高。

倾向方向 1（最小补丁，复用 17a 既有 NSPanel plumbing）。

## 关联

- 17a 漏验：AC-7 跨 Space / 全屏验证应该把 bubble window 也纳入。
- 修复时建议补 17a / 16 既有 AC 在 fullscreen 状态下的回归（bubble 显隐 / 跟随 / size 调整）。
