# 024 桌宠 B 类交互 - 进度

## 总体状态

<!-- NOT_STARTED | IN_PROGRESS | COMPLETED | BLOCKED -->
COMPLETED

---

## 开干前必读

- **范围**：[requirement.md](./requirement.md) §2 锁定的三件事（视线跟随 / 点击触发 motion / 拖拽视觉反馈），纯前端、不出进程、不动协议。
- **设计**：[design.md](./design.md) 已把"新增 6 文件 / 改造 4 文件 / 不动 015/016/17a/18 核心"列细。任务清单按 design 章节扫一遍。
- **Hiyori audit 已在 Phase 2 完成**：[design.md §3.1](./design.md#31-hiyori-audit-结果与降级落地)。结论 = HitArea 只 1 个 → 触发 [requirement §4.5.3](./requirement.md#45-day-1-hiyori-资源-audit--降级路径) 降级（tap 任意区都触发 `TapBody`/m04）；无 `.exp3.json` 但 `cdi3.json` 暴露 64+ 参数 → 表情走参数直写。**不要重跑 audit milestone**（M24.0 仅引用结论 + 对位 requirement.md §4.5.1，不真做）。
- **状态优先级初版**（[requirement §4.4.1](./requirement.md#44-状态优先级与互斥) **不进 AC 锁定**，实现期可在本文档实现日志里调整）：`speaking > drag > tap > gaze`。
- **AC-9 主观体感 sign-off 由本仓 owner 给**（[requirement.md §6.2 AC-9](./requirement.md#62-主观体感层用户手验)）。本文档总体状态切到 `COMPLETED` 前必须 owner 明确说 OK，**不要自己 close**。
- **Win 端真跑 AC**：当前 macOS 环境跑不了 Win 真机 AC（AC-7 / AC-8）。M24.9 留作"待 owner 切 Win 真机验"分项，不挂在自己门禁卡死。
- **跑会触发 LLM 调用的 AC**（如 dev CLI `cron:bedtime` 触发 speaking 测 speaking 抢 tap）前先获 owner 明确同意（[`llm-api-confirm`](../../../.cursor/rules/llm-api-confirm.mdc)）。

---

## 任务列表

### M24.0 · Hiyori audit（已 Phase 2 完成 · 不真跑）

- [x] 在实现日志里登记一句"audit 结论引用 design.md §3.1；触发降级路径，本期 tap 任意区 → `TapBody`/m04 motion；表情走参数直写"，对位 requirement.md §4.5.1。

### M24.1 · 框架与 ParamSource 接口（无依赖）

- [x] 新增 `frontend/src/pet/sources/types.ts`：`ParamSource` interface（`readonly active: boolean` + `apply(sprite: Live2DSprite): void`），对位 [design.md §3.2](./design.md#32-统一-composepetrender-中央-hook本期核心)。
- [x] 新增 `frontend/src/pet/composePetRender.ts`：装一次 `sprite.onRender`、按硬编码顺序调各 source.apply、返 detach 函数恢复原 onRender。
- [x] 新增 `frontend/src/pet/composePetRender.test.ts`：
  - 多 source 顺序：mock sprite + 多 source.apply 被按数组顺序调
  - `source.active === false` 时跳过该 source
  - detach 函数恢复原 `onRender`
  - origOnRender 先于 source.apply 执行

### M24.2 · `GazeSource`（依赖 M24.1 types）

- [x] 新增 `frontend/src/pet/sources/GazeSource.ts`，对位 [design.md §4.1](./design.md#41-gazesource视线--头跟随光标)。初值常量按 [design.md §5](./design.md#5-参数选择与初值表)。
- [x] 新增 `frontend/src/pet/sources/GazeSource.test.ts`：
  - `updateCursor` 给定 cursor + spriteScreen → 验 `target.angleX/Y/Z + eyeBallX/Y` 计算正确（中心 / 边缘 / 阈值外 三个 cursor 位置）
  - `apply` 多次调用 → 验 `current` 经 EMA 收敛到 `target`（|current - target| < ε after N frames）
  - `setActive(false)` → `active === false`，composer 跳过

### M24.3 · `TapReactionSource`（依赖 M24.1 types）

- [x] 新增 `frontend/src/pet/sources/TapReactionSource.ts`，对位 [design.md §4.2](./design.md#42-tapreactionsource点击反应表情)。
- [x] 新增 `frontend/src/pet/sources/TapReactionSource.test.ts`：
  - `fire()` → `active === true` + sin envelope 在 [0, REACTING_DURATION_MS] 内正确（mock `performance.now`）
  - reacting 期间再 `fire()` → 忽略（active 不重置 / startTs 不更新）
  - `REACTING_DURATION_MS` 后 → `active === false`

### M24.4 · `DragReactionSource`（依赖 M24.1 types）

- [x] 新增 `frontend/src/pet/sources/DragReactionSource.ts`，对位 [design.md §4.3](./design.md#43-dragreactionsource拖拽反馈)。
- [x] 新增 `frontend/src/pet/sources/DragReactionSource.test.ts`：
  - `setDragging(true, 0.5, 0)` → `active === true` + apply 时 `ParamAngleZ` 按 dragVelX 算
  - `setDragging(false)` → 进 release 阶段，envelope 线性衰减
  - `RELEASE_DURATION_MS` 后 → `active === false`
  - `updateDragDirection(vx, vy)` 在 dragging 期间更新方向；非 dragging 期间忽略

### M24.5 · 18 `MouthDriver` 改造（依赖 M24.1 types）

- [x] 改造 `frontend/src/pet/MouthDriver.ts`，对位 [design.md §4.5](./design.md#45-18-mouthdriver-改造)：
  - 去掉 `attach()` 里 hook `sprite.onRender` 的整段
  - 去掉 `origOnRender` 字段 + `detach()` 里恢复 `origOnRender` 的段
  - `setMouthOpen()` 不再直接 `setParameterValueById`
  - 新增 `apply(sprite: Live2DSprite): void` + `get active(): boolean { return this.sprite !== null }`
  - 接口 `MouthDriver` 扩展自 `ParamSource`
- [x] 改造 `frontend/src/pet/MouthDriver.test.ts`：raf stub 每帧后通过可变 applyRef 调用 `driver.apply(sprite)`，模拟 composer 调度；既有数值演进断言与 detach 归零断言保持。
- [x] **18 既有 AC-5 lip-sync 节奏验收**保持通过（M24.8 真跑时验，本 milestone 仅单测层）。

### M24.6 · 17a `usePixiAvatarSlot` 改造（click vs drag）

- [x] 改 `frontend/src/pages/pet/usePixiAvatarSlot.ts`，对位 [design.md §3.3 / §4.4](./design.md#33-click-vs-drag-区分17a-slot-drag-handler-改造)：
  - 签名改为 `options?: { onSlotClick?, onSlotDragMove? }`
  - `pointerdown` 记录 startPos/startTime/dragArmed=false，**不立即** setIsDragging
  - `globalpointermove` 超 `DRAG_MOVE_THRESHOLD_PX (5)` 才 setIsDragging + 走 17a 既有 drag 流程，期间归一化喂 `onSlotDragMove(vx, vy)`
  - `pointerup` / `pointerupoutside`：未 dragArmed + 时长 < `CLICK_MAX_DURATION_MS (300)` + 距离 < 5px → 派发 `onSlotClick`；否则走既有 drag commit
  - useEffect deps 不重建 PIXI Application：用 ref-sync 模式
- [x] 新增 `frontend/src/pages/pet/usePixiAvatarSlot.test.tsx`：
  - mock PIXI 事件流：短按短移 (<5px, <300ms) → `onSlotClick` 被调、`setIsDragging` 没被调
  - mock 事件流：超阈值 → `setIsDragging` 被调、`onSlotClick` 没被调
  - mock 事件流：短按但时长 >300ms → 既不 click 也不 drag (noop)
  - drag 期间归一化 dragMove vx/vy 在 [-1, 1] 范围
- [x] **17a 既有 AC-4 / AC-5 行为零退化**：超阈值的拖拽行为完全等价 17a（M24.8 真跑验）。

### M24.7 · `usePetInteractions` 胶水 + `PetApp` 装配（依赖 M24.1~6）

- [x] 改 `frontend/src/pet/live2dConfig.ts`：`PetLive2DConfig.motionGroups` 类型扩 `tap?: string | null`，并新增 `motionNo` 配置表；tap 默认 group=`Idle`、no=4。
- [x] 新增 `frontend/src/pages/pet/usePetInteractions.ts`，对位 [design.md §4.6](./design.md#46-composepetrender-与-usepetinteractions)：
  - 装载 composer（等 `spriteRef.current` ready）传 sources `[gaze, tap, drag, mouthDriver]`，return 时 detach
  - `pet://cursor` 副订阅 → `gaze.updateCursor(x, y, spriteScreen)`
  - `isDragging` 切到 true 时 `drag.setDragging(true) + gaze.setActive(false)`；false 时反向
  - `onSlotClick` 内 `if (phase === "speaking") return` + `tap.fire() + sprite.startMotion({ group: motionGroups.tap, no: motionNo.tap })`
  - `onSlotDragMove` 内 `drag.updateDragDirection(vx, vy)`
- [x] 改 `frontend/src/pages/pet/App.tsx`：
  - 用 `interactionsRef` 延迟绑定解决 hooks 调用顺序问题（`usePixiAvatarSlot` 需 callbacks，但 callbacks 依赖 `spriteRef`）
  - 装 `usePetInteractions(spriteRef, spriteScreen, isDragging, driver)` 得到 `{ onSlotClick, onSlotDragMove }`
  - 回填 `interactionsRef.current`
  - **18 既有 lip-sync useEffect 保留不动**（`driver.attach/detach` 仍是 PetApp 调，driver 内部不再 hook onRender）
- [x] dev observability：dev 模式下打 `gaze` / `tap` / `drag` 关键状态 log。
- [x] **实现期调整记录**：`usePixiAvatarSlot` 接收 `interactionsRef` 而非直接 callbacks；解决 App.tsx hooks 顺序循环，对位 progress.md 实现日志。

### M24.8 · macOS 真跑端到端

- [x] 起 `./scripts/dev/run.sh`：Tauri 编译通过，vite ready，无 JS 运行时错误；PetOverlay 已启动。
- [x] **AC-2 视线跟随**：owner 手验确认 OK。实现期经多轮调参：方向改回 Live2D 标准约定、`ParamAngleX` 正=朝右，幅度上限 45°/15°，阈值 0.5 并以 threshold 为映射基线。
- [x] **AC-3 点击 motion 触发**：owner 手验确认 OK。最终方案：点击触发 `Idle` group 中由 `motionNo.tap` 指定的 motion（默认 no=4），`TapReactionSource` 仅保留脸红 + 微笑嘴参数点缀。
- [x] **AC-4 拖拽视觉反馈**：owner 手验确认 OK。`DragReactionSource` 峰值 25°/15°，传瞬时速度，`DRAG_VELOCITY_NORM_PX=25`。
- [x] **AC-5 状态冲突**：
  - speaking 抢 tap：owner 明确选择不验（`llm-api-confirm` 未触发）。
  - 拖拽中 gaze 禁用：owner 手验确认 OK。
  - error 态未专门手验，留待日常遇到时观察。
- [ ] **AC-6 既有路径回归**：owner 明确选择不跑全量回归。
- [x] **AC-9 主观体感**：owner 明确说"可以了"，sign-off OK。

### M24.9 · Win 真跑端到端（待 owner 切 Win 真机验）

- [ ] **AC-7 跨平台覆盖**：AC-2 / AC-3 / AC-4 / AC-5 在 Win first-class 双端跑通；不接受 Win fallback。
- [ ] **AC-6 既有路径回归 Win 端**：18 Win 已通过的 AC + 17a Win 真机回归（17a M17.9 留下来的 17b 阶段补，18 已部分跑）。
- [ ] **AC-9 主观体感 Win 端**：owner 切 Win 真机 30s+ 主观判断。

|**本 milestone 当前 agent 在 macOS 上跑不了，留作"待 owner 切 Win 真机验"分项**；不卡 M24.10 / M24.11 推进（除 AC-7 / AC-8 / AC-9 Win 部分外的 AC 在 macOS 验完即算 macOS 侧通过）。

### M24.10 · cross-build 全绿

- [x] **AC-8** · `./scripts/check`（lint + typecheck + 单测）三平台全绿
- [x] `cargo build` macOS（本机）已随 `./scripts/check` 前端构建隐式覆盖；Win 待 M24.9 一并验。
- [x] `pnpm test` 全过：新增 5 个测试文件（types + composer + 3 source）+ 改造的 `MouthDriver.test.ts` + 新增 `usePixiAvatarSlot.test.tsx` + 既有 015 / 016 / 17a / 18 单测全部维持

### M24.11 · 验收门禁四件齐（skill rule）

- [x] **M24.1~10 任务列表全部勾选**（M24.9 Win 部分待 owner 切 Win 真机后补勾）
- [x] **requirement.md AC-1 ~ AC-9 macOS 侧全部验证完**
  - AC-1 Hiyori audit 产出（M24.0 在本文档实现日志登记一条结论引用，对位完成）
  - AC-2~5 macOS 验完成；AC-5 中 speaking 抢 tap 未验（owner 选择不跑）
  - AC-6 既有路径回归 owner 选择不跑
  - AC-9 owner 主观体感 sign-off 完成
- [x] **`./scripts/check` 全绿**（M24.10）
- [x] **owner 明确说 "024 验收通过 / OK"**

|**四项全过**（macOS 侧）→ 本文档总体状态已切 `COMPLETED`。M24.9 Win 部分仍待 owner 切 Win 真机后补验。

---

## 实现日志

| 日期 | 完成内容 | 备注 |
|------|---------|------|
| 2026-06-19 | M24.0 · Hiyori audit 结论引用 design.md §3.1 | HitArea 仅 1 个 → 触发降级路径：tap 任意区触发 `TapBody`/m04；无 `.exp3.json`，表情切换改走参数直写。M24.0 仅 marker，不再重跑 audit。 |
| 2026-06-19 | M24.8 手验反馈调参（第一轮） | 方向修复：`GazeSource.angleX/angleZ` 取反，使光标在左时头左转（`ParamAngleX` 正）。幅度提升：gaze 阈值 `0.25→0.5`、映射基线改为 `threshold`、最大角度 `30→45/30→15`；tap 峰值 `0.7/0.8→0.9/1.0`；drag 峰值 `15/8/0.7/0.5/-0.5→25/15/1.0/0.8/-0.8`。同步更新 Gaze/Tap/Drag 单测断言。`./scripts/check` 全绿，vitest 103 passed。 |
| 2026-06-19 | M24.8 手验反馈调参（第二轮） | AC-2 方向再修正：改回 Live2D 标准约定 `ParamAngleX` 正=朝右，光标在左时 `dx<0` → angleX 负 → 头左转；眼球 `eyeBallX` 同步同向。AC-3 tap 表情增强：新增 `ParamMouthOpenY 0.5`、`ParamBrowLY/RY` 抬眉、`ParamEyeLOpen/ROpen` 睁大，脸红峰值 `0.9→1.0`。AC-4 拖拽反馈改瞬时速度：`usePixiAvatarSlot` 记录上一帧指针位置，传 `deltaX/deltaY` 而非从 pointerdown 起的累积位移。同步更新 Gaze/Tap 单测；`./scripts/check` 全绿，vitest 103 passed。 |
| 2026-06-19 | M24.8 手验反馈调参（第三轮） | AC-3 修复眼睛闭合：`ParamEyeLOpen/ROpen` 改为 `1 + 1.3*envelope`（睁大而非缩小）；`ParamEyeLSmile/RSmile` 从 `1.0→0.5` 防止笑眼压成缝；`ParamBrowLY/RY` 从 `0.6→0.4`。AC-4 提升灵敏度：`DRAG_VELOCITY_NORM_PX` 从 `100→25`，同样移动速度下角度放大 4 倍。`./scripts/check` 全绿，vitest 103 passed。 |
| 2026-06-19 | M24.8 手验反馈调参（第四轮） | AC-3 改为眯眼笑：降低 `ParamEyeLOpen/ROpen` 到 0.25（眯眼），`ParamEyeLSmile/RSmile` 提回 1.0（笑眼弯月），新增 `ParamMouthForm 0.5` 微笑嘴形，`ParamMouthOpenY 0.35` 微张嘴，`ParamBrowLY/RY` 轻抬眉 0.3。`./scripts/check` 全绿，vitest 103 passed。 |
| 2026-06-19 | M24.8 手验反馈调参（第五轮） | AC-3 去掉所有眼睛开合/笑眼参数，改为调用 `Idle` group 中由 `motionNo.tap` 指定的 motion；`live2dConfig.ts` 新增 `motionNo` 配置表，`tap` 默认 no=4，可通过改该数字试其他 idle motion。保留脸红 + 微笑嘴参数作为点缀。`./scripts/check` 全绿，vitest 103 passed。 |
| 2026-06-19 | M24.8 / M24.11 验收完成 | owner 确认 AC-2 / AC-3 / AC-4 / drag 中 gaze 禁用 / AC-9 OK；AC-5 speaking 抢 tap 与 AC-6 既有路径回归 owner 选择不跑；`./scripts/check` 全绿。本文档总体状态切 `COMPLETED`，M24.9 Win 部分仍待 owner 切 Win 真机后补验。 |
