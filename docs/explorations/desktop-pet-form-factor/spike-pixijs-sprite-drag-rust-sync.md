# Spike · PixiJS 拖 sprite + Rust 侧位置同步

> Spike 记录，非决策、非需求。验证"拖拽语义从 window 改成 sprite"这条 plumbing 在 PixiJS + Tauri 下是否可工程化、性能能否撑住跨屏拖拽。

- **创建时间**：2026-06-14
- **状态**：planned
- **依赖**：[`spike-tauri-overlay-cross-platform.md`](./spike-tauri-overlay-cross-platform.md)（要在 overlay 环境下做拖拽 plumbing）
- **关联**：本目录 [`overlay-vs-windowed.md`](./overlay-vs-windowed.md) §7.2 / §3 trade-off "拖拽语义"行
- **下游 spike**：[`spike-bubble-follow-sprite.md`](./spike-bubble-follow-sprite.md)（依赖本 spike 输出的 sprite world position 通道）

---

## 1. 验证目标

回答三组问题：

1. **PixiJS sprite drag-handler 与整屏 cursor passthrough 的冲突处理**——给 sprite 加 `pointerdown` / `pointermove` / `pointerup`，能否在整屏 canvas 上正常 work，而不被 alpha 采样误吞 hit？
2. **Sprite world position emit 给 Rust 的通路**——每帧 emit 还是 drag-move 才 emit？IPC 延迟分布是多少（每帧 emit 是否会让 IPC 通道阻塞或丢帧）？
3. **跨屏拖拽**——用户把 sprite 从 display A 拖到 display B 时，在 sprite 坐标系下表现是否一致？（canvas 整屏 = 所有 display 并集，sprite 跨屏理论上只是 x / y 数值变化，但要验证视觉无错位 / 卡顿）

## 2. 验证方法

在 [`spike-tauri-overlay-cross-platform.md`](./spike-tauri-overlay-cross-platform.md) 跑通的 macOS throwaway 分支上：

1. 把 PixiJS 测试 sprite 加 `eventMode = 'static'`（PixiJS v8）+ pointer 事件 handler
2. drag 状态下：
   - drag-handler 锁焦点，不让 cursor passthrough alpha 采样在 drag 过程中误吞 hit
   - 实现 `sprite.position` 跟 pointer 移动（含 `pointerdown` 时的 offset 记录，避免点击瞬间 sprite 跳到指针位置）
3. 每次 sprite `position` 变化时通过 Tauri `emit` 把 sprite world position（PixiJS 坐标 × DPR）发给 Rust 侧；Rust 侧 listener 记录每次接收时间戳
4. 实验两种 emit 频率：
   - 方案 a：每帧 emit（drag-move 内 emit）
   - 方案 b：每 N ms throttle emit（如 16ms / 33ms）
5. 跑 30 秒拖拽测试：单屏 + 跨屏 + 边界（贴顶 / 贴底 / 贴左右屏边）
6. 测：drag fps 稳态 / IPC RTT 分布 / 跨屏拖时 sprite 是否被"另一屏的桌面"遮挡

## 3. 通过条件

- **pass**：
  - drag fps 稳态 ≥ 55
  - IPC RTT p95 ≤ 16ms（即 1 帧内 Rust 能看到最新 sprite 位置；为 [`spike-bubble-follow-sprite.md`](./spike-bubble-follow-sprite.md) 提供"足够新鲜"的位置源）
  - 跨屏拖拽视觉上无明显错位 / 卡顿
  - 方案 a 或 b 至少一个达到上述标准
- **fail**：两个方案都达不到指标（drag fps < 30 或 IPC RTT > 100ms）→ 触发回退（要么换通信通道、要么改"drag-end 才同步" 语义、要么承认路线 A 在 Tauri 下的拖拽手感劣于路线 B）
- **blocked**：PixiJS pointer 事件被 cursor passthrough alpha 采样误吞 → 不阻塞 pass，但要在 §5 写明 "sprite drag-handler 与 cursor passthrough 的优先级处理方式"

## 4. 不在范围

- **Live2D 模型本身的 hit-shape**——用矩形 sprite 足够验证拖拽 plumbing
- **bubble 怎么跟 sprite**——[`spike-bubble-follow-sprite.md`](./spike-bubble-follow-sprite.md) 负责
- **sprite drag 物理学 / 惯性 / 边界反弹 / 屏边吸附等交互细节**——属于真做 Live2D 需求时的 design 议题
- **Win / Linux 拖拽行为**（沿用 016 边界）
- **多 sprite 同时拖拽 / 选择**——本期就一只 pet

## 5. 实际结果

_待真跑后追加_

## 6. 下一步

_待真跑后追加_
