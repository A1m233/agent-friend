# 038 · Spine Avatar Backend Ark Models

## 状态

CONFIRMED

## 背景

当前桌宠形象已经有一条正式 Live2D 路径：Hiyori 模型在 pet 窗中通过 PIXI `avatar-slot` 承载，并复用了透明窗口、拖拽、ActionBar 锚点、bubble 跟随、alpha hit-test、状态机 motion、lip-sync 和鼠标反馈等能力。

并行探索确认，明日方舟宿舍 / 基建小人主要是 Spine 骨骼动画，不是 Live2D。项目里已有一个独立 `spine-spike` 页面，可以加载 Ark-Models 阿米娅 fixture，并验证了 Arknights / Ark-Models Spine 3.8 资源在 Web/PIXI 环境中的可行性。但该能力仍停留在 spike 页面，正式 pet 窗仍是 Live2D 专用。

本需求要把现有 Spine spike 收敛成正式 avatar backend 能力，让 Ark-Models Spine 小人可以作为桌宠 avatar runtime 接入现有 pet slot。它不是替换 Live2D，而是在现有 avatar 资源 catalog 地基之上增加 Spine backend，使 Live2D 与 Spine 能力差异被显式表达。

## 目标

- 从现有 Spine spike 中抽出正式的 Spine avatar runtime，而不是把 spike 页面逻辑直接塞进 `PetApp`。
- 让 Spine runtime 能挂载到现有 pet window 的 PIXI avatar slot，并复用 sprite bounds、alpha hit-test、drag、ActionBar anchor、bubble follow 等外层能力。
- 建立 Live2D 与 Spine 的能力差异模型：Spine 有 animation / action 能力；当前不具备 Live2D 的 mouth / gaze / expression 参数能力。
- 接入 Ark-Models Spine 3.8 路线，优先沿用 spike 已验证的 `pixi7 + pixi-spine` 路径；对 Pixi8 / Spine 4.3 路线保留清晰边界说明。
- 支持基础动作映射：`idle` / `thinking` / `speaking` / `error` / `sleep` / `tap` / `dragStart` / `dragEnd` 能落到模型 animation；缺失 animation 时有可解释 fallback。
- 支持 Spine 内置模型清单与运行参数配置；首批目标模型为用户已指定的 Ark-Models `models/` 下 14 个目录，模型清单、动作映射、scale / fit 参数不能写死在 UI 组件内部。

## 范围

### 包含

#### Avatar runtime 抽象

- 建立正式 avatar runtime 合约，使 pet 窗可以面向统一 runtime 控制当前 avatar。
- Runtime 至少能表达 backend kind、capabilities、mount / destroy 生命周期、phase 切换、一次性 action、bounds 或 anchor invalidate 能力。
- Live2D 路径需要继续保持现有行为；本需求只在必要范围内为它套上或适配到 runtime 合约。
- Spine runtime 需要从 spike 逻辑中抽出，成为可被正式 pet 窗消费的能力。

#### Spine pet window 接入

- Spine 模型可以作为 PIXI display object 或等价 child 挂入现有 pet `avatar-slot`。
- 保留现有 pet window 外层能力，包括：
  - PIXI slot 生命周期。
  - sprite bounds / visible bounds 上报。
  - alpha hit-test 与透明区穿透。
  - 拖拽与拖拽状态。
  - ActionBar 锚点跟随。
  - bubble 跟随。
- 不要求本期重做 pet window 外壳，不要求新增独立 Spine pet window。

#### 能力差异与状态映射

- Spine runtime 的 capabilities 明确标记：
  - `motions` / animation 能力可用。
  - `alphaHitTest` 可用或有 fallback。
  - `mouthParams`、`gazeParams`、`expressionParams` 当前不可用。
- Pet phase / action 与 Spine animation 的映射从模型配置读取，不写死在 `PetApp` 或 UI 控件里。
- 当目标 animation 缺失时，运行时应回退到模型默认 animation、idle-like animation 或首个可播放 animation，并在 dev 日志或状态中给出可解释信息。
- `sleep` 作为 avatar runtime action / phase 映射能力纳入配置；是否扩展当前 pet 状态机为正式 sleep 态，视技术方案确认，不在需求层强行要求。

#### Ark-Models / 内置模型支持

- 基于 `037-avatar-model-assets-and-catalog` 已落地的 avatar catalog 边界，支持 Spine 模型条目被 runtime 消费。
- 支持可配置的内置 Spine 模型清单，首批目标模型目录为：
  - `002_amiya`
  - `4202_haruka`
  - `140_whitew`
  - `103_angel`
  - `358_lisa`
  - `102_texas`
  - `263_skadi`
  - `003_kalts`
  - `1035_wisdel`
  - `4133_logos`
  - `2027_wang`
  - `2025_shu`
  - `4179_monstr`
  - `010_chen`
- 这些目录在 Ark-Models `models/` 下均为 `build_char_<id>.atlas` / `.png` / `.skel` 三件套，适合机械生成或配置成 Spine catalog entries。
- Ark-Models / Arknights 资源按当前非商用项目边界处理，保留 license / notice / distribution metadata，不把模型资源路径散写到 UI 组件中。

#### Spike 收敛

- `spine-spike` 页面可以继续作为调试 / playground，但正式 runtime 逻辑应下沉到 pet / avatar runtime 模块。
- Spike 页面如继续存在，应复用正式 runtime 或正式 catalog 中可复用的 helper，避免 action mapping、capabilities、资源路径长期双写。

### 不包含

- 不做完整模型切换 UI、设置中心重构、模型导入向导或模型市场。
- 不把 `033-pet-live2d-debugger` 改成通用 avatar debugger；它继续是 Live2D 专用调试器。
- 不负责 `avatar-model-assets-and-catalog` 的正式资源目录、catalog/schema、settings 持久化和 dev / packaged 路径策略；这些由 037 负责，本需求只消费或小幅补充 runtime 所需字段。
- 不要求完成所有 Ark-Models 角色接入；本需求首批覆盖用户已指定的 14 个目标模型目录，并保持后续可增补。
- 不实现 Live2D mouth / gaze / expression 在 Spine 上的等价参数能力。
- 不引入完整插件系统、远程 catalog 下载或资源自动更新。
- 不把 Ark-Models / Arknights 素材重新定义为商业可分发资源。

## 关键约束

- 现有 Live2D 默认模型 Hiyori 的用户可见行为不能退化。
- Pet window 的外层 slot / drag / anchor / bubble / passthrough 能力应尽量复用，不因 Spine 另起一套重复窗口 plumbing。
- Spine 3.8 主路径优先使用 spike 已验证的 `pixi7 + pixi-spine`；Pixi8 / Spine 4.3 路线只保留为边界说明或实验入口。
- 模型清单、动作映射、runtime mode、scale / fit 等必须从配置或 catalog entry 派生，不写死在 UI 组件内部。
- 需求与 037 的边界清楚：037 管资源地基和 settings 选择字段；本需求管 runtime backend 与 pet 窗接入。
- 如涉及用户可见桌宠窗口行为，需要在验收说明中说明是否做了真实桌面端视觉 / 交互验收；不能用普通浏览器预览冒充桌面验收结论。

## 关联上下文

- 探索文档：`docs/explorations/arknights-chibi-avatar-resources/README.md`
- 资源地基兄弟需求：`docs/requirements/037-avatar-model-assets-and-catalog/`
- 当前 Spine spike：
  - `frontend/src/pages/spine-spike/petAvatarRuntime.ts`
  - `frontend/src/pages/spine-spike/App.tsx`
- 当前 pet window：
  - `frontend/src/pages/pet/App.tsx`
  - `frontend/src/pages/pet/usePixiAvatarSlot.ts`
  - `frontend/src/pages/pet/usePetInteractions.ts`
  - `frontend/src/pages/pet/usePetPassthrough.ts`
- 当前 Live2D 路径：
  - `frontend/src/pet/usePetLive2D.ts`
  - `frontend/src/pet/MouthDriver.ts`
  - `frontend/src/pet/live2dConfig.ts`
- 当前 hit-test：
  - `frontend/src/pet/petAlphaHitTest.ts`
- 当前 avatar catalog：
  - `frontend/src/avatar-models/catalog.ts`
- 当前前端依赖：
  - `frontend/package.json`

## 验收标准

- [x] 项目中存在正式 avatar runtime 合约或等价边界，能表达 Live2D / Spine backend kind 与 capabilities。
- [x] Spine runtime 逻辑从 spike 页面下沉到正式模块；`PetApp` 不直接承载大段 Spine spike 页面逻辑。
- [x] Spine 模型可以进入现有 pet `avatar-slot`，并复用现有 drag、sprite bounds / visible bounds、ActionBar anchor、bubble follow 基础能力。
- [x] Spine runtime 支持 `idle` / `thinking` / `speaking` / `error` / `sleep` / `tap` / `dragStart` / `dragEnd` 的 configurable action mapping。
- [x] animation 缺失时有稳定 fallback，不因单个映射缺失导致 pet 窗崩溃或完全不可用。
- [x] Spine capabilities 明确标记 mouth / gaze / expression 参数能力不可用；Live2D 现有 mouth / gaze / expression 行为不退化。
- [x] Ark-Models Spine 3.8 主路径基于 `pixi7 + pixi-spine` 跑通；Pixi8 / Spine 4.3 路线在技术方案或代码边界中说明，不误作为本期主路径。
- [x] Spine 内置模型清单、资源、action mapping、scale / fit 参数由配置或 catalog 派生，不写死在 UI 组件内部；首批目标模型覆盖用户已指定的 14 个 Ark-Models `models/` 目录，并可后续增补。
- [x] 037 的 avatar catalog / settings 地基可被本需求 runtime 消费；本需求不重复定义完整资源目录和 settings 持久化策略。
- [x] 033 Live2D debugger 仍保持 Live2D 专用，不被改造成通用 avatar debugger。
- [x] 覆盖核心 runtime 映射 / fallback / capability 分支的自动化测试；必要时补充 pet 窗接入层测试或可重复 smoke。
- [x] `./scripts/check` 通过；若未做真实桌面视觉验收，交付说明必须写清原因与剩余风险。

## 开放问题

- 首批目标模型目录已确认，但展示名、默认 scale / fit、默认动作映射是否需要逐模型微调，需要在实现中根据实际 animation 列表和视觉效果确认。
- 当前 pet 状态机是否要正式扩展 `sleep` 态，还是仅在 avatar runtime action mapping 层支持 `sleep`，需要在设计阶段结合上游状态来源确认。
- Spine runtime 和 Live2D runtime 的统一合约是否应直接替代现有 `spriteRef` 交互链路，还是先用 adapter 保持 Live2D 路径局部不动，需要在设计阶段根据改动风险确定。
- 正式 pet 窗中选择 Spine 模型的入口是否先使用 dev / temporary catalog adapter，还是通过已有 settings `avatarModelId` 启用，需要结合 037 当前实现状态确认。

## 变更记录

| 日期 | 变更内容 | 影响范围 |
|------|---------|---------|
| 2026-06-30 | 初版确认：将 Spine spike 收敛为正式 avatar backend，接入 pet slot 外层能力，建立 Live2D / Spine capabilities 差异，支持 Ark-Models Spine 3.8、动作映射 fallback 与可配置内置 Spine 模型清单 | Phase 1 |
| 2026-06-30 | 补充首批目标内置 Spine 模型清单：`002_amiya`、`4202_haruka`、`140_whitew`、`103_angel`、`358_lisa`、`102_texas`、`263_skadi`、`003_kalts`、`1035_wisdel`、`4133_logos`、`2027_wang`、`2025_shu`、`4179_monstr`、`010_chen` | Phase 1 变更 |
