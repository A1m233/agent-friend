# 018 · 桌宠 Live2D 接入 + 状态机 + Codex 兼容 + lip-sync (17b) — 技术方案

## 状态

<!-- 草稿（Draft） | 已确认（Confirmed） -->
已确认（Confirmed）

## 需求文档

→ [requirement.md](./requirement.md)

---

## 1. 设计目标回顾

按 [requirement.md](./requirement.md) §1.2 / §2 In Scope，本期在 [17a](../017-pet-overlay-form-switch/design.md) 落地的整屏 overlay + PIXI canvas + avatar-slot Container 底座之上，完成 5 件事：

1. **17a 接缝点 #1 落地**：avatar-slot Container 内 children 由 `Graphics + Text` 占位换为 `easy-live2d.Live2DSprite`，外层 plumbing 不动。
2. **17a 接缝点 #4 落地**：slot 上挂 4 态状态机（idle / thinking / speaking / error），由 push event flow 驱动。
3. **Codex 兼容**：让 pet 窗也能收 push envelope（当前只发 bubble 窗）+ 独立 policy 投影成状态切换。
4. **lip-sync**：抽象 `MouthDriver` 接口 + 实现 `TextCadenceMouthDriver`；speaking 态生命周期内嘴部参数被驱动；voice 通道接入扩展位预留。
5. **17a Win 真机 12 AC 补回归**：17a progress.md M17.9 留下来的 Win 端真机回归在 17b 实施期跑一遍。

17a 接缝点 #2 / #3 / #5（sprite world position 数据流 / cursor alpha hit-test / 操作栏 hover bridge）保持现状，作为本期"零退化" AC 验证项。

---

## 2. 整体改动地图

| 文件路径 | 改动类型 | 说明 |
|---|---|---|
| `frontend/src-tauri/src/push_subscriber.rs` | 一行增 | line 83 `emit_to("bubble", ...)` 之后再 `emit_to("pet", ...)`；015 store / policy / subscriber 启动方式 / sessionProjection 不动 |
| `frontend/package.json` | 加依赖 | Live2D 库（实施期 M18.1 pin 具体 fork + 版本号；候选见 §3.3）|
| `frontend/public/live2dcubismcore.min.js` | 新增 | Cubism SDK Core JS（Live2D 官方 redist；放 public 原样 ship）|
| `frontend/public/live2d-models/hiyori/` | 新增目录 | Hiyori 模型多文件（`.model3.json` / `.moc3` / `.physics3.json` / textures / motions / expressions），原样 ship |
| `frontend/pet.html` | 加 script | `<head>` 内加 `<script src="/live2dcubismcore.min.js"></script>` 在 module entry 之前 |
| `frontend/src/pet/live2dConfig.ts` | 新建 | 顶层配置常量（模型名 + 模型路径 + 默认 motion / expression 命名 + 缩放策略），换模型只改这一文件 |
| `frontend/src/pet/usePetLive2D.ts` | 新建 hook | 接 17a `usePixiAvatarSlot` 输出的 slot ref + app，`new Live2DSprite() + sprite.modelPath = ... + await sprite.ready` 加载 → `slot.removeChildren()` + `slot.addChild(sprite)` → 暴露 spriteRef 给状态机 / driver |
| `frontend/src/pages/pet/usePixiAvatarSlot.ts` | 改造 | 暴露 slot ref（hook 返回值，不再 internal-only），让 `usePetLive2D` 能接到；占位 Graphics + Text 改为"加载中临时显示" 由 `usePetLive2D` mount 完成后清掉 |
| `frontend/src/stores/petState.ts` | 新建 | `usePetStateStore`（phase + transition）+ `startPetStateSubscriber()`；与 015 `petBubble.ts` 双件对称 |
| `frontend/src/stores/petStatePolicy.ts` | 新建 | `PushPolicy → PetStateTransition` 投影；与 015 `petBubblePolicy.ts` 对称 |
| `frontend/src/stores/petState.test.ts` | 新建 | store + subscriber 单测 |
| `frontend/src/stores/petStatePolicy.test.ts` | 新建 | policy transition table 单测 |
| `frontend/src/pet/MouthDriver.ts` | 新建 | 接口定义 + `TextCadenceMouthDriver` 实现 |
| `frontend/src/pet/MouthDriver.test.ts` | 新建 | `TextCadenceMouthDriver` text_delta 时序 → mouthOpenY 时序单测 |
| `frontend/src/pages/pet/App.tsx` | 改造 | mount 时启 `startPetStateSubscriber()`；订 `usePetStateStore` phase → enter/exit hook → driver attach/detach + Live2DSprite motion / expression 派发 |

**不动**（重点强调）：

- 015 全文件 + `bubble/App.tsx` + `petBubble.ts` / `petBubblePolicy.ts`
- 016 `bubble_window.rs` + `PetBubble` 组件 + bubble window 显隐逻辑
- 17a `ActionBar.tsx` / `usePetPassthrough.ts` / `slotBoundsHit.ts` / `computeActionBarPosition.ts`
- 17a `update_sprite_pos` invoke + `BubbleState.sprite_pos` + `run_follow_loop`
- 17a NSPanel / 整屏 overlay setup hook

---

## 3. 架构决策

### 3.1 push event 多窗分发 · Rust `emit_to` 叠加

**问题**：015 现状 `push_subscriber.rs:83` 只 `app.emit_to("bubble", "agent://push", env)`，pet 窗收不到 envelope。17b 状态机驱动需要 pet 窗能收 push event。

**方案选型**：

| 方案 | 行为 | 取舍 |
|---|---|---|
| A · 多发一份 `emit_to("pet", ...)` | 单次 SSE 解码 / 多次 emit 到目标窗 | 改动最小（一行）；015 对 bubble 窗的 contract 完全不变；未来加 voice 窗也是叠加一行 |
| B · 改用 `emit()` 广播 | 一次 emit 全窗都收得到 | 015 测试 / 调用方语义有变；侵入面更大 |
| C · pet 窗用 `invoke` 拉取 | pet 窗向 Rust 主动订阅 | 模式不一致，原 push 是 push 模型 |

**选 A**。`push_subscriber.rs` line 83 之后追加：

```rust
if let Err(e) = app.emit_to("pet", "agent://push", env) {
    log::warn!("emit_to(pet) failed: {e}");
}
```

> **影响**：严格意义违反 requirement.md §4.3.5 "015 完全不动" 措辞。如实表述：015 模块代码中**仅 `push_subscriber.rs` emit target 增加 pet 窗一份**；015 store / policy / subscriber 启动方式 / sessionProjection / 测试不动；015 全 9 AC 仍然成立。requirement.md §8 变更记录补一行。

### 3.2 状态机最小集 + transition table

**store 形态**（沿 015 `petBubble.ts` 同款 `create<…>(...)`）：

```ts
export type PetPhase = "idle" | "thinking" | "speaking" | "error";

interface PetStateState {
  phase: PetPhase;
  /** envelope.events 流入：policy 决策 → 切态。 */
  ingest: (env: PushEnvelope) => void;
  /** SSE / 通道错误进 error；外部由 subscriber 调。 */
  raiseError: () => void;
  /** 用户操作 / 下一个有效 envelope 自动恢复。 */
  reset: () => void;
}
```

**transition table**（`petStatePolicy.ts` defaultPolicy 落地）：

| 当前态 | event 类型 | 下一态 | 说明 |
|---|---|---|---|
| any | envelope.events 含 `tool_call_request` 或 `tool_call_result` | `thinking` | 内部工具调用阶段 |
| any（含 thinking）| envelope.events 含 `text_delta` | `speaking` | 开始向用户输出文本 |
| `speaking` | envelope.events 含 `done` | `idle`（300ms 短延迟）| 留窗口给 lip-sync 收尾 |
| `thinking` | envelope.events 含 `done` 但**无 text_delta**（silent turn） | `idle` | IdleReflectionSource 等无输出轮直跳过 speaking |
| any | SSE 断连 / 解析失败 | `error` | 由 subscriber 显式调 `raiseError()` |
| `error` | 下一个有效 envelope 到达 | `idle` | 自动恢复，envelope 本身再走上述表 |

**policy 决策**：单帧 envelope 内**多 event 类型同帧**时按优先级 `text_delta > tool_call_* > done`（一帧里有 text_delta 优先 speaking，避免短暂闪进 thinking 又跳 speaking）。

**store 之外**：

- `usePetStateStore` 与 `usePetBubbleStore` 完全独立、互不订阅、互不写对方（FSM 投影 vs 内容路由，正交关注点）
- `petStatePolicy` 沿 015 同款 module-level 变量注入模式（`setPolicy(p)` / `resetPolicy()` 测试钩子）
- 订阅源 `startPetStateSubscriber()` 自己 `listen("agent://push", ...)`（pet 窗收到的 envelope 来自 §3.1 emit_to("pet")）；不分叉 015 现有 subscriber

### 3.3 Live2D 库选型 · pin `easy-live2d@0.4.4`

**M18.1 探查结果（撞墙落入 fallback 2）**：

公开 npm 上**没有 PIXI v8 兼容的 `pixi-live2d-display` fork**：
- `pixi-live2d-display` 主仓 v0.5.0-beta：peerDeps `@pixi/* ^6`（PIXI v6 only）
- `pixi-live2d-display-mulmotion` v0.5.0-mm-6：peerDeps `pixi.js ^7`（PIXI v7 only）
- `pixi-live2d-display-lipsyncpatch` v0.5.0-ls-8：peerDeps `pixi.js ^7`（PIXI v7 only）
- `pixi-live2d-display-advanced` v1.1.0：peerDeps `pixi.js ^7`（PIXI v7 only）

`pixi-live2d-display` 路径在 PIXI v8 不存在公开包 → 触发 [requirement.md](./requirement.md) §1.2 "撞墙 fallback" 路径；从 declare 阶段倾向的 "fork 主选" 跨过 fallback 1（其他 PIXI v8 兼容 fork 不存在）直接落 **fallback 2 · `easy-live2d`**（用户 2026-06-15 拍板）。

**pin 版本**：`easy-live2d@^0.4.4`（依 `@pixi/sound@^6` 间接绑 `pixi.js@^8.0.0`，与 17a 既有 `pixi.js@^8.6.0` 直接兼容；MPL-2.0 license；panzer-jack 维护）。

**核心 API 形态**（与 declare 阶段假设的 `pixi-live2d-display.Live2DModel.from(...)` 不同；改为 `Live2DSprite`）：

```ts
import { Application, Ticker } from "pixi.js";
import { Config, Live2DSprite, Priority, LogLevel } from "easy-live2d";

// 全局 Config（在 app 启动期设置一次）
Config.MotionGroupIdle = "Idle";
Config.MouseFollow = false;          // 17b 不用 mouse-follow，跟随由桌宠状态机驱动
Config.CubismLoggingLevel = LogLevel.LogLevel_Warning;

// 加载模型
const sprite = new Live2DSprite();
sprite.modelPath = "/live2d-models/hiyori/Hiyori.model3.json";
sprite.ticker = app.ticker;
sprite.renderer = app.renderer;
sprite.setSize({ width: 320, height: 320 });
slot.addChild(sprite);
await sprite.ready;

// 播放 motion / 表情
await sprite.startMotion({ group: "Idle", no: 0, priority: Priority.Idle });
sprite.setExpression({ name: "..." });

// 设 mouth 参数（lip-sync 用）
sprite.setParameterValueById("ParamMouthOpenY", 0.8);
```

**进一步 fallback 路径（如 easy-live2d 实施期撞墙）**：

| Fallback | 触发条件 | 切换成本 |
|---|---|---|
| 3. 直接基于 [Live2D Cubism Web Samples](https://github.com/Live2D/CubismWebSamples) 用 Cubism SDK Web 自渲染 | easy-live2d 加载 / API 不兼容 | 高（需手写 PIXI mesh + Cubism core 绑定） |
| 4. 降 `pixi.js` 到 v7 + 用 `pixi-live2d-display-mulmotion` | fallback 3 也死路 | 极高（动 17a baseline + 47 tests 回归） |

撞墙时按 declare 阶段确认的"现场切"，design / requirement 不重新走 declare。

### 3.4 Live2D 模型 ship 策略 · public + script tag

**模型选**：Hiyori（Live2D Inc 官方 sample，Cubism 4 spec 标准，文件量适中 ~3MB）。

> license 注：Live2D Inc 官方 sample model 的 free material license 允许非商业使用 + 部分商业有条件使用。实施期 M18.1 把 license 文件随模型一起 ship（`frontend/public/live2d-models/hiyori/LICENSE.md`），并在 `docs/decisions/` 评估是否单独立 ADR 锁定 Live2D model license 路径。本期 design 默认按 free material 路径走。

**目录约定**：

```
frontend/public/
├── live2dcubismcore.min.js              # Cubism SDK Core (Live2D Proprietary Software License, 207KB)
└── live2d-models/
    ├── CUBISM_CORE_LICENSE.md           # Cubism Core license
    └── hiyori/                          # Hiyori sample model (Live2D Open Software License, ~4.7MB)
        ├── LICENSE.md
        ├── Hiyori.model3.json
        ├── Hiyori.moc3
        ├── Hiyori.physics3.json
        ├── Hiyori.pose3.json
        ├── Hiyori.cdi3.json
        ├── Hiyori.userdata3.json
        ├── Hiyori.2048/
        │   ├── texture_00.png
        │   └── texture_01.png
        └── motions/
            ├── Hiyori_m01.motion3.json
            └── ... (11 个 motion)
```

**注：实际文件名首字母大写**（来自 CubismWebSamples 仓库），与 declare 阶段假设的 `hiyori.model3.json`（小写）不同。`live2dConfig.modelPath` 按实际文件名落 `/live2d-models/hiyori/Hiyori.model3.json`。

**`pet.html` 改造**（核心 JS 挂载）：

```html
<head>
  <!-- 在 module entry 之前加载，确保 PIXI Live2D 库能找到全局 Live2DCubismCore -->
  <script src="/live2dcubismcore.min.js"></script>
  ...
  <script type="module" src="/src/pages/pet/main.tsx"></script>
</head>
```

**为什么 public + script tag**（不走 Vite plugin pre-bundle）：

- Cubism Core JS 是 Live2D 官方 redist，license 要求保持原样 ship，不能被 bundler 重打包
- public 目录原样 copy 到 dist，运行期路径稳定（`/live2dcubismcore.min.js`）
- script tag 在 module entry 之前执行，`window.Live2DCubismCore` 全局可用，PIXI Live2D 库初始化时能直接读
- 多文件模型资源（.moc3 + textures + motions）也走 public，避免 Vite asset hash 把相对路径打散

**`live2dConfig.ts` 配置常量**（换模型只改这一文件）：

```ts
export const PET_LIVE2D_CONFIG = {
  modelName: "hiyori",
  modelPath: "/live2d-models/hiyori/Hiyori.model3.json",
  // 进入态时优先派发的 motion group 名（按 model3.json motions 字段实际给）；
  // Hiyori 模型的 motion group 名按其 model3.json `Motions` 字段实际填——
  // 实施期 M18.6 读 Hiyori.model3.json 确认（看到的是 "Idle" group 还是其他）
  motionGroups: {
    idle: "Idle",
    thinking: null,        // 暂不切，沿用 idle
    speaking: null,        // 暂不切，由 lip-sync 驱嘴
    error: null,           // 暂不切（缺失模型表情不强求）
  },
  // 模型 sprite 在 slot 内的渲染尺寸（设 sprite.setSize({...})）
  spriteSize: { width: 320, height: 320 },
} as const;
```

未来换模型流程：
1. 把新模型文件夹拷到 `frontend/public/live2d-models/<new-name>/`
2. 改 `live2dConfig.ts` `modelName` / `modelPath` 一行
3. 必要时调 `motionGroups` 映射到新模型 motion 命名
4. dev 重启

### 3.5 MouthDriver 接口设计

**接口签名**：

```ts
import type { Live2DSprite } from "easy-live2d";

export interface MouthDriver {
  /** 由状态机在 enter speaking 态时调；driver 拿到 sprite handle 准备驱动 mouth 参数。 */
  attach(sprite: Live2DSprite): void;

  /** 文本类 driver 实现；speaking 态期间从 push event 转发过来的 text_delta。 */
  onTextDelta?(text: string): void;

  /** 音频类 driver 未来实现（17c 接 voice_bridge）；speaking 态期间 PCM 帧。 */
  onAudioFrame?(pcm: Float32Array, sampleRate: number): void;

  /** 由状态机在 exit speaking 态时调；driver 把 mouth 参数回零 + 释放内部 timer / 资源。 */
  detach(): void;
}
```

**`TextCadenceMouthDriver` 实现**（本期落）：

最简算法（实施期可调）：

- 每条 `text_delta.text` 到达 → 计算"按字 / token 的估算时长"（默认按字数 × 80ms，可配）
- 在该时长内**驱动 `ParamMouthOpenY` 从 0 → 0.8 → 0 sin 波**（Live2D 标准参数 ID，Cubism 4 模型通用；easy-live2d 暴露 `sprite.setParameterValueById(id, value)`）
- 多条 text_delta 重叠时简单串行（前一段结束才开始下一段；首期不做 audio crossfade）
- detach 时立即把 `ParamMouthOpenY` 写 0、清 pending timer

```ts
export class TextCadenceMouthDriver implements MouthDriver {
  private sprite: Live2DSprite | null = null;
  private currentTimer: number | null = null;
  private queue: string[] = [];

  attach(sprite: Live2DSprite): void {
    this.sprite = sprite;
  }

  onTextDelta(text: string): void {
    if (!this.sprite) return;
    this.queue.push(text);
    if (this.currentTimer === null) this.tickQueue();
  }

  private tickQueue(): void {
    const next = this.queue.shift();
    if (!next || !this.sprite) { this.currentTimer = null; return; }
    const durationMs = Math.max(100, next.length * 80);
    const startTs = performance.now();
    const step = () => {
      if (!this.sprite) return;
      const elapsed = performance.now() - startTs;
      if (elapsed >= durationMs) {
        this.setMouthOpen(0);
        this.currentTimer = null;
        this.tickQueue();
        return;
      }
      const t = elapsed / durationMs;
      const open = Math.sin(t * Math.PI) * 0.8;
      this.setMouthOpen(open);
      this.currentTimer = requestAnimationFrame(step);
    };
    this.currentTimer = requestAnimationFrame(step);
  }

  private setMouthOpen(v: number): void {
    this.sprite?.setParameterValueById("ParamMouthOpenY", v);
  }

  detach(): void {
    if (this.currentTimer !== null) {
      cancelAnimationFrame(this.currentTimer);
      this.currentTimer = null;
    }
    this.queue = [];
    if (this.sprite) this.setMouthOpen(0);
    this.sprite = null;
  }
}
```

**`AudioRmsMouthDriver` 占位**（17c 立项时实现，本期**只留接口签名 + 一个 stub 类**作为扩展位证明）：

```ts
/** 17c · 未来接 voice_bridge audio out / 火山 RTC PCM 流时实现。本期为 stub。 */
export class AudioRmsMouthDriver implements MouthDriver {
  attach(_sprite: Live2DSprite): void { /* TODO 17c */ }
  onAudioFrame(_pcm: Float32Array, _sampleRate: number): void { /* TODO 17c */ }
  detach(): void { /* TODO 17c */ }
}
```

**driver 切换钩子**：本期单一 driver（`TextCadenceMouthDriver`）；未来可在 `petState` 状态机入口加 driver factory：

```ts
// 17c 接 voice 时改这里：speaking 时根据 envelope.source_kind / 通道选 driver
function pickMouthDriver(_env: PushEnvelope): MouthDriver {
  return new TextCadenceMouthDriver();
}
```

### 3.6 17a 接缝点 #2 / #3 / #5 plumbing 维持

| 接缝点 | 17a 现状 | 17b 处理 |
|---|---|---|
| #2 sprite world position 数据流 | drag → `pointermove`/`pointerup` invoke `update_sprite_pos`（17a `usePixiAvatarSlot.ts` line 110~123）| **不动**；slot.x / slot.y 由 17b motion 系统更新时仍走 `emitSpritePos` 把世界坐标上报给 Rust（既有 plumbing 兜住） |
| #3 cursor alpha hit-test target | `usePetPassthrough` 60Hz Rust cursor channel + alpha 采样 / `slotBoundsHit` 兜底 | **不动**；alpha 采样目标仍是 avatar-slot 区域；Live2D 内部 alpha 透明区由 SDK 渲染、与 slot bounds 重合区域被 alpha hit-test 自动覆盖 |
| #5 操作栏 hover bridge | `usePetPassthrough` 驱动 `cursorOnSprite` → `App.tsx` sticky 防抖 → `ActionBar` 显隐 | **不动**；本期不重排操作栏 / 不改 hover 算法 |

**`usePixiAvatarSlot.ts` 唯一改造点**（小幅）：

- hook 返回值由 `void` 改为 `{ slotRef: React.MutableRefObject<PIXI.Container | null> }`，把 slot 暴露给 `usePetLive2D` 接力
- 占位 children（Graphics + Text）保留作为"模型加载中临时显示"——`usePetLive2D` mount 完成 + Live2D 加载完成后调 `slot.removeChildren()` + `slot.addChild(model)`，无 Live2D 加载完成前用户看到的还是 17a 占位（用户体验上"加载中"语义清晰）

---

## 4. Rust 侧改动

### 4.1 `push_subscriber.rs` · `emit_to` 多发一行

文件：`frontend/src-tauri/src/push_subscriber.rs`

定位：line 83 `app.emit_to("bubble", "agent://push", env)` 之后。

改动：

```rust
// 015 现状：只发 bubble 窗
if let Err(e) = app.emit_to("bubble", "agent://push", env.clone()) {
    log::warn!("emit_to(bubble) failed: {e}");
}
// 17b · 桌宠状态机 + lip-sync 也消费 envelope
if let Err(e) = app.emit_to("pet", "agent://push", env) {
    log::warn!("emit_to(pet) failed: {e}");
}
```

> 注：原 line 83 `env`（无 clone）会被 move 进 emit_to；改成"先 clone 给 bubble、再 move 给 pet"。`PushEnvelope` 已 `#[derive(Clone)]`（line 23），无新依赖。

无其他 Rust 改动。`spawn_push_subscriber` / `run_loop` / 分帧 / 解析 / 测试不动。

---

## 5. 前端 store + policy

### 5.1 `frontend/src/stores/petState.ts`

完全对称 015 `petBubble.ts` 形态：

```ts
import { create } from "zustand";
import { listen } from "@tauri-apps/api/event";
import { isTauri } from "@/utils/tauri";
import type { PushEnvelope } from "@/types/push";
import { defaultPetStatePolicy, type PetStateTransition, type PetStatePolicy } from "./petStatePolicy";

export type PetPhase = "idle" | "thinking" | "speaking" | "error";

interface PetStateState {
  phase: PetPhase;
  ingest: (env: PushEnvelope) => void;
  raiseError: () => void;
  reset: () => void;
}

let currentPolicy: PetStatePolicy = defaultPetStatePolicy;
export function setPetStatePolicy(p: PetStatePolicy): void { currentPolicy = p; }
export function resetPetStatePolicy(): void { currentPolicy = defaultPetStatePolicy; }

export const usePetStateStore = create<PetStateState>((set, get) => ({
  phase: "idle",
  ingest(env) {
    const transition: PetStateTransition = currentPolicy(env, get().phase);
    if (transition.next === get().phase) return;
    if (transition.delayMs && transition.delayMs > 0) {
      setTimeout(() => {
        // 仅在仍处于触发态时才执行延迟切换（避免被后续 envelope 抢断）
        if (get().phase === transition.from) set({ phase: transition.next });
      }, transition.delayMs);
      return;
    }
    set({ phase: transition.next });
  },
  raiseError() { set({ phase: "error" }); },
  reset() { set({ phase: "idle" }); },
}));

export async function startPetStateSubscriber(): Promise<() => void> {
  if (!isTauri()) return () => {};
  const unlisten = await listen<PushEnvelope>("agent://push", (e) => {
    usePetStateStore.getState().ingest(e.payload);
  });
  return unlisten;
}
```

### 5.2 `frontend/src/stores/petStatePolicy.ts`

```ts
import type { PushEnvelope } from "@/types/push";
import type { PetPhase } from "./petState";

export interface PetStateTransition {
  from: PetPhase;
  next: PetPhase;
  /** 切换前可选延迟（ms）。speaking → idle 用 300ms 留窗口给 lip-sync 收尾。 */
  delayMs?: number;
}

export type PetStatePolicy = (env: PushEnvelope, current: PetPhase) => PetStateTransition;

const EVENT_KIND = {
  TOOL_CALL_REQUEST: "tool_call_request",
  TOOL_CALL_RESULT: "tool_call_result",
  TEXT_DELTA: "text_delta",
  DONE: "done",
} as const;

export const defaultPetStatePolicy: PetStatePolicy = (env, current) => {
  if (env.kind !== "agent_turn") return { from: current, next: current };
  const types = new Set(env.events.map((e) => e.type));

  // 优先级 1：text_delta 出现 → speaking（避免同帧 tool_call + text_delta 闪进 thinking）
  if (types.has(EVENT_KIND.TEXT_DELTA)) {
    return { from: current, next: "speaking" };
  }

  // 优先级 2：tool_call_* → thinking
  if (types.has(EVENT_KIND.TOOL_CALL_REQUEST) || types.has(EVENT_KIND.TOOL_CALL_RESULT)) {
    return { from: current, next: "thinking" };
  }

  // 优先级 3：done → idle（speaking 走 300ms 延迟收尾；thinking / 直接 done = silent turn 立即回）
  if (types.has(EVENT_KIND.DONE)) {
    if (current === "speaking") return { from: current, next: "idle", delayMs: 300 };
    return { from: current, next: "idle" };
  }

  return { from: current, next: current };
};
```

### 5.3 单测计划

`petStatePolicy.test.ts`（核心 transition 覆盖）：

| 测试 case | 输入 envelope.events | current phase | 预期 next | 预期 delayMs |
|---|---|---|---|---|
| tool_call_request → thinking | `[tool_call_request]` | idle | thinking | undefined |
| text_delta 优先级高于 tool_call | `[tool_call_request, text_delta]` | idle | speaking | undefined |
| text_delta → speaking | `[text_delta]` | thinking | speaking | undefined |
| speaking 中 done → idle 延迟 | `[done]` | speaking | idle | 300 |
| silent turn done → idle 立即 | `[done]` | thinking | idle | undefined |
| user_turn 不动 | — | speaking | speaking | undefined |
| heartbeat 不动 | — | idle | idle | undefined |

`petState.test.ts`：

- 单点 ingest：transition.delayMs 触发延迟 set + 期间被新 envelope 抢断不切错态
- `raiseError` / `reset` 行为
- `setPetStatePolicy` 注入测试 policy → ingest 用新 policy

---

## 6. 前端 Live2D 渲染

### 6.1 `frontend/src/pages/pet/usePixiAvatarSlot.ts` 改造

只改两处：

```ts
// 改造点 1：hook 返回 slot ref
export function usePixiAvatarSlot(...): { slotRef: React.MutableRefObject<PIXI.Container | null> } {
  const slotRef = useRef<PIXI.Container | null>(null);
  useEffect(() => {
    ...
    const slot = new PIXI.Container();
    slot.label = "avatar-slot";
    ...
    slotRef.current = slot;     // 改造点 1：保存 ref
    ...
    return () => {
      slotRef.current = null;   // cleanup 时清掉
      ...
    };
  }, [...]);
  return { slotRef };
}
```

占位 Graphics + Text **保留**——`usePetLive2D` 加载完模型时主动调 `slot.removeChildren()` 再 `addChild(model)`；加载失败时占位仍在（用户体验 fallback）。

### 6.2 `frontend/src/pet/usePetLive2D.ts`

```ts
import { useEffect, useRef, type RefObject } from "react";
import * as PIXI from "pixi.js";
import { Live2DSprite, Config, Priority, LogLevel } from "easy-live2d";
import { PET_LIVE2D_CONFIG } from "./live2dConfig";
import { usePetStateStore } from "@/stores/petState";

// 全局 Config 在 module load 时设置（easy-live2d 是全局 singleton config）
Config.MotionGroupIdle = PET_LIVE2D_CONFIG.motionGroups.idle ?? "Idle";
Config.MouseFollow = false;  // 17b 不用 mouse-follow，跟随由桌宠状态机驱动
Config.CubismLoggingLevel = LogLevel.LogLevel_Warning;

export function usePetLive2D(
  slotRef: RefObject<PIXI.Container | null>,
  app: PIXI.Application | null,
): { spriteRef: RefObject<Live2DSprite | null> } {
  const spriteRef = useRef<Live2DSprite | null>(null);

  useEffect(() => {
    if (!app || !slotRef.current) return;
    let cancelled = false;
    let sprite: Live2DSprite | null = null;

    void (async () => {
      try {
        sprite = new Live2DSprite();
        sprite.modelPath = PET_LIVE2D_CONFIG.modelPath;
        sprite.ticker = app.ticker;
        sprite.renderer = app.renderer;
        sprite.setSize(PET_LIVE2D_CONFIG.spriteSize);

        if (cancelled || !slotRef.current) {
          sprite.destroy({ children: true });
          return;
        }
        // 替换占位 children
        slotRef.current.removeChildren();
        slotRef.current.addChild(sprite);

        await sprite.ready;

        if (cancelled) {
          sprite.destroy({ children: true });
          return;
        }

        spriteRef.current = sprite;

        // 默认播 idle motion（如未设 motionGroups.idle 或模型缺失，startMotion 内部会 throw，try/catch 吃掉）
        const idleGroup = PET_LIVE2D_CONFIG.motionGroups.idle;
        if (idleGroup) {
          try {
            await sprite.startMotion({ group: idleGroup, no: 0, priority: Priority.Idle });
          } catch (e) {
            console.warn("[usePetLive2D] idle motion start failed:", e);
          }
        }
      } catch (e) {
        console.warn("[usePetLive2D] load failed; keeping placeholder:", e);
        usePetStateStore.getState().raiseError();
        if (sprite) sprite.destroy({ children: true });
      }
    })();

    return () => {
      cancelled = true;
      if (sprite) {
        try { slotRef.current?.removeChild(sprite); } catch { /* ignore */ }
        sprite.destroy({ children: true });
      }
      spriteRef.current = null;
    };
  }, [app, slotRef]);

  return { spriteRef };
}
```

> **API 形态确认**：`Live2DSprite` 继承 PIXI Sprite，通过 `sprite.modelPath = "..."` + `sprite.ticker / renderer` 配置 + `await sprite.ready` 等待加载完成，与 17a slot Container 兼容（slot 作 parent，sprite 作 child）。`sprite.setSize({ width, height })` 控制渲染尺寸，不需要手算缩放（与 declare 阶段假设的 `fitModelToSlot` scale 算法不同；easy-live2d 内部按 setSize 自动 fit canvas viewport）。

### 6.3 错误处理

- 模型加载失败（网络 / 文件丢失 / Cubism core 未挂载）→ 占位 children 仍在 + 状态机 raiseError → 进 error 态
- Cubism Core 未挂载（pet.html script 没加载 / 失败）→ `await sprite.ready` 内部 throw，被 try/catch 吃掉 + raiseError
- 模型加载成功但 motion 文件缺失 → log warn 不切动作（沿 [requirement.md 风险 #5](./requirement.md#7-已知风险与监测项不阻塞验收-不进-ac)）

---

## 7. lip-sync · `frontend/src/pet/MouthDriver.ts`

接口 + 实现 + stub 见 §3.5 已完整给出。这里补**状态机 → driver 联动**部分（在 `pet/App.tsx` 内实现）：

```ts
// 伪代码 · pet/App.tsx 内 useEffect
const driver = useMemo(() => new TextCadenceMouthDriver(), []);
const model = /* from usePetLive2D */;

useEffect(() => {
  return usePetStateStore.subscribe((state, prev) => {
    if (prev.phase !== "speaking" && state.phase === "speaking" && model) {
      driver.attach(model);
    } else if (prev.phase === "speaking" && state.phase !== "speaking") {
      driver.detach();
    }
  });
}, [model, driver]);

// text_delta 转发给 driver:speaking 态下 ingest 之外另一条订阅
useEffect(() => {
  if (!isTauri()) return;
  let unlisten: (() => void) | null = null;
  void listen<PushEnvelope>("agent://push", (e) => {
    if (usePetStateStore.getState().phase !== "speaking") return;
    for (const ev of e.payload.events) {
      if (ev.type === "text_delta" && typeof ev.text === "string") {
        driver.onTextDelta?.(ev.text);
      }
    }
  }).then((u) => { unlisten = u; });
  return () => unlisten?.();
}, [driver]);
```

> 这里**额外起了一条 `listen("agent://push", ...)`**——为什么不复用 `petStateStore.subscribe(phase)`？因为 phase 切换是离散的，而 text_delta 是连续流；用 store ingest 改 phase 后又把 text_delta 透传出来会让 store 长出"事件存档"职责。第二条 listen 是只读副流（只关心 speaking 态下的 text_delta），不修改任何 store；与 §3.1 设计精神一致（多 listener 共订 tauri 广播，cost 可忽略）。

`TextCadenceMouthDriver.test.ts`（单测覆盖）：

- attach + onTextDelta 序列 → setMouthOpen 调用时序符合预期（mock requestAnimationFrame + performance.now）
- detach → 立即 setMouthOpen(0) + 清 queue + 后续 onTextDelta 不再触发
- multiple onTextDelta 串行（不并发驱嘴）

---

## 8. `pet/App.tsx` 改造（入口胶水）

```tsx
import { useEffect, useMemo, useRef, useState } from "react";
import { listen } from "@tauri-apps/api/event";
import { Priority } from "easy-live2d";
import { isTauri } from "@/utils/tauri";
import type { PushEnvelope } from "@/types/push";

import { ActionBar } from "./ActionBar";
import { usePixiAvatarSlot } from "./usePixiAvatarSlot";
import { usePetPassthrough } from "./usePetPassthrough";
import { usePetLive2D } from "@/pet/usePetLive2D";
import { PET_LIVE2D_CONFIG } from "@/pet/live2dConfig";
import { TextCadenceMouthDriver } from "@/pet/MouthDriver";
import { startPetStateSubscriber, usePetStateStore } from "@/stores/petState";

export function PetApp() {
  const stageRef = useRef<HTMLDivElement | null>(null);
  // ... 17a 既有 state（spriteScreen / cursorOnSprite / hoverActionBarDom / isDragging / stickyVisible）保持不动

  // 17a · PIXI canvas + avatar-slot（hook 返回值改为含 slotRef + appRef）
  const { slotRef, appRef } = usePixiAvatarSlot(stageRef, setSpriteScreen, setIsDragging);

  // 17a · cursor passthrough
  usePetPassthrough({ isDragging, spriteScreen, setCursorOnSprite });

  // 17b · 启动 push event 订阅 → petStateStore
  useEffect(() => {
    let unlisten: (() => void) | null = null;
    void startPetStateSubscriber().then((u) => { unlisten = u; });
    return () => unlisten?.();
  }, []);

  // 17b · 加载 Live2DSprite 到 avatar-slot
  const { spriteRef } = usePetLive2D(slotRef, appRef.current);

  // 17b · lip-sync · MouthDriver 与状态机联动
  const driver = useMemo(() => new TextCadenceMouthDriver(), []);
  useEffect(() => {
    return usePetStateStore.subscribe((state, prev) => {
      const sprite = spriteRef.current;
      if (prev.phase !== "speaking" && state.phase === "speaking" && sprite) {
        driver.attach(sprite);
      } else if (prev.phase === "speaking" && state.phase !== "speaking") {
        driver.detach();
      }
    });
  }, [driver, spriteRef]);

  // 17b · speaking 态下 text_delta 副流转发给 driver（见 §7）
  useEffect(() => {
    if (!isTauri()) return;
    let unlisten: (() => void) | null = null;
    void listen<PushEnvelope>("agent://push", (e) => {
      if (usePetStateStore.getState().phase !== "speaking") return;
      for (const ev of e.payload.events) {
        if (ev.type === "text_delta" && typeof ev.text === "string") {
          driver.onTextDelta?.(ev.text);
        }
      }
    }).then((u) => { unlisten = u; });
    return () => unlisten?.();
  }, [driver]);

  // 17b · 状态机 → Live2DSprite motion 派发（thinking / speaking / error 切动作）
  useEffect(() => {
    return usePetStateStore.subscribe((state) => {
      const sprite = spriteRef.current;
      if (!sprite) return;
      const group = PET_LIVE2D_CONFIG.motionGroups[state.phase];
      if (group) {
        sprite.startMotion({ group, no: 0, priority: Priority.Normal })
          .catch((e) => console.warn("motion failed:", e));
      }
      // group === null 时不切动作（让默认 idle 继续跑）
    });
  }, [spriteRef]);

  // —— 17a 既有 ActionBar 渲染保持不动 ——
  return (
    <>
      <div ref={stageRef} className="fixed inset-0 overflow-hidden bg-transparent" />
      {spriteScreen && (
        <ActionBar ... />
      )}
    </>
  );
}
```

---

## 9. 资源 + 配置

### 9.1 `frontend/pet.html` 改造

加载顺序：Cubism Core JS 必须**在 main.tsx module 之前**。

```html
<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <title>agent-friend pet</title>
    <link rel="stylesheet" href="/src/styles/index.css" />
    <!-- 17b · Cubism SDK Core JS 必须 module 之前挂载 -->
    <script src="/live2dcubismcore.min.js"></script>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/pages/pet/main.tsx"></script>
  </body>
</html>
```

`bubble.html` / `index.html`（chat）**不动**（bubble / chat 不需要 Live2D）。

### 9.2 `frontend/package.json` 加依赖

```json
{
  "dependencies": {
    "pixi.js": "^8.6.0",
    "easy-live2d": "^0.4.4"
  }
}
```

> M18.1 选定具体 fork 名 + 版本后落 `package.json` + `pnpm install`；`pnpm-lock.yaml` 自动 resolve。

### 9.3 `frontend/vite.config.ts`

不需要改动。public/ 目录原样 ship 是 Vite 默认行为；多文件模型资源在运行期通过 easy-live2d 内部按 `sprite.modelPath` 相对路径加载，Vite 不参与。

> 注：如果选定的 fork 在打包时遇到 `process.env` / `Buffer` 等 Node-only 全局，可能需要在 vite.config.ts 加 polyfill 或 `optimizeDeps.include`。M18.1 撞墙时按 fork 文档微调，本期 design 不预先动 vite 配置。

### 9.4 `frontend/src-tauri/tauri.conf.json`

不动。bubble window / pet window / chat window 配置沿 17a；CSP 不限制 public 同源资源加载。

---

## 10. 17b/17c 接缝点（供 17c 接力）

| # | 接缝点 | 17b 落地 | 17c 接 |
|---|---|---|---|
| 1 | MouthDriver 接口 | `MouthDriver` interface + `TextCadenceMouthDriver` + `AudioRmsMouthDriver` stub | 实现 `AudioRmsMouthDriver.onAudioFrame` 接 voice_bridge audio out / 火山 RTC PCM 流 |
| 2 | driver factory 切换点 | `pickMouthDriver(env)` 单一返回 `TextCadenceMouthDriver` | 按 `env.source_kind` / voice 通道激活态选 `TextCadenceMouthDriver` vs `AudioRmsMouthDriver` |
| 3 | live2dConfig | 单模型 hardcode | 多模型 dropdown / 用户设置 / persona 关联（与 007 voice_type 选音色对称） |
| 4 | 状态机扩展态 | 4 态 idle / thinking / speaking / error | 加 sleeping / away / excited / celebrating 等（如有产品诉求） |
| 5 | error 恢复路径 | 下一个 envelope 自动恢复 | 加用户手动点击形象 reset / SSE 重连后自动 reset / etc |

---

## 11. 测试策略

### 11.1 Rust 单测

- `push_subscriber.rs` 既有测试不动；`emit_to("pet")` 多发一行**不增加单测**（emit_to 是 tauri runtime 提供、行为在集成测试 / 手动跑里验）。

### 11.2 前端单测

| 文件 | 覆盖 |
|---|---|
| `stores/petStatePolicy.test.ts` | §5.3 transition table 7 case 全覆盖 |
| `stores/petState.test.ts` | ingest delayMs 延迟切 + raiseError + reset + setPolicy 注入 |
| `pet/MouthDriver.test.ts` | TextCadenceMouthDriver attach + onTextDelta 序列 → setMouthOpen 时序（mock raf + performance.now） |

17a 既有单测（`slotBoundsHit.test.ts` / `computeActionBarPosition.test.ts` / `petBubble.test.ts` / `petBubblePolicy.test.ts` / `petBubbleSync.test.ts`）全部维持。

### 11.3 手动真跑端到端

| AC | 验证方式 | 平台 |
|---|---|---|
| AC-1 Live2D 加载 | dev 启动 → 看 PIXI canvas 内 Hiyori 模型加载 + 几何位置 / 锚点与 17a 占位 1:1 | macOS + Win |
| AC-2 默认 idle motion 播放 | 看模型呼吸 / 待机 motion 在播 | macOS + Win |
| AC-3 4 态切换 | dev CLI BedtimeSource / IdleReflectionSource 触发 + chat 窗输入 → 看 petStateStore phase log + Live2DSprite motion 切换 | macOS + Win |
| AC-4 push event → state 派发 | 详细在 console 看 `tool_call_request` 进 thinking / `text_delta` 进 speaking / `done` 回 idle | macOS + Win |
| AC-5 lip-sync 文本 cadence | speaking 态下看模型嘴部参数动；exit speaking 后回零 | macOS + Win |
| AC-6 MouthDriver 接口扩展位 | 代码 review + `AudioRmsMouthDriver` stub 存在 + interface 签名稳定 | — |
| AC-7 17a 接缝点零退化 | drag sprite + bubble 跟随 / cursor alpha hit-test / 操作栏 hover gate 跑 17a AC-4/5/6 子集等价 | macOS + Win |
| AC-8 17a Win 真机 12 AC | 17a AC-1 ~ AC-12 Win 真机跑（17a M17.9 留的） | Win |
| AC-9 / AC-10 015 / 016 全 AC 回归 | 15 dev CLI BedtimeSource / IdleReflectionSource + 16 inject_test_envelope | macOS + Win |
| AC-11 chat / pet 零退化 | 操作栏点"打开对话" + chat 窗输入 + tray 菜单 | macOS + Win |
| AC-12 cross-build 全绿 | `./scripts/check` + `cargo build` 双平台 | macOS + Win |

---

## 12. 影响分析

### 12.1 上下游影响

- **15 模块代码**：仅 `push_subscriber.rs` emit target 增加一份发给 pet 窗（叠加不替换）；store / policy / `startPetBubbleSubscriber` / `attachBubbleWindowSync` / sessionProjection 不动；015 全 9 AC 仍然成立。
- **16 模块代码**：`bubble_window.rs` / `PetBubble` / bubble window 显隐 / size / 内容渲染零改动；016 全 12 AC 仍然成立。
- **17a 模块代码**：`ActionBar.tsx` / `usePetPassthrough.ts` / `slotBoundsHit.ts` / `computeActionBarPosition.ts` / `update_sprite_pos` invoke / `BubbleState.sprite_pos` / `run_follow_loop` / NSPanel setup 不动；`usePixiAvatarSlot.ts` 仅暴露 slotRef（hook 返回值由 void 变 `{ slotRef }`）；17a 全 12 AC 仍然成立（macOS 已通过的 7/12 不退化；Win 补 12/12 是本期 AC-8）。
- **17b 新增模块**：`petState` / `petStatePolicy` / `usePetLive2D` / `MouthDriver` / `live2dConfig` 全部为本期新增，与既有模块通过明确接口（push event listen + slot ref + Live2DSprite handle）耦合，未来扩展（17c voice / 多模型）只挂新增模块、不动既有。
- **chat / tray / push subscriber 流向 / sessionProjection / petBubblePolicy / usePetBubbleStore / `<PetBubble />`**：全部不动。
- **dev CLI 端到端**：015 BedtimeSource / IdleReflectionSource + 016 inject_test_envelope 路径不动；新增"看 petStateStore phase 切换"的观测点。

### 12.2 风险点

沿 [requirement.md §7](./requirement.md#7-已知风险与监测项不阻塞验收-不进-ac) 7 项 + 本 design 加 3 项：

| # | 风险 | 概率 / 影响 | 缓解 |
|---|---|---|---|
| 8 | 选定 fork 在 PIXI v8 实际不兼容 / API 不匹配 | 中 / 高 | §3.3 fallback 路径登记（候选 fork → easy-live2d → Cubism Web Samples 自渲染）；M18.1 选型 milestone 撞墙现场切 |
| 9 | Cubism Core JS 与 pet.html 加载顺序竞争 / `window.Live2DCubismCore` 未挂载 | 低 / 中 | script tag 在 module entry 之前同步加载（保证 `await sprite.ready` 调用时全局可用）；usePetLive2D try/catch + raiseError 兜底 |
| 10 | Live2D model.motion 切换在 speaking → idle 延迟切窗口内被抢断 | 低 / 低 | store ingest 延迟切前重新读 phase 确认；motion 失败 try/catch + log warn |
| 11 | speaking 态 text_delta 副流 listen + petStateStore subscribe 两条 listener 在 envelope 同帧到达时的执行顺序竞争（先切 speaking 还是先转 text_delta）| 中 / 低 | tauri event listener 同帧按注册顺序执行；先注册 petStateStore subscriber 让 phase 先切；首条 text_delta 进 driver 时 phase 已是 speaking。M18.6 验证 |

### 12.3 跨平台行为

| 维度 | macOS | Win | Linux |
|---|---|---|---|
| PIXI canvas + Live2D 渲染 | NSPanel 30fps cap（沿 17a 已知）| WebView2 native fps（17a Win spike ≈ 300fps） | 不在范围 |
| Cubism Core JS 加载 | 同 public/ + script tag | 同 | — |
| 模型文件加载（model3.json + 多文件） | Vite public/ 原样 ship | 同 | — |
| push event → state 切换 | Rust emit_to + TS listen 行为一致 | 同 | — |
| MouthDriver `requestAnimationFrame` | 跟随 PIXI fps（30fps cap）| 跟随 Win fps（无 cap）| — |
| 内存 / GPU 长跑 | 沿 17a 已知 Tahoe ≈ 110MB；Live2D 加 ~20-50MB | 沿 17a Win spike ≈ 40-60MB；Live2D 加同量级 | — |

无新增 `#[cfg(target_os = "...")]` gate。

---

## 13. 变更记录

| 日期 | 变更内容 | 是否需要重新实现 |
|------|---------|----------------|
| 2026-06-15 | M18.1 探查发现公开 npm 上不存在 PIXI v8 兼容的 `pixi-live2d-display` fork（主仓 v6 / 3 个主流社区 fork 全 v7）；触发 design §3.3 撞墙 fallback 路径，用户拍板切 fallback 2 · `easy-live2d@^0.4.4`。design §3.3 / §3.4 / §3.5 / §6.2 / §8 / §9.2 / §10 / §11.3 / §12 把 API 形态从 `pixi-live2d-display.Live2DModel.from` 改为 `easy-live2d.Live2DSprite + sprite.modelPath = ... + await sprite.ready`；`live2dConfig.fitMode` 改为 `spriteSize` 让 `sprite.setSize` 自动 fit；变量 / 类型名 `model` → `sprite`、`Live2DModel` → `Live2DSprite`、`model.motion(group)` → `sprite.startMotion({ group, no, priority })`。Cubism Core JS + Hiyori 模型资源已落 `frontend/public/` 下，license 文件随附。store / policy / MouthDriver 接口签名 / 状态机 transition table 不动；§3.1 push event 多窗分发 / §4 Rust 改动 / §5 store + policy / §6.1 17a hook 改造方式不动。 | 否（design 阶段澄清，仅 API 形态调整，子模块边界与机制层设计不变）|

---

## 文档元信息

- **状态**：已确认（Confirmed）
- **创建时间**：2026-06-15
- **确认时间**：2026-06-15
- **关联需求**：[requirement.md](./requirement.md)
- **关联决策**：[ADR 0004 · 桌宠承载形态与渲染路径](../../decisions/0004-pet-overlay-route/README.md)
- **关联前置**：[17a design.md §7 接缝点表](../017-pet-overlay-form-switch/design.md#7-17a17b-接缝点显式列表供-17b-接力)
- **下期承接**：17c · audio mel/RMS lip-sync（接 007 voice_bridge）+ 多模型切换 + 状态机扩展态
