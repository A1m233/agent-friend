# 024 桌宠 B 类交互 - 进度

## 总体状态

<!-- NOT_STARTED | IN_PROGRESS | COMPLETED | BLOCKED -->
NOT_STARTED

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

- [ ] 在实现日志里登记一句"audit 结论引用 design.md §3.1；触发降级路径，本期 tap 任意区 → `TapBody`/m04 motion；表情走参数直写"，对位 requirement.md §4.5.1。

### M24.1 · 框架与 ParamSource 接口（无依赖）

- [ ] 新增 `frontend/src/pet/sources/types.ts`：`ParamSource` interface（`readonly active: boolean` + `apply(sprite: Live2DSprite): void`），对位 [design.md §3.2](./design.md#32-统一-composepetrender-中央-hook本期核心)。
- [ ] 新增 `frontend/src/pet/composePetRender.ts`：装一次 `sprite.onRender`、按硬编码顺序调各 source.apply、返 detach 函数恢复原 onRender。
- [ ] 新增 `frontend/src/pet/composePetRender.test.ts`：
  - 多 source 顺序：mock sprite + 多 source.apply 被按数组顺序调
  - `source.active === false` 时跳过该 source
  - detach 函数恢复原 `onRender`
  - origOnRender 先于 source.apply 执行

### M24.2 · `GazeSource`（依赖 M24.1 types）

- [ ] 新增 `frontend/src/pet/sources/GazeSource.ts`，对位 [design.md §4.1](./design.md#41-gazesource视线--头跟随光标)。初值常量按 [design.md §5](./design.md#5-参数选择与初值表)。
- [ ] 新增 `frontend/src/pet/sources/GazeSource.test.ts`：
  - `updateCursor` 给定 cursor + spriteScreen → 验 `target.angleX/Y/Z + eyeBallX/Y` 计算正确（中心 / 边缘 / 阈值外 三个 cursor 位置）
  - `apply` 多次调用 → 验 `current` 经 EMA 收敛到 `target`（|current - target| < ε after N frames）
  - `setActive(false)` → `active === false`，composer 跳过

### M24.3 · `TapReactionSource`（依赖 M24.1 types）

- [ ] 新增 `frontend/src/pet/sources/TapReactionSource.ts`，对位 [design.md §4.2](./design.md#42-tapreactionsource点击反应表情)。
- [ ] 新增 `frontend/src/pet/sources/TapReactionSource.test.ts`：
  - `fire()` → `active === true` + sin envelope 在 [0, REACTING_DURATION_MS] 内正确（mock `performance.now`）
  - reacting 期间再 `fire()` → 忽略（active 不重置 / startTs 不更新）
  - `REACTING_DURATION_MS` 后 → `active === false`

### M24.4 · `DragReactionSource`（依赖 M24.1 types）

- [ ] 新增 `frontend/src/pet/sources/DragReactionSource.ts`，对位 [design.md §4.3](./design.md#43-dragreactionsource拖拽反馈)。
- [ ] 新增 `frontend/src/pet/sources/DragReactionSource.test.ts`：
  - `setDragging(true, 0.5, 0)` → `active === true` + apply 时 `ParamAngleZ` 按 dragVelX 算
  - `setDragging(false)` → 进 release 阶段，envelope 线性衰减
  - `RELEASE_DURATION_MS` 后 → `active === false`
  - `updateDragDirection(vx, vy)` 在 dragging 期间更新方向；非 dragging 期间忽略

### M24.5 · 18 `MouthDriver` 改造（依赖 M24.1 types）

- [ ] 改造 `frontend/src/pet/MouthDriver.ts`，对位 [design.md §4.5](./design.md#45-18-mouthdriver-改造)：
  - 去掉 `attach()` 里 hook `sprite.onRender` 的整段（line 70-87）
  - 去掉 `origOnRender` 字段 + `detach()` 里恢复 `origOnRender` 的段（line 158-162）
  - `setMouthOpen()` 不再直接 `setParameterValueById`（移除 line 147）
  - 新增 `apply(sprite: Live2DSprite): void` + `get active(): boolean { return this.sprite !== null }`
  - 接口 `MouthDriver` 扩展自 `ParamSource`
- [ ] 跑 `pnpm test -- MouthDriver` 看 `frontend/src/pet/MouthDriver.test.ts` 现有断言：
  - 走 `currentMouthValue` 数值演进的断言 → 不动
  - 走 `setParameterValueById` 被调的断言 → 测试用例里手动 `driver.apply(mockSprite)` 一步，然后断言 `setParameterValueById` 被调
  - 改动幅度预估 < 20 行测试代码
- [ ] **18 既有 AC-5 lip-sync 节奏验收**保持通过（M24.8 真跑时验，本 milestone 仅单测层）。

### M24.6 · 17a `usePixiAvatarSlot` 改造（click vs drag）

- [ ] 改 `frontend/src/pages/pet/usePixiAvatarSlot.ts`，对位 [design.md §3.3 / §4.4](./design.md#33-click-vs-drag-区分17a-slot-drag-handler-改造)：
  - 签名加 `onSlotClick?: (e) => void` + `onSlotDragMove?: (vx: number, vy: number) => void`
  - `pointerdown` 记录 startPos/startTime/dragArmed=false，**不立即** setIsDragging
  - `globalpointermove` 超 `DRAG_MOVE_THRESHOLD_PX (5)` 才 setIsDragging + 走 17a 既有 drag 流程，期间归一化喂 `onSlotDragMove(vx, vy)`
  - `pointerup` / `pointerupoutside`：未 dragArmed + 时长 < `CLICK_MAX_DURATION_MS (300)` + 距离 < 5px → 派发 `onSlotClick`；否则走既有 drag commit
  - useEffect deps 不重建 PIXI Application：用 ref-sync stable callback
- [ ] 新增 `frontend/src/pages/pet/usePixiAvatarSlot.test.ts`（如不存在则建）：
  - mock PIXI 事件流：短按短移 (<5px, <300ms) → `onSlotClick` 被调、`setIsDragging` 没被调
  - mock 事件流：超阈值 → `setIsDragging` 被调、`onSlotClick` 没被调
  - mock 事件流：短按但时长 >300ms → 既不 click 也不 drag (noop)
  - drag 期间归一化 dragMove vx/vy 在 [-1, 1] 范围
- [ ] **17a 既有 AC-4 / AC-5 行为零退化**：超阈值的拖拽行为完全等价 17a（M24.8 真跑验）。

### M24.7 · `usePetInteractions` 胶水 + `PetApp` 装配（依赖 M24.1~6）

- [ ] 改 `frontend/src/pet/live2dConfig.ts`：`PetLive2DConfig.motionGroups` 类型扩 `tap?: string | null`，`PET_LIVE2D_CONFIG.motionGroups.tap = "TapBody"`。
- [ ] 新增 `frontend/src/pages/pet/usePetInteractions.ts`，对位 [design.md §4.6](./design.md#46-composepetrender-与-usepetinteractions)：
  - 装载 composer（等 `spriteRef.current` ready）传 sources `[gaze, tap, drag, mouthDriver]`，return 时 detach
  - `pet://cursor` 副订阅 → `gaze.updateCursor(x, y, spriteScreen)`
  - `isDragging` 切到 true 时 `drag.setDragging(true) + gaze.setActive(false)`；false 时反向
  - `onSlotClick` 内 `if (phase === "speaking") return` + `tap.fire() + sprite.startMotion("TapBody")`
  - `onSlotDragMove` 内 `drag.updateDragDirection(vx, vy)`
- [ ] 改 `frontend/src/pages/pet/App.tsx`，对位 [design.md §4.7](./design.md#47-petapptsx-改造入口胶水)：
  - 装 `usePetInteractions(spriteRef, spriteScreen, isDragging, driver)` 得到 `{ onSlotClick, onSlotDragMove }`
  - 透传到 `usePixiAvatarSlot(stageRef, setSpriteScreen, setIsDragging, onSlotClick, onSlotDragMove)`
  - **18 既有 lip-sync useEffect 保留不动**（`driver.attach/detach` 仍是 PetApp 调，driver 内部不再 hook onRender）
- [ ] dev observability：dev 模式下打 `gaze.current` / `tap.fire` / `drag.setDragging` log（参 18 App.tsx line 134-168 同款）。

### M24.8 · macOS 真跑端到端

- [ ] **AC-2 视线跟随**：开 PetOverlay → 移光标从屏一角扫到对角 → 头部 + 眼球跟随；停在桌宠中央 → 头回中立；移到屏幕远角（超阈值）→ 头眼归零；dev console 看 `gaze.current` 参数滑动无阶跃。
- [ ] **AC-3 点击 motion 触发（降级路径）**：点桌宠任意位置 → `TapBody`/m04 触发（手摆动）+ 脸颊红 / 笑眼 800ms；motion 进行中再点 → 不重复。
- [ ] **AC-4 拖拽视觉反馈**：在桌宠上 mouse down + 拖 → `ParamAngleZ` 摇晃 + 惊讶眉 + 微张嘴；松开 → 300ms 内回零；桌宠确实跟着拖到新位置（17a 既有行为，AC-4 通过）。
- [ ] **AC-5 状态冲突**：
  - 跑 LLM 前先获 owner 同意 →`./scripts/dev-fire-source/run.sh cron:bedtime` 触发 speaking → 点击桌宠 → tap 不触发；speaking 结束后 tap 重新可用
  - 拖拽中 dev console 看 `gaze.active === false`
  - error 态（手动 raise）→ 各 source 不崩
- [ ] **AC-6 既有路径回归**：
  - 015 全 9 AC 跑通（dev CLI + bubble 显隐 / sessionProjection 等）
  - 016 全 12 AC 跑通（bubble 窗口跟随 / 跨 Space / sticky 等）
  - 17a 全 12 AC（pet 主窗整屏 / PIXI canvas / sprite drag / bubble 跟随 / 操作栏 hover gate 等）—— macOS 7/12 已通过的 + 本期受影响的 AC-4 重验
  - 18 全 12 AC（Live2D 加载 / 4 态机 / push event 驱动 / lip-sync 文本 cadence / `MouthDriver` 接口扩展位 / 既有路径回归）
- [ ] **AC-9 主观体感**：owner 用 PetOverlay 30s+ 主观判断三件事感受。**等 owner 明确说 OK**。

### M24.9 · Win 真跑端到端（待 owner 切 Win 真机验）

- [ ] **AC-7 跨平台覆盖**：AC-2 / AC-3 / AC-4 / AC-5 在 Win first-class 双端跑通；不接受 Win fallback。
- [ ] **AC-6 既有路径回归 Win 端**：18 Win 已通过的 AC + 17a Win 真机回归（17a M17.9 留下来的 17b 阶段补，18 已部分跑）。
- [ ] **AC-9 主观体感 Win 端**：owner 切 Win 真机 30s+ 主观判断。

**本 milestone 当前 agent 在 macOS 上跑不了，留作"待 owner 切 Win 真机验"分项**；不卡 M24.10 / M24.11 推进（除 AC-7 / AC-8 / AC-9 Win 部分外的 AC 在 macOS 验完即算 macOS 侧通过）。

### M24.10 · cross-build 全绿

- [ ] **AC-8** · `./scripts/check`（lint + typecheck + 单测）三平台全绿
- [ ] `cargo build` macOS（本机）+ Win（待 M24.9 一并验）
- [ ] `pnpm test` 全过：新增 5 个测试文件（types + composer + 3 source）+ 改造的 `MouthDriver.test.ts` + 新增 `usePixiAvatarSlot.test.ts` + 既有 015 / 016 / 17a / 18 单测全部维持

### M24.11 · 验收门禁四件齐（skill rule）

- [ ] **M24.1~10 任务列表全部勾选**（M24.9 Win 部分待 owner 标）
- [ ] **requirement.md AC-1 ~ AC-9 全部验证完**
  - AC-1 Hiyori audit 产出（M24.0 在本文档实现日志登记一条结论引用，对位完成）
  - AC-2~5 macOS 验（M24.8）+ Win 验（M24.9）
  - AC-6 既有路径回归（M24.8 macOS + M24.9 Win）
  - AC-7 跨平台 + AC-8 cross-build（M24.9 + M24.10）
  - AC-9 owner 主观体感 sign-off（M24.8 macOS + M24.9 Win）
- [ ] **`./scripts/check` 全绿**（M24.10）
- [ ] **owner 明确说 "024 验收通过 / OK"**

**四项全过**才把本文档总体状态改 `COMPLETED`。任一未达 → 总体状态停留 `IN_PROGRESS`，明确告知 owner 哪项未达 + 需要做什么。

---

## 实现日志

| 日期 | 完成内容 | 备注 |
|------|---------|------|
