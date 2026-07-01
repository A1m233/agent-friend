# 041 · 桌宠边缘召回交互 - 技术方案

> Pet Edge Recall Interaction - Technical Design

---

## 状态

<!-- 草稿（Draft） | 已确认（Confirmed） -->
已确认（Confirmed）

---

## 2026-07-01 语义修订

本方案在后续前端优化中修正了一个耦合：原 `petDisplayMode = normal | edgeRecall` 同时表达“尺寸”和“贴边”，导致持久化 edge-recall 后冷启动自动贴边。

当前实现采用“尺寸状态 + 一次性 placement 行为”：

- `petAvatarSize = normal | mini`：用户尺寸偏好，走 settings 持久化。
- 从 `normal -> mini` 的尺寸转换：触发一次移动到最近边缘。
- 从 `mini -> normal` 的尺寸转换：恢复正常尺寸，并以当前小模型位置为参考做屏幕 clamp。

旧 settings 中的 `petDisplayMode=edgeRecall` 仅作为 legacy 输入迁移为 `petAvatarSize=mini`；迁移后会保持 mini 尺寸，但不会让 pet 冷启动自动贴边。后文若仍出现 `petDisplayMode` / `edgeRecall` 持久化，视为原 041 方案背景，不代表当前实现口径。

---

## 需求文档

→ [`requirement.md`](./requirement.md)

---

## 1. 现状分析

### 1.1 当前相关实现

| 文件 | 现状概要 |
| --- | --- |
| `frontend/src/pages/pet/App.tsx` | pet 窗协调层：持有 `spriteScreen` / `actionBarAnchor` / `avatarPlacementPoint` / hover gate 状态；根据 settings 的 `avatarModelId` 选择 Live2D 或 Spine surface；ActionBar 的 `onHidePet` 仍 invoke `hide_pet` |
| `frontend/src/pages/pet/ActionBar.tsx` | 横向 chip + TooltipButton + carousel；位置由 `computeActionBarPosition(actionBarAnchor, barSize)` 决定；按钮里有 `EyeOff` + tooltip "隐藏桌宠" |
| `frontend/src/pages/pet/computeActionBarPosition.ts` | 只负责横向 ActionBar：默认贴 anchor 上方、屏顶不足时翻到下方 |
| `frontend/src/pages/pet/usePetPassthrough.ts` | 透明 overlay 鼠标穿透：DOM `[data-hit]` 优先、其次 `spriteScreen` + runtime alpha hit-test，drag 期间锁定不穿透 |
| `frontend/src/pages/pet/usePixiAvatarSlot.ts` | Live2D 底座：创建 Pixi v8 slot，处理 drag/click、alpha-scan、`spriteScreen` 上报、`initialAnchorPoint` 对齐 |
| `frontend/src/pet/avatarRuntime/live2d/Live2DAvatarSurface.tsx` | Live2D runtime wrapper：用 `usePixiAvatarSlot` 承载模型，输出 `PetAvatarRuntime` |
| `frontend/src/pet/avatarRuntime/spine38/Spine38AvatarSurface.tsx` | Spine runtime wrapper：创建 Pixi7 slot，`fitSpine38ToStage` 决定初始 scale/position，处理 drag/click、visible bounds、`actionBarAnchor` offset |
| `frontend/src/pet/avatarRuntime/spine38/spine38Fit.ts` | Spine fit 纯函数：把模型按 fit 配置放进目标 viewport |
| `frontend/src-tauri/src/lib.rs` | `hide_pet` command 直接隐藏 pet 窗；tray `toggle_pet` 直接 `w.hide()` / `w.show()` |
| `frontend/src/lib/settings/index.ts` / `frontend/src-tauri/src/settings.rs` | 现有 settings 通路：前端类型 + 默认值 + `useSetting`，Rust `Settings` + `set_setting` + `settings.json` 落盘 + `settings://changed` |
| `frontend/src-tauri/src/bubble_window.rs` | bubble 跟随 `update_sprite_pos` 上报的物理坐标；只要缩放后继续上报当前 `spriteScreen`，bubble 会自然跟随 |

### 1.2 已有有利条件

- 040 之后 `PetApp` 已经有 `actionBarAnchor` 与 `avatarPlacementPoint`，切换模型时可保持 anchor 连续。
- Live2D / Spine 都有“slot”概念，点击、拖拽、hit-test、sprite position 上报都围绕 slot/visible bounds 工作。
- bubble 跟随已经不依赖 pet 窗口尺寸，而是依赖前端上报的 sprite position；缩小后只要上报新的 bounds，Rust 侧无需理解 edge-recall。
- settings 已经统一解决启动首帧注入、跨窗口广播、落盘路径和默认值。

### 1.3 主要设计约束

- 不新增窗口，不重做 transparent overlay / passthrough 底座。
- 不把 edge-recall 做成独立图标或第二套交互。
- 不能让 Live2D / Spine 各自发明 display mode；模式和几何策略要有统一来源。
- 不持久化拖拽后的精确位置；只持久化 normal / mini 尺寸偏好，移动到边缘是进入 mini 时的一次性行为。
- 桌面视觉结论必须用真实 Tauri pet 窗验证。

---

## 2. 方案概览

本方案把 041 拆成四层：

1. **Settings 层**：持久化用户选择的尺寸偏好 `petAvatarSize`。
2. **Display Profile 层**：从 `petAvatarSize` 推导 scale，并根据 `previousAvatarSize -> petAvatarSize` 的转换推导一次性 placement / restore 语义。
3. **Renderer Adapter 层**：Live2D / Spine 分别把统一 profile 应用到自己的 Pixi slot 和 fit 逻辑。
4. **ActionBar Layout 层**：根据当前 anchor 与 viewport 选择横向 / 竖向布局，与 display mode 解耦。

核心原则：**尺寸偏好持久化，贴边只是进入 mini 的行为；渲染差异局部适配；几何策略集中在纯函数中，方便验收后替换。**

---

## 3. 文件改动清单

| 文件 | 改动类型 | 说明 |
| --- | --- | --- |
| `docs/requirements/041-pet-edge-recall-interaction/design.md` | 新增 | 本技术方案 |
| `frontend/src/lib/settings/index.ts` | 修改 | 新增 `PetAvatarSize` 类型、`Settings.petAvatarSize` 字段、默认值；兼容旧 `petDisplayMode` 快照 |
| `frontend/src-tauri/src/settings.rs` | 修改 | Rust `Settings` 增加 `pet_avatar_size` enum/字段/default/`set_setting` 分支与测试；旧 `petDisplayMode` 迁移为尺寸偏好 |
| `frontend/src/pages/pet/petDisplayProfile.ts` | 新增 | 统一 avatar size、profile、edge placement、restore placement、viewport helper 等纯函数 |
| `frontend/src/pages/pet/petDisplayProfile.test.ts` | 新增 | 覆盖 size → scale、normal→mini 触发贴边、mini 冷启动不贴边、贴边 clamp、restore clamp |
| `frontend/src/pages/pet/actionBarLayout.ts` | 新增 | ActionBar 横/竖布局判定与定位纯函数 |
| `frontend/src/pages/pet/actionBarLayout.test.ts` | 新增 | 覆盖远离边缘横向、左右/上下边缘竖向、屏幕 clamp |
| `frontend/src/pages/pet/App.tsx` | 修改 | 读取/写入 `petAvatarSize`；用尺寸转换生成统一 display profile；替换 `hidePet` 为 shrink-and-move / restore-size handlers；传 profile 给 avatar surface 和 ActionBar |
| `frontend/src/pages/pet/ActionBar.tsx` | 修改 | 支持 horizontal / vertical 两种布局；按钮 tooltip 按尺寸切为“缩小桌宠并移动到边上”/“恢复桌宠大小”；继续用 TooltipButton |
| `frontend/src/pages/pet/usePixiAvatarSlot.ts` | 修改 | 接收 display profile 或 slot transform options，应用 Live2D slot scale / placement |
| `frontend/src/pet/avatarRuntime/live2d/Live2DAvatarSurface.tsx` | 修改 | 透传 display profile 给 `usePixiAvatarSlot` |
| `frontend/src/pet/avatarRuntime/spine38/Spine38AvatarSurface.tsx` | 修改 | 接收 display profile，应用 Spine slot scale / placement，并保持 actionBar anchor offset |
| `frontend/src/pet/avatarRuntime/spine38/spine38Fit.ts` | 修改 | 支持 profile scale 或 target viewport 的小尺寸 fit；补单测 |
| `frontend/src-tauri/src/lib.rs` | 修改 | 更新 `hide_pet` 注释；tray show 不改尺寸偏好或 dock state，只恢复窗口可见 |
| `docs/issues/013-pet-recall-from-desktop/README.md` | 实现验收后修改 | 状态改 `resolved`，写修复指向 |

---

## 4. Settings 持久化

### 4.1 前端类型

在 `frontend/src/lib/settings/index.ts` 增加：

```ts
export type PetDisplayMode = "normal" | "edgeRecall";

export interface Settings {
  theme: ThemeMode;
  voiceTunnelConsentAccepted: boolean;
  deepseekModel: DeepseekModel;
  deepseekMemoryModel: DeepseekModel;
  avatarModelId: AvatarModelId;
  petDisplayMode: PetDisplayMode;
}

export const DEFAULT_SETTINGS: Settings = {
  // existing...
  petDisplayMode: "normal",
};
```

命名选择 `edgeRecall` 而不是 `collapsed`，是为了表达“桌面上仍可见、可召回”的产品语义，避免被误解为 window hidden。

### 4.2 Rust 类型

在 `frontend/src-tauri/src/settings.rs` 增加：

```rust
#[derive(Serialize, Deserialize, Clone, Debug, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub enum PetDisplayMode {
    Normal,
    EdgeRecall,
}
```

`Settings` 增加：

```rust
#[serde(default = "default_pet_display_mode")]
pub pet_display_mode: PetDisplayMode,
```

`set_setting` 增加 `"petDisplayMode"` 分支。测试补：

- default 为 `Normal`
- JSON 缺字段时默认 `Normal`
- `set_setting` 能解析 `"edgeRecall"`
- `build_init_script` 注入包含 `petDisplayMode`

### 4.3 托盘语义

tray `toggle_pet` 继续只控制窗口 show/hide，不写 settings。

这样用户若选择 edge-recall 后通过托盘隐藏，再通过托盘显示，pet webview 仍会根据 settings 以 edge-recall 展示。这符合“托盘不强制恢复 normal”的共识，也与“重启后保持小/全形象一致”一致。

---

## 5. Display Profile 统一管理

### 5.1 类型

新增 `frontend/src/pages/pet/petDisplayProfile.ts`：

```ts
export type PetDisplayMode = "normal" | "edgeRecall";
export type PetEdge = "left" | "right";

export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface Point {
  x: number;
  y: number;
}

export interface Viewport {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface PetDisplayProfile {
  mode: PetDisplayMode;
  scale: number;
  placement: {
    kind: "keep" | "edge" | "restore";
    targetCenter: Point | null;
    edge?: PetEdge;
  };
}
```

### 5.2 常量

初始常量集中在同一文件：

```ts
export const PET_NORMAL_SCALE = 1;
export const PET_EDGE_RECALL_SCALE = 0.5;
export const PET_EDGE_VISIBLE_RATIO = 0.62;
export const PET_VIEWPORT_MARGIN = 8;
```

这些值只作为本期初始体感，真实桌面验收后可调；不要散落在 Live2D / Spine / ActionBar 组件里。

### 5.3 统一策略函数

#### `resolvePetDisplayProfile`

根据当前 mode、当前 anchor、viewport 和触发原因生成 profile：

```ts
export function resolvePetDisplayProfile(params: {
  mode: PetDisplayMode;
  previousMode: PetDisplayMode | null;
  anchor: Rect | null;
  viewport: Viewport;
}): PetDisplayProfile
```

规则：

- `normal`：`scale = 1`；若从 edge-recall 恢复，则 placement 为 `restore`，target center 由当前小形象中心 clamp 后得到；否则 `keep`。
- `edgeRecall`：`scale = PET_EDGE_RECALL_SCALE`；若从 normal 进入或启动时无现成 edge placement，则 placement 为 `edge`，target center 由 `computeEdgeRecallPlacement` 得到；若已经是 edge-recall 后拖动，则 `keep`。

#### `computeEdgeRecallPlacement`

唯一管理“贴哪条边、贴到哪里”的函数：

```ts
export function computeEdgeRecallPlacement(params: {
  anchor: Rect;
  viewport: Viewport;
  targetSize: { w: number; h: number };
}): { edge: PetEdge; targetCenter: Point }
```

策略 v1：

- 只在左右边缘中选择停靠边；本期不做顶部 / 底部停靠，避免出现只露腿或只露头的召回态。
- 从 normal 主动收起时，根据 anchor 到左/右边缘的距离选最近边。
- 持久化 `edgeRecall` 冷启动且没有历史位置时，默认停靠到右边缘。
- left/right：让缩小后的形象保留 `PET_EDGE_VISIBLE_RATIO` 的宽度在屏内，y 按当前中心 clamp。
- 角落不作为独立 mode；当 y clamp 贴近上下边界时，只形成左右边缘靠近角落的效果。

这个函数是策略接缝。如果验收后觉得“最近左右边”不好，可以替换为“总是右侧”“优先避开 Dock 所在侧”“根据 ActionBar 空间选边”等，不影响 renderer。

#### `clampAvatarCenter`

恢复 normal 或 edge placement 时使用：

```ts
export function clampAvatarCenter(params: {
  center: Point;
  size: { w: number; h: number };
  viewport: Viewport;
  margin?: number;
}): Point
```

保证恢复 normal 后形象不跑出屏幕。

### 5.4 viewport 来源

前端使用 pet webview viewport 坐标系：

```ts
export function getPetViewport(): Viewport {
  return { x: 0, y: 0, w: window.innerWidth, h: window.innerHeight };
}
```

这与 `spriteScreen` / `actionBarAnchor` 的 CSS px 坐标系一致；Rust 侧多屏 origin / DPR 仍由现有 `update_sprite_pos` 和 `spawn_cursor_feed` 处理。

---

## 6. Renderer Adapter

### 6.1 共同接口

两个 avatar surface 都接收：

```ts
displayProfile: PetDisplayProfile;
```

但不强行抽象一个大 runtime。共同管理在 profile 层；不同 renderer 自己负责如何把 profile 应用到 slot。

### 6.2 Live2D 适配

`Live2DAvatarSurface` 继续使用 `usePixiAvatarSlot`，把 `displayProfile` 传入：

```tsx
usePixiAvatarSlot(stageRef, setSpriteScreen, setIsDragging, {
  interactionsRef,
  initialAnchorPoint,
  displayProfile,
});
```

`usePixiAvatarSlot` 中：

- normal：保持当前 slot scale / placement 逻辑。
- edge-recall：在 stable bounds 可用后，根据 profile 的 `targetCenter` 调整 slot 位置，并对 slot scale 乘以 `displayProfile.scale`。
- drag：拖动时只移动 slot，保持当前 profile scale。
- restore normal：scale 回 1，并以当前小形象中心为参考 clamp 后调整 slot。

注意点：

- `cachedOffsetRef` 表达 visible bounds 相对 slot 的偏移。slot scale 改变时，bounds 计算必须使用 scale 后的世界坐标，不能沿用未缩放尺寸。
- hitArea 使用缩放后的 visible bounds。
- `update_sprite_pos` 上报缩放后的 `spriteScreen`。

### 6.3 Spine 适配

Spine 当前 scale 主要由 `fitSpine38ToStage` 决定。041 不直接在每个调用点手算，而是在 Spine surface 内统一把 profile 转成“额外 display scale”：

- 初始 fit：仍由 `fitSpine38ToStage` 得到 normal scale。
- edge-recall：在 normal fit scale 基础上乘以 `displayProfile.scale`。
- `toScreenRegion`、`toSlotHitRegion`、`syncBounds` 继续以 slot.scale 后结果为准。
- `actionBarAnchorOffsetRef` 继续表达 actionBar anchor 相对 `spriteScreen` 的 offset；缩放后 offset 应重新计算或按缩放后的 visible bounds 更新，避免 ActionBar 锚在旧尺寸上。

`spine38Fit.ts` 可补一个小 helper：

```ts
export function applyDisplayScale(baseScale: number, profileScale: number): number
```

或者直接在 surface 内处理。关键是 display profile 来源统一，不让 Spine 自己决定 edge 策略。

### 6.4 不做的抽象

不新增全局 avatar runtime registry / adapter interface。当前只有两个 renderer，本期只需要统一 profile 输入与几何策略；强行抽复杂接口会扩大 041 范围。

---

## 7. PetApp 状态流

### 7.1 状态

`PetApp` 增加：

```ts
const [petDisplayMode, setPetDisplayMode] = useSetting("petDisplayMode");
const previousDisplayModeRef = useRef<PetDisplayMode | null>(null);
```

基于当前 anchor 和 viewport 计算：

```ts
const displayProfile = useMemo(
  () => resolvePetDisplayProfile({
    mode: petDisplayMode,
    previousMode: previousDisplayModeRef.current,
    anchor: actionBarAnchor ?? spriteScreen,
    viewport: getPetViewport(),
  }),
  [petDisplayMode, actionBarAnchor, spriteScreen],
);
```

mode 改变后更新 `previousDisplayModeRef`。

### 7.2 handlers

替换当前 `hidePet`：

```ts
const collapseToEdge = () => {
  void setPetDisplayMode("edgeRecall");
};

const restoreNormal = () => {
  void setPetDisplayMode("normal");
};
```

ActionBar props 改为：

```tsx
<ActionBar
  petDisplayMode={petDisplayMode}
  onCollapseToEdge={collapseToEdge}
  onRestoreNormal={restoreNormal}
  ...
/>
```

`hide_pet` 不再由 ActionBar 使用；保留给 tray 或未来“完全隐藏”入口。

### 7.3 启动时 edge-recall

如果 settings 首帧就是 `edgeRecall`：

- 初始模型可能还没有 `spriteScreen`。
- profile 可以先保持 `placement.kind = "keep"`。
- 首次 stable bounds 出来后，renderer 调 `resolvePetDisplayProfile` / `computeEdgeRecallPlacement` 贴边。

实现上可通过 `hasAppliedProfilePlacementRef` 避免每帧重复把用户拖动后的小形象吸回边缘。

---

## 8. ActionBar Layout

### 8.1 类型

新增 `frontend/src/pages/pet/actionBarLayout.ts`：

```ts
export type ActionBarOrientation = "horizontal" | "vertical";

export interface ActionBarLayout {
  orientation: ActionBarOrientation;
  left: number;
  top: number;
  width: number;
  height: number;
}
```

### 8.2 计算函数

```ts
export function computeActionBarLayout(params: {
  anchor: Rect;
  viewport: Viewport;
  horizontalSize: { w: number; h: number };
  verticalSize: { w: number; h: number };
  margin?: number;
  edgeThreshold?: number;
}): ActionBarLayout
```

规则 v1：

- 如果 anchor 中心靠近左/右边缘，或 horizontal layout 会明显溢出，则使用 vertical。
- horizontal：复用当前 `computeActionBarPosition` 的语义，默认上方，屏顶不足翻下方，再做 viewport clamp。
- vertical：优先贴在 anchor 远离边缘的一侧，例如靠左边时 bar 放右侧，靠右边时放左侧；如果上下空间不足，则 top clamp 到 viewport。
- 返回 width/height，用于 style 和诊断。

### 8.3 ActionBar JSX

按钮数组改为根据 mode 生成：

```ts
const displayButton =
  petDisplayMode === "edgeRecall"
    ? { icon: <Maximize2 />, tooltip: "恢复正常", onClick: onRestoreNormal }
    : { icon: <PanelRightClose />, tooltip: "收起到边缘", onClick: onCollapseToEdge };
```

横向布局保持现有 carousel 结构，使用左右箭头翻页。竖向布局复用同一份按钮数组与 `PAGE_SIZE` 分页语义，改用上下箭头翻页；两种布局只改变排列方向，不改变可访问入口数量。

所有容器和按钮继续 `data-hit`，由 `usePetPassthrough` DOM hit-test 承担命中。

---

## 9. Rust / Tray

### 9.1 `hide_pet`

保留 command，但更新注释：

- 语义：完全隐藏 pet window。
- 不再作为 ActionBar “收起”入口。
- 桌面召回由前端 edge-recall 负责。

### 9.2 tray `toggle_pet`

保持：

```rust
if w.is_visible().unwrap_or(true) {
    let _ = w.hide();
} else {
    let _ = w.show();
}
```

不写 `petDisplayMode`，不强制恢复 normal。

如真实验收发现 `w.show()` 后 webview 可见但 profile 没应用，可补一个轻量 event：

```rust
let _ = app.emit_to("pet", "pet://window-shown", ());
```

前端收到后只触发一次 profile re-apply，不改变 settings。此事件作为实现兜底，不改变产品语义。

---

## 10. 测试策略

### 10.1 前端单测

新增：

- `petDisplayProfile.test.ts`
  - default normal profile
  - normal → edge-recall 在左右边缘中选择最近边
  - 持久化 edge-recall 冷启动默认右边缘
  - edge-recall target center clamp
  - edge-recall → normal restore clamp
  - edge-recall 持续拖动后不反复吸边

- `actionBarLayout.test.ts`
  - 中央位置 horizontal
  - 左侧/右侧 near edge vertical
  - horizontal 溢出时 vertical
  - vertical layout clamp 不越屏

更新：

- `usePixiAvatarSlot.test.tsx`
  - edge display profile 使 slot 缩放并移动到 target center
  - drag 后保持 edge scale

- `spine38Fit.test.ts`
  - display scale 叠加后尺寸按比例缩小

### 10.2 Rust 单测

更新 `settings.rs` tests：

- `Settings::default()` 的 `pet_display_mode == Normal`
- 缺字段兼容默认
- parse `"edgeRecall"`
- `set_setting` unknown key 仍拒绝，`petDisplayMode` 合法 key 接受

### 10.3 真实桌面验收

按 `desktop-visual-inspection` 路径验证：

- normal 下点击收起到边缘，窗口未 hidden，桌面仍可见小形象。
- 小形象 hover / click / drag / ActionBar 可用。
- 小形象恢复 normal，尺寸和位置合理。
- 拖到屏幕边缘时 ActionBar 竖向显示，离开边缘恢复横向。
- 托盘 hide/show 后仍按 settings 的 `petDisplayMode` 展示。
- 改 settings 到 edge-recall 后重启，首帧或模型加载完成后以边缘召回态出现。

---

## 11. 影响分析

### 11.1 上游影响

- settings schema 增加字段。因 Rust serde default 和前端 DEFAULT_SETTINGS 都补默认值，旧 settings 文件可兼容。
- ActionBar props 改名会影响 `PetApp` 调用处；没有其他调用方。
- Live2D / Spine surface 增加 display profile prop；由 `PetApp` 单点传入。

### 11.2 下游影响

- `usePetPassthrough` 不需要理解 display mode，只消费缩放后的 `spriteScreen`。
- `bubble_window.rs` 不需要理解 display mode，只消费缩放后的 `update_sprite_pos`。
- Settings 窗口如果未来展示 pet display mode，可直接复用 settings 字段；本期不加设置 UI。

### 11.3 风险

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| Live2D / Spine scale 入口不同导致行为不一致 | edge-recall 两后端体感不同 | profile 统一、renderer adapter 局部适配；真实桌面验收覆盖当前可选模型 |
| 缩放后 hit-test 坐标错位 | 小形象点不到或透明区挡桌面 | 单测 slot bounds；真实桌面验证 click/drag/passthrough |
| ActionBar 竖向布局与 carousel 冲突 | dev 按钮多时布局乱 | 竖向不直接复用横向 carousel，改为同一分页状态的上下箭头与当前页按钮列表 |
| settings 持久化导致启动时反复吸边 | 用户拖动后被 profile 重置 | 用 placement applied ref，只在进入 mode / 启动首次应用贴边 |
| edge-recall bubble 太大 | 视觉压迫 | 本期只保证跟随；体验问题登记后续 issue |

---

## 12. 实施任务草案

1. 扩展 settings 前后端字段与测试。
2. 新增 `petDisplayProfile.ts` 与单测。
3. 新增 `actionBarLayout.ts` 与单测。
4. 改 `PetApp`：读取 settings、生成 profile、替换 ActionBar hide handler。
5. 改 Live2D `usePixiAvatarSlot` 应用 profile。
6. 改 Spine surface / fit 应用 profile。
7. 改 ActionBar 支持横/竖布局与收起/恢复按钮。
8. 更新 Rust `hide_pet` 注释与必要 tray show 兜底事件。
9. 跑自动化门禁与真实桌面验收。
10. 验收后关闭 issue 013。

---

## 13. 变更记录

| 日期 | 变更内容 | 是否需要重新实现 |
| --- | --- | --- |
| 2026-07-01 | 初版技术方案确认：统一 display profile / placement 管理，renderer adapter 局部适配，settings 持久化 normal / edge-recall。 | 否 |
