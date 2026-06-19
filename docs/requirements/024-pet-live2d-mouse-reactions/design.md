# 024 · 桌宠 B 类交互 - 技术方案

## 状态

<!-- 草稿（Draft） | 已确认（Confirmed） -->
已确认（Confirmed）

---

## 需求文档

→ [requirement.md](./requirement.md)

---

## 1. 设计目标回顾

在 [需求 018](../018-pet-live2d-state-and-lipsync/) 完成的「Hiyori Live2D + 4 态机（`idle` / `thinking` / `speaking` / `error`）+ Codex push 驱动 + lip-sync」基础上，叠加三件 B 类（纯前端、不出进程、不入 agent inbox）的鼠标输入反馈：

- 视线 / 头跟随光标（全屏 mousemove → 低通 → `ParamAngleX/Y/Z + ParamEyeBallX/Y`）
- 点击触发 motion + 临时反应态（叠加 18 4 态机外的并行 transient 层）
- 拖拽时 Live2D 角度叠加 + 表情参数偏移（沿用 17a slot drag 在 fullscreen overlay 内移动）

不动 18 状态机命名空间、不引入反向 HTTP、不立策略 gate。Hiyori audit 已在本设计阶段完成（§3.1），降级路径已落地。

---

## 2. 整体改动地图

### 2.1 文件清单

**新增文件**：

| 文件 | 职责 |
| --- | --- |
| `frontend/src/pet/composePetRender.ts` | 中央 `sprite.onRender` 装载 hook + `ParamSource` 接口 + 多 source 按硬编码优先级顺序调 `apply` |
| `frontend/src/pet/sources/GazeSource.ts` | 视线 / 头跟随光标的 `ParamSource` 实现（持有 EMA 滤波状态 + target 计算 + apply 写 5 个参数） |
| `frontend/src/pet/sources/TapReactionSource.ts` | tap 反应表情参数偏移的 `ParamSource` 实现（脸颊 + 笑眼 + 时长门控） |
| `frontend/src/pet/sources/DragReactionSource.ts` | 拖拽角度叠加 + 惊讶表情参数的 `ParamSource` 实现（含 mouse up 后线性回归） |
| `frontend/src/pet/sources/types.ts` | `ParamSource` interface 定义（供 4 个 source + MouthDriver 共用） |
| `frontend/src/pages/pet/usePetInteractions.ts` | PetApp 内胶水 hook：装 composer + 持 3 个 source 实例 + 监听 cursor channel + 派发 click event |

**改造文件**：

| 文件 | 改动 |
| --- | --- |
| `frontend/src/pet/MouthDriver.ts` | 去掉自己 hook `sprite.onRender` 的段（含 `origOnRender` 字段 + detach 时恢复）；新增公开 `apply(sprite)` 方法 + `active` getter；让 `TextCadenceMouthDriver` 实现 `ParamSource`。**接口（`attach / onTextDelta / onAudioFrame / detach`）+ 18 调用方 + 既有单测断言完全不动**。 |
| `frontend/src/pages/pet/usePixiAvatarSlot.ts` | `slot.on("pointerdown")` 不再立即 `setIsDragging(true)`：记录 startPos + startTime；`globalpointermove` 超阈值（5px）才 `setIsDragging`；`pointerup` 时未进 drag + 时长<300ms → 通过 props `onSlotClick` 派发。17a AC-4（sprite drag）零退化。 |
| `frontend/src/pages/pet/App.tsx` | 启动 `usePetInteractions(spriteRef, app, spriteScreen, isDragging, phase)`；把 18 既有 lip-sync useEffect 改成"把 MouthDriver 注册进 composer"；新增 `onSlotClick` 透传到 `usePixiAvatarSlot`。 |
| `frontend/src/pet/live2dConfig.ts` | 新增 `motionGroups.tap = "TapBody"`（Hiyori 自带 group）。 |

**不动文件**（R-4.6.5 / R-4.6.1~4 既有路径回归）：

- 015：`push_subscriber.rs` / `petBubble.ts` / `petBubblePolicy.ts` / `attachBubbleWindowSync.ts` / `startPetBubbleSubscriber.ts` / sessionProjection
- 016：bubble 独立窗口全套
- 017 / 17a：alpha hit-test (`petAlphaHitTest.ts`) / cursor passthrough (`usePetPassthrough.ts`) / spritePos 数据流（`emitSpritePos` / Rust `update_sprite_pos` / `bubble_window.rs::run_follow_loop`）
- 018：`usePetLive2D.ts`（Live2D 加载） / `petState.ts`（4 态机）/ `petStatePolicy.ts` / `startPetStateSubscriber`

### 2.2 数据流总览

```
Rust 60Hz pet://cursor (CSS px, 全屏)
   ├─→ usePetPassthrough （17a/18 既有，不动）
   │     └→ setIgnoreCursorEvents / cursorOnSprite
   └─→ usePetInteractions （新增副订阅）
         └→ GazeSource.updateCursor() → target 计算 + EMA → composer.apply

slot.on(pointerdown / globalpointermove / pointerup) （17a 既有，本期改造）
   ├─→ 未超阈值 + 短按 → triggerClick → TapReactionSource.fire + sprite.startMotion("TapBody")
   └─→ 超阈值 → setIsDragging(true) → 17a 既有 emitSpritePos + DragReactionSource.setActive(true)

agent://push envelope （18 既有，不动）
   └→ usePetStateStore.ingest → phase 切换 → MouthDriver attach/detach

composePetRender (本期核心)
sprite.onRender = (renderer) => {
  origOnRender?.(renderer);                                  // Live2D motion / breath apply
  if (gaze.active && !drag.active) gaze.apply(sprite);
  if (tap.active) tap.apply(sprite);
  if (drag.active) drag.apply(sprite);
  if (lipSync.active) lipSync.apply(sprite);
}
```

---

## 3. 架构决策

### 3.1 Hiyori audit 结果与降级落地

读 `frontend/public/live2d-models/hiyori/Hiyori.model3.json` + `Hiyori.cdi3.json` 完成 audit：

**HitAreas**（model3.json）：

```json
"HitAreas": [{ "Id": "HitArea", "Name": "Body" }]
```

只 1 个 hit area，**不足以细分 head / body** → 触发 [requirement §4.5.3](./requirement.md#45-day-1-hiyori-资源-audit--降级路径) 降级路径：本期 tap 任意区都触发同一 motion。

**Motions**（model3.json）：

- `Idle` group：9 个（`Hiyori_m01.motion3.json` ~ `m10.motion3.json`，排除 m04），都是呼吸 / 待机类
- `TapBody` group：1 个（`Hiyori_m04.motion3.json`）

**Expressions**（model3.json + 文件系统）：

- 无 `.exp3.json` 文件，model3.json 也无 `Expressions` 字段
- 只有 `LipSync` 参数组（`ParamMouthOpenY`）+ `EyeBlink` 参数组（`ParamEyeLOpen` / `ParamEyeROpen`）

**Parameters**（cdi3.json，本设计的关键发现）：

Hiyori 暴露 64+ 参数，本期重点用到：

| 参数 | 用途 |
| --- | --- |
| `ParamAngleX` / `ParamAngleY` / `ParamAngleZ` | 头部偏转（视线跟随主参数） |
| `ParamEyeBallX` / `ParamEyeBallY` | 眼球（视线跟随细节） |
| `ParamEyeLOpen` / `ParamEyeROpen` | 眨眼（18 EyeBlink 在用，本期不动） |
| `ParamEyeLSmile` / `ParamEyeRSmile` | 笑眼（tap reaction） |
| `ParamBrowLForm` / `ParamBrowRForm` | 眉形（drag reaction 惊讶眉） |
| `ParamBrowLY` / `ParamBrowRY` | 眉上下（drag reaction 抬眉） |
| `ParamMouthForm` | 嘴形（drag reaction 微张） |
| `ParamMouthOpenY` | 嘴张（18 lip-sync 在用，本期不动） |
| `ParamCheek` | 脸颊红（tap reaction） |
| `ParamBodyAngleZ` | 身体旋转（drag reaction 摇晃） |

**结论**：表情不需要加载 `.exp3` 文件，**直接写参数即可**。AC-3 / AC-4 「motion 触发」「表情参数偏离 idle 值」语义不变，实现路径换成参数直写。

**`live2dConfig.ts` 新增**：

```ts
motionGroups: {
  idle: "Idle",
  thinking: null,
  speaking: null,
  error: null,
  tap: "TapBody",   // 新增：tap 反应触发的 motion group
}
```

（`PetLive2DConfig.motionGroups` 类型相应扩 `tap` 字段，不破坏 18 既有取用。）

### 3.2 统一 `composePetRender` 中央 hook（本期核心）

**为什么需要**：18 `TextCadenceMouthDriver.attach()` 自己 hook `sprite.onRender`（motion apply 后强写 `ParamMouthOpenY`，避免被 Live2D motion 覆盖）。本期视线 / tap / drag 都需要类似的"在 motion 之后写参数"——多个独立 hook 互相覆盖会撞车（最后 attach 的赢，前面的全失效）。

**方案**：抽一个中央 composer 装一次 `sprite.onRender`，按硬编码优先级顺序调各 source 的 `apply(sprite)`。

```ts
// frontend/src/pet/sources/types.ts
export interface ParamSource {
  /** composer 每帧 onRender 时检查；false 时跳过 apply（noop）。 */
  readonly active: boolean;
  /** motion apply 之后，由 composer 在 onRender 内调；source 负责 setParameterValueById。 */
  apply(sprite: Live2DSprite): void;
}
```

```ts
// frontend/src/pet/composePetRender.ts
export interface ComposerSources {
  /** 按数组顺序 = 硬编码优先级（越靠后越后写，覆盖前面） */
  readonly sources: readonly ParamSource[];
}

/**
 * 装一次 sprite.onRender；返 detach 函数恢复原 onRender。
 *
 * 排序硬编码：调用方按 [gaze, tap, drag, lipSync] 顺序传 sources 数组。
 * 不上 priority 字段 / polymorphism，5 个 source 不需要。
 */
export function composePetRender(
  sprite: Live2DSprite,
  sources: readonly ParamSource[],
): () => void {
  const orig = (sprite as { onRender?: (r: unknown) => void }).onRender ?? null;
  (sprite as { onRender: (r: unknown) => void }).onRender = (renderer) => {
    orig?.(renderer);              // Live2D motion / breath / physics apply
    for (const src of sources) {
      if (src.active) src.apply(sprite);
    }
  };
  return () => {
    (sprite as { onRender: ((r: unknown) => void) | null }).onRender = orig;
  };
}
```

**为什么硬编码顺序而非 priority 字段**：

- 5 个 source（gaze / tap / drag / lipSync），产品上的优先级是固定的
- priority 字段意味着 source 之间运行时排序 + polymorphism，over-engineer
- 调用方（`usePetInteractions`）传 `[gaze, tap, drag, lipSync]` 顺序即可表达

**source 之间参数冲突**：

| Source | 主写参数 |
| --- | --- |
| gaze | `ParamAngleX/Y/Z` + `ParamEyeBallX/Y` |
| tap | `ParamCheek` + `ParamEyeLSmile/RSmile` |
| drag | `ParamAngleZ`（覆盖 gaze）+ `ParamBodyAngleZ` + `ParamBrowL/RForm` + `ParamBrowL/RY` + `ParamMouthForm` |
| lipSync | `ParamMouthOpenY` |

drag 写 `ParamAngleZ` 会覆盖 gaze 的同名参数 → composer 调用方在 `usePetInteractions` 内用 `gaze.active && !drag.active` 短路（§3.5）。tap 主写 `ParamCheek/Smile`，不跟其他 source 撞参数。

### 3.3 click vs drag 区分（17a slot drag handler 改造）

**现状**（`usePixiAvatarSlot.ts:294`）：

```ts
slot.on("pointerdown", (e) => {
  pointerStart = { x: e.global.x, y: e.global.y };
  slotStart = { x: slot.x, y: slot.y };
  setIsDragging(true);  // 立即进 drag
  slot.cursor = "grabbing";
});
```

**问题**：pointerdown 立即进 drag 模式，没法区分"短按一下"（click）和"按住拖动"（drag）。

**方案**：

```ts
const DRAG_MOVE_THRESHOLD_PX = 5;
const CLICK_MAX_DURATION_MS = 300;

let pointerStart: { x: number; y: number } | null = null;
let slotStart: { x: number; y: number } | null = null;
let downTimestamp = 0;
let dragArmed = false;  // 标记：已经超阈值，进入 drag 模式

slot.on("pointerdown", (e) => {
  pointerStart = { x: e.global.x, y: e.global.y };
  slotStart = { x: slot.x, y: slot.y };
  downTimestamp = performance.now();
  dragArmed = false;
  // **不**立即 setIsDragging
});

slot.on("globalpointermove", (e) => {
  if (!pointerStart || !slotStart) return;
  const dx = e.global.x - pointerStart.x;
  const dy = e.global.y - pointerStart.y;
  if (!dragArmed) {
    if (Math.hypot(dx, dy) > DRAG_MOVE_THRESHOLD_PX) {
      dragArmed = true;
      setIsDragging(true);
      slot.cursor = "grabbing";
    } else {
      return;  // 还没超阈值，不开始 drag
    }
  }
  slot.x = slotStart.x + dx;
  slot.y = slotStart.y + dy;
  emitSpritePos();
});

const endPointer = (e: PIXI.FederatedPointerEvent) => {
  if (!pointerStart) return;
  const duration = performance.now() - downTimestamp;
  const dx = e.global.x - pointerStart.x;
  const dy = e.global.y - pointerStart.y;
  const dist = Math.hypot(dx, dy);
  const wasClick =
    !dragArmed && duration < CLICK_MAX_DURATION_MS && dist < DRAG_MOVE_THRESHOLD_PX;
  pointerStart = null;
  slotStart = null;
  if (dragArmed) {
    setIsDragging(false);
    slot.cursor = "grab";
    emitSpritePos();  // commit 最终位置（17a 既有）
  }
  dragArmed = false;
  if (wasClick) onSlotClick?.(e);  // 派发 click（新增 props）
};
slot.on("pointerup", endPointer);
slot.on("pointerupoutside", endPointer);
```

**`usePixiAvatarSlot` 签名扩展**：

```ts
export function usePixiAvatarSlot(
  stageRef: RefObject<HTMLDivElement | null>,
  setSpriteScreen: (s: { x: number; y: number; w: number; h: number } | null) => void,
  setIsDragging: (v: boolean) => void,
  onSlotClick?: (e: PIXI.FederatedPointerEvent) => void,  // 新增
): UsePixiAvatarSlotHandles
```

**17a AC-4 / AC-5 零退化分析**：

- 超阈值（>5px）/ 持续时间长的按住移动 → 走 17a 既有 drag 路径，行为完全等价（setIsDragging / emitSpritePos / slot.x/y 更新 / Rust update_sprite_pos）
- 短按短移（实际 mouse 抖动 < 5px）→ 新增的 click 路径，**17a 没有这个 case**（17a 短按短移会触发一次 setIsDragging(true) → 立即 setIsDragging(false)，等价 noop）
- 阈值 5px 比正常人手抖动大，比真拖动小，不会误判

**为什么 click 阈值不用 0px**：

- mouse 物理抖动 + DPI 缩放误差 → 5px 是常见 UX 库（react-dnd / framer-motion）默认值
- 大于 5px 才算 drag，给真实"想点一下"的用户合理容差

### 3.4 cursor channel 副订阅与不依赖穿透状态

**前提**：Rust 端 `spawn_cursor_feed`（17a 既有，`src-tauri/src/lib.rs`）以 ~60Hz 向 `pet://cursor` 发"光标相对 pet 窗 content 区的 CSS px 坐标"，**无论 `setIgnoreCursorEvents` 是 true 还是 false 都发**——这是 17a 设计的初衷（webview 收不到自己的 mousemove 时，Rust 兜底）。

**18 既有用法**（`usePetPassthrough.ts:110`）：

```ts
const unlisten = listen<{ x: number; y: number }>("pet://cursor", (e) => {
  // ... alpha hit-test + setIgnoreCursorEvents 切换 ...
});
```

**本期新增**：`usePetInteractions` 内独立 `listen<{x,y}>("pet://cursor", ...)`（tauri event 是广播，cost 可忽略），把 cursor 喂给 `GazeSource`。

**为什么不复用 17a 既有 listener**：

- 17a `usePetPassthrough` 把 cursor 用来"决定穿透状态"，回调签名是 `(v: boolean) => void` 类型，跟 gaze 需要的"喂坐标 + spriteScreen"不同
- 独立 listener 实现简单，不动 17a 代码
- 性能上：两个 listener × 60Hz × 8 byte payload ≈ 1KB/s，可忽略

**实现**：

```ts
// usePetInteractions.ts （摘）
useEffect(() => {
  if (!isTauri()) return;
  let cancelled = false;
  let unlisten: (() => void) | null = null;
  void listen<{ x: number; y: number }>("pet://cursor", (e) => {
    if (!spriteScreen) return;
    gazeSource.updateCursor(e.payload.x, e.payload.y, spriteScreen);
  }).then((u) => {
    if (cancelled) safeUnlisten(u);
    else unlisten = u;
  });
  return () => {
    cancelled = true;
    safeUnlisten(unlisten);
  };
}, [spriteScreen]);
```

**`Config.MouseFollow = false` 保持**：easy-live2d 内置 `MouseFollow` 是基于 webview 内 `mousemove`，在透明窗 `setIgnoreCursorEvents(true)` 时收不到事件，18 已关。本期不用动这个 config——我们自己用 cursor channel 驱动。

### 3.5 状态优先级落地

| 冲突 | 解决方式 | 位置 |
| --- | --- | --- |
| speaking 抢 tap | click handler 内 `if (phase === "speaking") return` | `usePetInteractions` 的 `onSlotClick` 回调 |
| drag 抢 click | pointer 阈值区分（>5px = drag、不超 = click），天然互斥 | `usePixiAvatarSlot` `endPointer` |
| drag 抢 gaze | composer 调用方传 sources 数组时，gaze 在 source 内自检 `!drag.active` | `GazeSource.active` getter |
| tap 抢 tap（连点） | TapReactionSource 在 reacting 期间忽略 fire | `TapReactionSource.fire` |
| error 态 | 各 source `active` getter 检查 sprite 是否传入 + 模型是否 ready | source 自检 |

**speaking 抢 tap 单行实现**：

```ts
// usePetInteractions.ts
const onSlotClick = useCallback(() => {
  const phase = usePetStateStore.getState().phase;
  if (phase === "speaking") return;  // speaking 抢镜
  tapSource.fire();
  void spriteRef.current?.startMotion({
    group: PET_LIVE2D_CONFIG.motionGroups.tap ?? "TapBody",
    no: 0,
    priority: Priority.Normal,
  });
}, [spriteRef, tapSource]);
```

**优先级初版**（[requirement §4.4.1](./requirement.md#44-状态优先级与互斥) 不进 AC 锁定，可在 progress.md 调整）：

```
speaking > drag > tap > gaze
（lip-sync 在 speaking 期间永远有效，写自己的 ParamMouthOpenY 不跟其他冲突）
```

**为什么 listening 不在表里**：18 4 态机命名是 `idle / thinking / speaking / error`，无 `listening`——Phase 1 用户说的 listening = idle / thinking（用户在听 / agent 在想，桌宠不说话）。本设计统一用 18 命名空间。

### 3.6 与既有 18 / 17a / 015 / 016 plumbing 的关系

按 [requirement §4.6](./requirement.md#46-既有路径回归)：

**18 接口不动**：

- `usePetLive2D.ts`：Live2D 加载 + idle motion 启动，不动
- `usePetStateStore` / `petStatePolicy`：4 态机 + push 驱动，不动；本期 source 内**只读** `phase`，不写
- `startPetStateSubscriber`：push 订阅入口，不动
- `MouthDriver` interface（`attach / onTextDelta / onAudioFrame / detach`）：不动；内部实现策略改（独立 onRender hook → 暴露 apply 方法供 composer 调），既有调用方代码与单测断言全不动

**18 调用方代码改动最小化**（`pet/App.tsx` line 171-211）：

- 既有 `useMemo(() => new TextCadenceMouthDriver(), [])` 不动
- 既有 `driver.attach(sprite)` / `driver.detach()` 不动（attach 时不再 hook onRender，但 attach 这个动作本身保留——driver 内部把 `this.sprite = sprite` 记下来）
- 既有 listen<PushEnvelope> 转发 text_delta 不动
- 新增：把 driver 实例作为 source 之一传给 composer（在 `usePetInteractions`）

**17a 接缝点动 / 不动**：

| 接缝点 | 状态 |
| --- | --- |
| #1 avatar-slot Container 内 children | 18 已替占位为 Live2DSprite；本期不动 |
| #2 sprite world position 数据流 | 不动（drag 时仍 `emitSpritePos` → Rust） |
| #3 cursor alpha hit-test target | 不动（`usePetPassthrough` 仍指 slot 区域） |
| #4 状态机 hook 点 | 18 已挂；本期不动 |
| #5 操作栏 hover bridge | 不动 |

**17a drag handler 改 click 区分**（本期唯一改 17a 的地方）：是接缝点 #2 / #5 之外的扩展——不动数据流方向（仍 emitSpritePos）、不动操作栏 hover（drag 期间 isDragging 仍触发 sticky 显示）。新增 props `onSlotClick` 是纯新增、向下兼容。

---

## 4. 模块细节

### 4.1 `GazeSource`（视线 / 头跟随光标）

```ts
// frontend/src/pet/sources/GazeSource.ts
import type { Live2DSprite } from "easy-live2d";
import type { ParamSource } from "./types";

// 初值常量（[requirement §1.3 / §4.5] 主观体感不对在 progress.md 调）
const DISTANCE_THRESHOLD_FRAC = 0.25;  // 屏幕宽的 1/4 起，超出归零
const ANGLE_X_MAX_DEG = 30;
const ANGLE_Y_MAX_DEG = 30;
const ANGLE_Z_MAX_DEG = 10;
const EYEBALL_MAX = 1;  // ParamEyeBallX/Y range 通常 [-1, 1]
const EMA_TAU_MS = 200;

export class GazeSource implements ParamSource {
  private isActive = true;        // 默认开；composer 调用方在 drag 时 short-circuit
  // EMA 滤波后的当前值
  private current = { angleX: 0, angleY: 0, angleZ: 0, eyeBallX: 0, eyeBallY: 0 };
  // updateCursor 算出的目标值（未滤波）
  private target = { angleX: 0, angleY: 0, angleZ: 0, eyeBallX: 0, eyeBallY: 0 };
  private lastApplyTs = 0;

  /** 由 usePetInteractions 外部 setter 控制（drag.active 时设 false） */
  setActive(v: boolean): void {
    this.isActive = v;
  }

  get active(): boolean {
    return this.isActive;
  }

  /** 由 pet://cursor 副订阅 60Hz 喂；输入是 CSS px（viewport 内）。 */
  updateCursor(
    cx: number,
    cy: number,
    sprite: { x: number; y: number; w: number; h: number },
  ): void {
    const spriteCx = sprite.x + sprite.w / 2;
    const spriteCy = sprite.y + sprite.h / 2;
    const dx = cx - spriteCx;
    const dy = cy - spriteCy;
    const dist = Math.hypot(dx, dy);

    const viewportW = window.innerWidth;
    const viewportH = window.innerHeight;
    const threshold = Math.max(viewportW, viewportH) * DISTANCE_THRESHOLD_FRAC;
    // 距离超阈值时 factor 直接 0；不超时 factor = 1（不衰减）
    // 设计：阈值内全跟，阈值外平滑归零（EMA 让回归丝滑）
    const factor = dist > threshold ? 0 : 1;

    const halfW = viewportW / 2;
    const halfH = viewportH / 2;
    this.target.angleX = clamp((dx / halfW) * ANGLE_X_MAX_DEG * factor, -ANGLE_X_MAX_DEG, ANGLE_X_MAX_DEG);
    // 注意 Y：屏幕 Y 朝下 / Live2D angleY 正向头抬起 → 取反
    this.target.angleY = clamp((-dy / halfH) * ANGLE_Y_MAX_DEG * factor, -ANGLE_Y_MAX_DEG, ANGLE_Y_MAX_DEG);
    this.target.angleZ = clamp((dx / halfW) * ANGLE_Z_MAX_DEG * factor, -ANGLE_Z_MAX_DEG, ANGLE_Z_MAX_DEG);
    this.target.eyeBallX = clamp((dx / halfW) * EYEBALL_MAX * factor, -EYEBALL_MAX, EYEBALL_MAX);
    this.target.eyeBallY = clamp((-dy / halfH) * EYEBALL_MAX * factor, -EYEBALL_MAX, EYEBALL_MAX);
  }

  apply(sprite: Live2DSprite): void {
    // EMA 低通：每帧用 dt 估算
    const now = performance.now();
    const dt = this.lastApplyTs === 0 ? 16 : now - this.lastApplyTs;
    this.lastApplyTs = now;
    const alpha = Math.min(1, dt / (EMA_TAU_MS + dt));
    this.current.angleX += alpha * (this.target.angleX - this.current.angleX);
    this.current.angleY += alpha * (this.target.angleY - this.current.angleY);
    this.current.angleZ += alpha * (this.target.angleZ - this.current.angleZ);
    this.current.eyeBallX += alpha * (this.target.eyeBallX - this.current.eyeBallX);
    this.current.eyeBallY += alpha * (this.target.eyeBallY - this.current.eyeBallY);

    sprite.setParameterValueById("ParamAngleX", this.current.angleX);
    sprite.setParameterValueById("ParamAngleY", this.current.angleY);
    sprite.setParameterValueById("ParamAngleZ", this.current.angleZ);
    sprite.setParameterValueById("ParamEyeBallX", this.current.eyeBallX);
    sprite.setParameterValueById("ParamEyeBallY", this.current.eyeBallY);
  }
}

function clamp(v: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, v));
}
```

**初值表**（[requirement §1.3] 实现期可在 progress.md 调）：

| 常量 | 值 | 含义 |
| --- | --- | --- |
| `DISTANCE_THRESHOLD_FRAC` | 0.25 | 光标距桌宠超过 max(viewportW, viewportH)×0.25 时归零 |
| `ANGLE_X_MAX_DEG` | 30 | 头部 X 轴最大偏转角（屏幕宽边缘对应） |
| `ANGLE_Y_MAX_DEG` | 30 | 头部 Y 轴最大偏转角 |
| `ANGLE_Z_MAX_DEG` | 10 | 头部 Z 轴最大偏转角（Live2D 通常较小） |
| `EYEBALL_MAX` | 1.0 | 眼球最大偏转（Live2D 通常 [-1, 1]） |
| `EMA_TAU_MS` | 200 | 一阶 EMA 时间常数（响应到稳态 ~3τ = 600ms） |

### 4.2 `TapReactionSource`（点击反应表情）

```ts
// frontend/src/pet/sources/TapReactionSource.ts
import type { Live2DSprite } from "easy-live2d";
import type { ParamSource } from "./types";

const REACTING_DURATION_MS = 800;  // motion m04 时长 ~1.5s，反应表情压短一些避免久挂
const CHEEK_PEAK = 0.7;
const EYE_SMILE_PEAK = 0.8;

export class TapReactionSource implements ParamSource {
  private startTs: number | null = null;  // null = 不在 reacting

  get active(): boolean {
    if (this.startTs === null) return false;
    return performance.now() - this.startTs < REACTING_DURATION_MS;
  }

  /** click handler 调；motion 进行中（active=true）忽略后续 fire。 */
  fire(): void {
    if (this.active) return;
    this.startTs = performance.now();
  }

  apply(sprite: Live2DSprite): void {
    if (this.startTs === null) return;
    const elapsed = performance.now() - this.startTs;
    // sin 包络：0 → 峰值 → 0
    const t = elapsed / REACTING_DURATION_MS;
    const envelope = Math.sin(t * Math.PI);  // [0, 1, 0]
    sprite.setParameterValueById("ParamCheek", CHEEK_PEAK * envelope);
    sprite.setParameterValueById("ParamEyeLSmile", EYE_SMILE_PEAK * envelope);
    sprite.setParameterValueById("ParamEyeRSmile", EYE_SMILE_PEAK * envelope);
  }
}
```

**注意**：sin 包络在 `t >= 1` 时 envelope = 0（自然回零），active getter 同时变 false → 下一帧 apply 短路。不需要显式 reset。

### 4.3 `DragReactionSource`（拖拽反馈）

```ts
// frontend/src/pet/sources/DragReactionSource.ts
import type { Live2DSprite } from "easy-live2d";
import type { ParamSource } from "./types";

const RELEASE_DURATION_MS = 300;
const ANGLE_Z_PEAK = 15;
const BODY_ANGLE_Z_PEAK = 8;
const BROW_FORM_PEAK = 0.7;   // 惊讶眉
const BROW_Y_PEAK = 0.5;       // 抬眉
const MOUTH_FORM_PEAK = -0.5;  // 嘴形微张（O 形：Hiyori ParamMouthForm < 0 = 圆口）

export class DragReactionSource implements ParamSource {
  private isDragging = false;
  private dragVelX = 0;        // 拖动方向 X 分量（-1 .. 1）
  private dragVelY = 0;
  private releaseStartTs: number | null = null;  // 松开后回归动画起点

  setDragging(v: boolean, vx = 0, vy = 0): void {
    if (v) {
      this.isDragging = true;
      this.dragVelX = clamp(vx, -1, 1);
      this.dragVelY = clamp(vy, -1, 1);
      this.releaseStartTs = null;
    } else if (this.isDragging) {
      this.isDragging = false;
      this.releaseStartTs = performance.now();
    }
  }

  /** 拖动中由 usePetInteractions / usePixiAvatarSlot 喂"瞬时方向"（normalized）。 */
  updateDragDirection(vx: number, vy: number): void {
    if (!this.isDragging) return;
    this.dragVelX = clamp(vx, -1, 1);
    this.dragVelY = clamp(vy, -1, 1);
  }

  get active(): boolean {
    if (this.isDragging) return true;
    if (this.releaseStartTs === null) return false;
    return performance.now() - this.releaseStartTs < RELEASE_DURATION_MS;
  }

  apply(sprite: Live2DSprite): void {
    let envelope = 1;
    if (!this.isDragging && this.releaseStartTs !== null) {
      const elapsed = performance.now() - this.releaseStartTs;
      envelope = Math.max(0, 1 - elapsed / RELEASE_DURATION_MS);
    }
    // 摇晃（手指拖动方向反向 → 桌宠像被甩起来）
    sprite.setParameterValueById("ParamAngleZ", ANGLE_Z_PEAK * this.dragVelX * envelope);
    sprite.setParameterValueById("ParamBodyAngleZ", BODY_ANGLE_Z_PEAK * this.dragVelX * envelope);
    // 惊讶表情（不分方向，整体上抬）
    sprite.setParameterValueById("ParamBrowLForm", BROW_FORM_PEAK * envelope);
    sprite.setParameterValueById("ParamBrowRForm", BROW_FORM_PEAK * envelope);
    sprite.setParameterValueById("ParamBrowLY", BROW_Y_PEAK * envelope);
    sprite.setParameterValueById("ParamBrowRY", BROW_Y_PEAK * envelope);
    sprite.setParameterValueById("ParamMouthForm", MOUTH_FORM_PEAK * envelope);
  }
}

function clamp(v: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, v));
}
```

**dragVel 喂法**：`usePixiAvatarSlot` 在 `globalpointermove` 内算 dx / dy → 归一化（除以 50px 步长得 [-1, 1] 范围）→ 通过 onDragMove 回调喂给 DragReactionSource：

```ts
// usePixiAvatarSlot.ts globalpointermove 内
if (dragArmed && onSlotDragMove) {
  const stepNorm = 50;  // 50px 像素步长 → 1.0 强度
  onSlotDragMove(
    clamp((slot.x - slotStart.x - lastSlotX) / stepNorm, -1, 1),
    clamp((slot.y - slotStart.y - lastSlotY) / stepNorm, -1, 1),
  );
  lastSlotX = slot.x - slotStart.x;
  lastSlotY = slot.y - slotStart.y;
}
```

也可以简单点：用拖动绝对位移方向（slot 相对 startSlot 的 dx/dy），不每帧算 velocity。本设计选后者：

```ts
// usePixiAvatarSlot.ts globalpointermove 内（最终方案）
if (dragArmed) {
  const totalDx = slot.x - slotStart.x;
  const totalDy = slot.y - slotStart.y;
  const norm = 100;  // 100px 位移 → 满强度
  onSlotDragMove?.(clamp(totalDx / norm, -1, 1), clamp(totalDy / norm, -1, 1));
}
```

更简单、单测可断言。

### 4.4 17a `usePixiAvatarSlot` 改造

**签名扩展**：

```ts
export function usePixiAvatarSlot(
  stageRef: RefObject<HTMLDivElement | null>,
  setSpriteScreen: (s: { x: number; y: number; w: number; h: number } | null) => void,
  setIsDragging: (v: boolean) => void,
  onSlotClick?: (e: PIXI.FederatedPointerEvent) => void,        // 新增
  onSlotDragMove?: (vx: number, vy: number) => void,            // 新增（normalized [-1, 1]）
): UsePixiAvatarSlotHandles
```

**handler 改造**：§3.3 已展开完整代码。三个 callback（`setIsDragging` / `onSlotClick` / `onSlotDragMove`）都是 React state setter 或 useCallback ref，可以传 stale 闭包：handler 里直接读最新 ref（用 `useRefSync` 同步），或直接传不变的 callback（PetApp 用 useCallback wrap）。

**`useEffect` 依赖管理**：onSlotClick / onSlotDragMove 加入 deps。考虑到 useEffect 重建会触发 PIXI Application 重建（开销大），用 ref-sync 模式：

```ts
// PetApp 内
const onSlotClickRef = useRef<typeof onSlotClick>();
useEffect(() => { onSlotClickRef.current = onSlotClick; }, [onSlotClick]);
// 传给 hook 的是 useCallback wrap 的稳定函数：
const stableOnSlotClick = useCallback((e: PIXI.FederatedPointerEvent) => {
  onSlotClickRef.current?.(e);
}, []);
```

让 `usePixiAvatarSlot` 的 useEffect deps 不因 click handler 变化重建。

### 4.5 18 `MouthDriver` 改造

**接口**（不动）：

```ts
export interface MouthDriver {
  attach(sprite: Live2DSprite): void;
  onTextDelta?(text: string): void;
  onAudioFrame?(pcm: Float32Array, sampleRate: number): void;
  detach(): void;
}
```

新增 `ParamSource` 让 `TextCadenceMouthDriver` 也实现（接口 union）：

```ts
import type { ParamSource } from "./sources/types";

export interface MouthDriver extends ParamSource {
  attach(sprite: Live2DSprite): void;
  onTextDelta?(text: string): void;
  onAudioFrame?(pcm: Float32Array, sampleRate: number): void;
  detach(): void;
  // ParamSource 提供：
  //   readonly active: boolean
  //   apply(sprite: Live2DSprite): void
}
```

**`TextCadenceMouthDriver` 内部改造**：

```ts
export class TextCadenceMouthDriver implements MouthDriver {
  private sprite: Live2DSprite | null = null;
  private rafId: number | null = null;
  private queue: string[] = [];
  private currentMouthValue = 0;
  // **移除字段**：origOnRender（不再需要恢复）
  // **移除 dev 日志字段**：setMouthOpenLogged 保留即可

  attach(sprite: Live2DSprite): void {
    this.sprite = sprite;
    // **移除**：原 attach 里 hook sprite.onRender 那一整段（line 70-87）
    // composer 负责 onRender 装载 + 调用 apply
  }

  onTextDelta(text: string): void {
    // 不动
  }

  // 不动：tickQueue / setMouthOpen 内 setParameterValueById 移除？
  // 实际上：保留也无害（composer 后调 apply 会再写一次相同值）
  // 但简化语义：setMouthOpen 只更新 currentMouthValue，不直接 set 参数
  private setMouthOpen(v: number): void {
    this.currentMouthValue = v;
    // **移除**：原 sprite.setParameterValueById（line 147）
    // apply() 会被 composer 每帧调，从 currentMouthValue 写参数
  }

  get active(): boolean {
    return this.sprite !== null;
  }

  apply(sprite: Live2DSprite): void {
    if (this.sprite !== sprite) return;  // safety: attach 后才生效
    sprite.setParameterValueById("ParamMouthOpenY", this.currentMouthValue);
  }

  detach(): void {
    if (this.rafId !== null) {
      cancelAnimationFrame(this.rafId);
      this.rafId = null;
    }
    this.queue = [];
    this.currentMouthValue = 0;
    if (this.sprite) {
      this.sprite.setParameterValueById("ParamMouthOpenY", 0);
    }
    // **移除**：原 detach 里恢复 origOnRender（line 158-162）
    this.sprite = null;
  }
}
```

**18 既有单测怎么保**：

读 `MouthDriver.test.ts` 看断言点（[要在 progress.md 第一步确认]）：
- 如果断言走 attach → onTextDelta → setMouthOpen 数值演进 → 改造后 currentMouthValue 仍正确演进，单测过
- 如果断言走 attach → onTextDelta → sprite.setParameterValueById 被调 → 改造后 setMouthOpen 不再直接调参数，单测**会挂**
- 修复：在 apply() 内调 setParameterValueById，单测加 `driver.apply(mockSprite)` 一步即可，断言 setParameterValueById 被调；不动测试主线

具体改动量在 progress.md 第一步实测后再定。

### 4.6 `composePetRender` 与 `usePetInteractions`

`composePetRender` 已在 §3.2 给出。`usePetInteractions` 是胶水：

```ts
// frontend/src/pages/pet/usePetInteractions.ts
import { useEffect, useMemo, useRef, type RefObject } from "react";
import type { Live2DSprite } from "easy-live2d";
import { listen } from "@tauri-apps/api/event";
import { isTauri } from "@/utils/tauri";
import { composePetRender } from "@/pet/composePetRender";
import { GazeSource } from "@/pet/sources/GazeSource";
import { TapReactionSource } from "@/pet/sources/TapReactionSource";
import { DragReactionSource } from "@/pet/sources/DragReactionSource";
import type { TextCadenceMouthDriver } from "@/pet/MouthDriver";
import { usePetStateStore } from "@/stores/petState";

export interface PetInteractions {
  /** PIXI slot 派来的 click（已经经 click vs drag 区分）。 */
  onSlotClick: () => void;
  /** PIXI slot 派来的 drag 方向（归一化）。 */
  onSlotDragMove: (vx: number, vy: number) => void;
}

export function usePetInteractions(
  spriteRef: RefObject<Live2DSprite | null>,
  spriteScreen: { x: number; y: number; w: number; h: number } | null,
  isDragging: boolean,
  mouthDriver: TextCadenceMouthDriver,  // 18 既有；让 composer 拿到
): PetInteractions {
  const gaze = useMemo(() => new GazeSource(), []);
  const tap = useMemo(() => new TapReactionSource(), []);
  const drag = useMemo(() => new DragReactionSource(), []);

  // isDragging → drag.setDragging + gaze.setActive 互斥
  useEffect(() => {
    drag.setDragging(isDragging);
    gaze.setActive(!isDragging);
  }, [isDragging, drag, gaze]);

  // composer 装载（等 sprite ready）
  useEffect(() => {
    const sprite = spriteRef.current;
    if (!sprite) return;
    const detach = composePetRender(sprite, [gaze, tap, drag, mouthDriver]);
    return detach;
  }, [spriteRef, gaze, tap, drag, mouthDriver]);

  // cursor channel 副订阅
  useEffect(() => {
    if (!isTauri() || !spriteScreen) return;
    let cancelled = false;
    let unlisten: (() => void) | null = null;
    void listen<{ x: number; y: number }>("pet://cursor", (e) => {
      gaze.updateCursor(e.payload.x, e.payload.y, spriteScreen);
    }).then((u) => {
      if (cancelled) safeUnlisten(u);
      else unlisten = u;
    });
    return () => {
      cancelled = true;
      safeUnlisten(unlisten);
    };
  }, [spriteScreen, gaze]);

  const onSlotClick = useCallback(() => {
    const phase = usePetStateStore.getState().phase;
    if (phase === "speaking") return;
    tap.fire();
    void spriteRef.current?.startMotion({
      group: PET_LIVE2D_CONFIG.motionGroups.tap ?? "TapBody",
      no: 0,
      priority: Priority.Normal,
    });
  }, [spriteRef, tap]);

  const onSlotDragMove = useCallback((vx: number, vy: number) => {
    drag.updateDragDirection(vx, vy);
  }, [drag]);

  return { onSlotClick, onSlotDragMove };
}

function safeUnlisten(fn: (() => void) | null | undefined): void {
  if (!fn) return;
  try { fn(); } catch { /* stale-cleanup race */ }
}
```

### 4.7 `pet/App.tsx` 改造（入口胶水）

PetApp 改动小：

```tsx
// PetApp 内（节选）
const { spriteRef } = usePetLive2D(slotRef, app, invalidateAnchor);
const driver = useMemo(() => new TextCadenceMouthDriver(), []);

// 18 既有 attach/detach（保留，driver 内部不再 hook onRender）
useEffect(() => {
  let prevPhase: PetPhase = usePetStateStore.getState().phase;
  const unsub = usePetStateStore.subscribe((state) => {
    const nextPhase = state.phase;
    if (nextPhase === prevPhase) return;
    const sprite = spriteRef.current;
    if (prevPhase !== "speaking" && nextPhase === "speaking" && sprite) {
      driver.attach(sprite);
    } else if (prevPhase === "speaking" && nextPhase !== "speaking") {
      driver.detach();
    }
    prevPhase = nextPhase;
  });
  return () => {
    unsub();
    driver.detach();
  };
}, [driver, spriteRef]);

// 18 既有 listen<PushEnvelope> 转发 text_delta（不动）

// 新增：装 interactions（composer + 3 source + cursor channel 副订阅）
const { onSlotClick, onSlotDragMove } = usePetInteractions(
  spriteRef,
  spriteScreen,
  isDragging,
  driver,
);

// usePixiAvatarSlot 多传 onSlotClick / onSlotDragMove
const { slotRef, app, invalidateAnchor, alphaScanGivenUpRef } = usePixiAvatarSlot(
  stageRef, setSpriteScreen, setIsDragging,
  onSlotClick, onSlotDragMove,
);
```

**18 既有"状态机 → motion 派发" useEffect**（App.tsx line 214-224）保留不动——`motionGroups.thinking / speaking / error` 仍是 null，sprite.startMotion 仍只在 idle 时被 PetLive2D 启动。tap motion 由 `usePetInteractions` 派发，跟状态机 motion 派发独立路径。

---

## 5. 参数选择与初值表

集中列出本期所有"拍脑袋初值"，便于实现期 progress.md 调整时一处汇总：

| 模块 | 常量 | 初值 | 含义 / 调整方向 |
| --- | --- | --- | --- |
| `usePixiAvatarSlot` | `DRAG_MOVE_THRESHOLD_PX` | 5 | click vs drag 区分阈值；过小误判鼠标抖动为 drag |
| `usePixiAvatarSlot` | `CLICK_MAX_DURATION_MS` | 300 | 短按时长上限；超此时长即使没动也算长按（非 click） |
| `GazeSource` | `DISTANCE_THRESHOLD_FRAC` | 0.25 | 阈值距离 = max(viewportW, viewportH) × 此值；超出归零 |
| `GazeSource` | `ANGLE_X_MAX_DEG` / `ANGLE_Y_MAX_DEG` | 30 | 头部最大偏转角；过大显得"歪脖子"，过小看不出跟随 |
| `GazeSource` | `ANGLE_Z_MAX_DEG` | 10 | 头部 Z 轴最大偏转；过大显得头要掉了 |
| `GazeSource` | `EYEBALL_MAX` | 1.0 | 眼球最大偏转（Live2D 范围 [-1, 1]） |
| `GazeSource` | `EMA_TAU_MS` | 200 | 一阶 EMA 时间常数；过小卡顿，过大滞后 |
| `TapReactionSource` | `REACTING_DURATION_MS` | 800 | reacting 持续时长；过短反应表情来不及看到，过长干扰下一轮交互 |
| `TapReactionSource` | `CHEEK_PEAK` | 0.7 | 脸颊红峰值（参数范围一般 [0, 1]） |
| `TapReactionSource` | `EYE_SMILE_PEAK` | 0.8 | 笑眼峰值 |
| `DragReactionSource` | `RELEASE_DURATION_MS` | 300 | mouse up 后参数线性回归时长 |
| `DragReactionSource` | `ANGLE_Z_PEAK` | 15 | 拖拽 Z 摇晃峰值（度） |
| `DragReactionSource` | `BODY_ANGLE_Z_PEAK` | 8 | 身体扭动峰值（度） |
| `DragReactionSource` | `BROW_FORM_PEAK` | 0.7 | 惊讶眉峰值 |
| `DragReactionSource` | `BROW_Y_PEAK` | 0.5 | 抬眉峰值 |
| `DragReactionSource` | `MOUTH_FORM_PEAK` | -0.5 | 嘴形微张（Hiyori 负值 = 圆口） |

**初值调整流程**（沿 [requirement §1.3 / §4.4.3]）：实现期主观体感不对 → 改 design.md 同款常量 + progress.md 实现日志登记 → 不动 AC、不动 requirement.md。

---

## 6. 测试策略

### 6.1 前端单测（vitest）

新增：

- `frontend/src/pet/sources/GazeSource.test.ts`：
  - `updateCursor` 给定 cursor + spriteScreen → 验 `target.angleX/Y/Z + eyeBallX/Y` 计算正确（取 [中心 / 边缘 / 阈值外] 三个 cursor 位置）
  - `apply` 多次调用 → 验 `current` EMA 收敛到 `target`（先 set target 再连调 N 次 apply，验 |current - target| < ε）
  - `setActive(false)` → `active === false`，composer 跳过
- `frontend/src/pet/sources/TapReactionSource.test.ts`：
  - `fire()` → `active === true` + 0~REACTING_DURATION_MS 内 sin 包络正确（mock performance.now）
  - reacting 期间再 fire → 忽略（active 不重置）
  - REACTING_DURATION_MS 后 → `active === false`
- `frontend/src/pet/sources/DragReactionSource.test.ts`：
  - `setDragging(true, 0.5, 0)` → `active === true` + apply 时 ParamAngleZ 按 dragVelX 算
  - `setDragging(false)` → 进入 release 阶段，envelope 线性衰减
  - RELEASE_DURATION_MS 后 → `active === false`
- `frontend/src/pet/composePetRender.test.ts`：
  - 多 source 顺序 → mock sprite.onRender，验 origOnRender 先调 + 各 source.apply 按数组顺序调
  - source.active = false → apply 不被调
  - detach 函数 → 恢复 origOnRender

改造既有：

- `frontend/src/pet/MouthDriver.test.ts`：
  - 既有 `attach → onTextDelta → currentMouthValue 演进` 断言**不动**（演进逻辑没改）
  - 既有 `attach 后 sprite.setParameterValueById 被调` 断言可能需要改成"调用 apply(sprite) 后 setParameterValueById 被调"——具体看现行断言写法（progress.md 第一项做改造时确认）

新增（17a `usePixiAvatarSlot` 改造）：

- `frontend/src/pages/pet/usePixiAvatarSlot.test.ts`（如不存在则新建）：
  - mock PIXI 事件流 `pointerdown → pointerup`（短按短移 < 5px / < 300ms）→ 验 `onSlotClick` 被调、`setIsDragging` 没被调
  - mock `pointerdown → globalpointermove(>5px) → pointerup`（drag）→ 验 `setIsDragging` 被调、`onSlotClick` 没被调
  - 短按 + 在阈值时间外 mouseup → 验既不是 click 也不是 drag（noop）

### 6.2 手动真跑端到端

macOS + Win 双端：

- **AC-2 视线跟随**：开 PetOverlay → 移光标从屏一角扫到对角 → 观察头部 + 眼球跟随；停在桌宠中央 → 头回中立；移到屏幕远角（超阈值）→ 头眼归零。dev console 看 ParamAngle 系列参数滑动无阶跃（dev 加 log 印 GazeSource.current 即可，verification 时打开）。
- **AC-3 点击 motion 触发（降级路径）**：在桌宠任意位置短按 → m04 motion 触发（手摆动）+ 脸颊红 / 笑眼 800ms。motion 进行中再点 → 不重复（active getter 拦）。
- **AC-4 拖拽视觉反馈**：在桌宠上 mouse down + 拖动 → Live2D ParamAngleZ 摇晃 + 惊讶眉 + 微张嘴；松开 → 300ms 内参数线性回零。桌宠位置确实跟着移动到松开点（17a 既有行为）。
- **AC-5 状态冲突**：
  - dev CLI `./scripts/dev-fire-source/run.sh cron:bedtime` → 触发 speaking 态 → 此时点击桌宠 → tap motion 不触发（click handler return）；视线跟随仍在；speaking 结束后 tap 重新可用
  - 拖拽中视线跟随暂关（用 dev 加 log 看 `gaze.active === false` during drag）
  - error 态（手动 raise）→ 各 source 不崩
- **AC-6 既有路径回归**：跑 015 / 016 / 17a / 18 全 AC list 对照过一遍（具体清单已在 requirement.md）
- **AC-7 跨平台**：macOS + Win 双端同 list；不接受 fallback
- **AC-8 cross-build**：`./scripts/check` + `pnpm test` + `cargo build`

### 6.3 dev observability（实现期辅助验证，不进 AC）

PetApp 加 dev-only log：

```ts
if (import.meta.env.DEV) {
  // gaze 每秒打一次当前 current 值
  // tap fire 时打一行
  // drag setDragging(true/false) 各打一行
}
```

跟 18 既有 dev observability 同款（App.tsx line 134-168）。

---

## 7. 影响分析

### 7.1 上下游影响

| 方向 | 影响 |
| --- | --- |
| **上游：Rust 端** | 不动。`pet://cursor` 通道 / `update_sprite_pos` invoke / push_subscriber 不动。 |
| **上游：015 push 通道** | 不动。 |
| **上游：016 bubble window** | 不动。drag 时 emitSpritePos 仍 60Hz → Rust → bubble.follow_loop（17a 既有路径）。 |
| **同期：17a / 18 既有模块** | 改造点仅 2 个（`usePixiAvatarSlot` 加 click 区分 + `MouthDriver` 内部去掉独立 hook）。接口签名 / 单测断言 / AC 行为全维持。 |
| **下游：019 ActionBar** | 不动。drag 中 isDragging 仍 true，sticky 显示路径不变。 |
| **下游：022 IM 接入** | 不动。 |

### 7.2 风险点

| # | 风险 | 缓解 |
| --- | --- | --- |
| 1 | `composePetRender` 多 source 写 ParamAngleZ 冲突（gaze + drag 都写） | 调用方在 `usePetInteractions` 用 `drag.active` 短路 gaze；composer 内顺序保证 drag 后写覆盖 gaze 也无碍（gaze 在 drag 时已 inactive） |
| 2 | `MouthDriver` 内部改造碰 18 既有单测断言 | progress.md 第一步先读 `MouthDriver.test.ts`，断言走 currentMouthValue 演进的不动，断言走 setParameterValueById 被调的改成"apply 后被调"。修改幅度可控（< 20 行测试代码） |
| 3 | 17a `usePixiAvatarSlot` 改 drag 区分误判（5px 阈值过严 / 300ms 过紧） | 阈值表 §5 列入；progress.md 实现期实测 + 调整。本期 AC-4 行为零退化是验"超阈值就是 drag"，不验"小幅鼠标抖动不误判"——实测有误判再调阈值 |
| 4 | `pet://cursor` 60Hz 副订阅 + 5 个 source 每帧 apply → 60Hz × 5 setParameterValueById | 实测开销极小（每次 setParameterValueById 是 JS 引擎内查表 + 数值赋值，每帧总开销 < 0.1ms）。macOS NSPanel 30fps cap 下进一步减半。如实际 FPS 掉了再调（不太可能） |
| 5 | error 态期间 sprite 可能 null / Live2D 未加载 | 各 source `active` getter 仅判内部状态，apply 内部不做 sprite-null check（attach 后才会被调；attach 是 PetApp 等 sprite ready 才做）。composer 在 sprite=null 时不装载。安全边界由 useEffect 依赖 spriteRef.current 保证 |
| 6 | EMA τ=200ms 在 60Hz 下大约 12 帧到稳态 80% → 用户主观感受"滞后" | progress.md 实现期主观体感调整 τ（150~250 区间） |
| 7 | tap 期间的 ParamCheek/EyeSmile 与未来若加 thinking motion 冲突 | 18 `motionGroups.thinking = null`，本期不变。tap reaction 用的是表情参数，跟 motion 系统正交，不冲突 |
| 8 | macOS NSPanel 30fps cap 下视线跟随感官偏卡 | 沿 ADR 0004 §4.2 接受；产品验证如不可接受立 0004 §6 反转条件 5 专项 spike |

### 7.3 跨平台行为

- macOS NSPanel + Win 整屏 alwaysOnTop（17a 既有 cfg-gate），本期不新加 cfg-gate
- `pet://cursor` 通道在 macOS + Win 双端语义一致（Rust 端统一 logical CSS px）
- PIXI 事件 `pointerdown / globalpointermove / pointerup` macOS + Win 同 PIXI 实现
- Live2D 参数写入完全 GPU 无关（CPU 端 set 参数 → Live2D SDK 内部 → 下一帧渲染）
- macOS 30fps cap 下 EMA τ 实际效果不变（公式按 dt 算 alpha，dt=33ms 时 alpha 略大但稳态相同）

---

## 8. 变更记录

| 日期 | 变更内容 | 影响范围 |
|------|---------|---------|
| 2026-06-19 | 立项初稿。Hiyori audit 在本设计阶段直接完成：HitArea 只 1 个 Body → 触发 [requirement §4.5.3](./requirement.md#45-day-1-hiyori-资源-audit--降级路径) 降级（统一 motion = `TapBody`/m04）；无 `.exp3` 文件但参数列表丰富（64+），表情切换改路径为参数直写。`MouthDriver` 内部改造（去独立 hook → 暴露 apply 方法）+ 17a `usePixiAvatarSlot` 改造（click vs drag 区分）由用户授权"技术方面自决",不破坏既有接口 / 调用方 / 单测主断言 / AC 行为；不改 17a / 18 既有文档。视线跟随 / tap 反应 / 拖拽反馈三件事走"统一 `composePetRender` 中央 hook + 4 个 ParamSource"统一架构。 | 全文档初稿 |

---

## 文档元信息

- **状态**：已确认（Confirmed）
- **创建时间**：2026-06-19
- **确认时间**：2026-06-19
- **关联探索**：[exploration · pet-liveliness-and-proactive-events](../../explorations/pet-liveliness-and-proactive-events/README.md)
- **关联决策**：[ADR 0004 · 桌宠承载形态与渲染路径](../../decisions/0004-pet-overlay-route/README.md)
- **关联需求**：
  - [需求 017 / 17a](../017-pet-overlay-form-switch/)（avatar-slot Container 底座 + 17a drag handler 改造起点）
  - [需求 018](../018-pet-live2d-state-and-lipsync/)（Live2D 形象 + 4 态机 + `MouthDriver` 改造起点）
- **下一步**：本文档确认后写 `progress.md` + 进入 Phase 3 实现
