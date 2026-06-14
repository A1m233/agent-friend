# Spike · Bubble window 跟 sprite 而非 pet 主窗

> Spike 记录，非决策、非需求。验证"bubble window 位置跟随源从 pet 主窗 `outer_position` 切到 sprite world position"在 overlay 形态下的 plumbing 可行性。

- **创建时间**：2026-06-14
- **状态**：planned
- **依赖**：[`spike-pixijs-sprite-drag-rust-sync.md`](./spike-pixijs-sprite-drag-rust-sync.md)（要在 sprite world position 能可靠 emit 给 Rust 的前提下做 bubble 跟随）
- **关联**：本目录 [`overlay-vs-windowed.md`](./overlay-vs-windowed.md) §7.3 / §3 trade-off "bubble 跟随逻辑"行 / [需求 016](../../requirements/016-pet-bubble-independent-window/) 现有 bubble 跟随实现（`bubble_window.rs` 16ms tick）

---

## 1. 验证目标

回答三组问题：

1. **新通路是否可工程化**——overlay 形态下 pet 主窗位置固定（整屏），016 的"读 pet 窗 `outer_position` → 算 bubble 屏幕坐标"语义失效。改为"读 sprite world position + canvas 在屏幕的 origin + DPR / scale_factor → 算 bubble 屏幕坐标"这条新通路能否可靠工作？跟随精度是否能达到 016 现有手感？
2. **跟随频率选择**——是 16ms tick + 持续跟随（drag 中 bubble 实时跟着 sprite 跑），还是 sprite drag-end 才同步一次（drag 中 bubble 暂停跟随、松手后跳过去）？哪个手感更好？
3. **翻转逻辑是否需要重做**——016 R-4.3.3 `flipBelow`（bubble 贴屏顶时翻到 sprite 下方）在 overlay 形态下还成立吗？sprite 可以跨屏，"屏顶"的判定语义有没有变化？

## 2. 验证方法

在 [`spike-pixijs-sprite-drag-rust-sync.md`](./spike-pixijs-sprite-drag-rust-sync.md) 跑通的 macOS throwaway 分支上：

1. **新通路 plumbing**：在 Rust 侧加一个 bubble window 跟随 tick（沿用 016 `bubble_window.rs` 的 16ms 间隔做 baseline）。把读 pet 窗 `outer_position` 的逻辑改成"读最近一次 emit 的 sprite world position → 加 canvas 在屏幕的 origin（overlay 形态下 origin = (0, 0)）+ DPR 换算 → 算 bubble 屏幕坐标"
2. **频率方案对比**：
   - 方案 a：16ms tick + 持续跟随（drag 中 bubble 实时跟）
   - 方案 b：drag-end 才同步（drag 中 bubble 不跟、松手时跳过去）
3. **跑测试**：
   - 各跑 30 秒拖拽 + bubble 同时显示，主观判断手感差异
   - 测试 bubble 跟随延迟分布（sprite emit 时间 → bubble window setPosition 完成时间）
4. **跨屏拖**：sprite 从 display A → display B 时 bubble 能否正确跨屏（OS-level window 跨 display 的 `set_position` 在 Tauri 上行为如何，是否会出现 bubble 闪烁 / 重置 / 黑屏）
5. **翻转**：sprite 拖到屏顶时，`flipBelow` 翻转判定是用"sprite world position.y < threshold"还是"bubble 计算后的屏幕坐标 y < threshold"——在 overlay 形态下两者应该等价（canvas origin = (0, 0)），但要验证

## 3. 通过条件

- **pass**：
  - 方案 a **或** b 至少一个在主观手感上达到 016 现有水平（"手感上感知不到明显错位"）
  - 跨屏拖：bubble 能正确跟随 sprite 跨到第二个 display 上、无闪烁 / 黑屏
  - 翻转逻辑：sprite 贴屏顶时 bubble 自动跳到 sprite 下方，跟 016 等价
- **fail**：两个方案都达不到 016 手感且无明显改进路径 → 触发回退（备选：要么 bubble 也内嵌 overlay canvas 内做 DOM 元素而非独立 window / 要么承担 "bubble drag 中滞后" 作为路线 A 的代价）
- **blocked**：Tauri 跨 display `set_position` 行为异常（如 bubble 跨屏时闪烁 / 重置 / 黑屏 / 跨屏后窗口尺寸丢失）→ 标注并寻 Tauri 等价 workaround，不阻塞 pass 但要在 §5 / §6 写清楚

## 4. 不在范围

- **bubble window UI 表现**——沿用 015 / 016 既有 `<PetBubble />` 组件
- **真做需求时的最终方案选择（a or b）**——本 spike 只验证"两个方案都可工程化"，最终选哪个由真做 Live2D 需求的 design.md 决定
- **Win / Linux 跨 display 行为**（沿用 016 边界）
- **bubble 形态联动 sprite 状态机**（如 speaking / thinking 状态的 bubble 抬升 / 抖动）——跟"位置跟随"正交，是真做 Live2D 需求时的议题
- **多 bubble 排队 / 堆叠**——单 bubble 已够 spike 主轴

## 5. 实际结果

_待真跑后追加_

## 6. 下一步

_待真跑后追加_
