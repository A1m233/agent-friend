# 033 · 桌宠 Live2D 调试器（pet live2d debugger）

> Pet Live2D Debugger — dev-only motion / feedback 调试窗口
>
> 基于 [exploration · pet-live2d-debugger](../../explorations/pet-live2d-debugger/README.md)，为当前桌宠 Live2D 形象提供一个开发期调试入口：不用改配置 / 重启 / 反复手敲代码，就能直接播放模型 motion、触发现有项目级点击反馈，并记录最近调试命令，帮助人工观察 Hiyori motion 的实际语义。首版服务当前 Hiyori，但调试器结构需为后续模型切换 / 模型试点 / 参数源扩展保留清晰接缝。

---

## 状态

<!-- 草稿（Draft） | 已确认（Confirmed） -->
已确认（Confirmed）

---

## 1. 背景

### 1.1 现状

- [需求 018](../018-pet-live2d-state-and-lipsync/) 已落地 Hiyori Live2D 形象、`easy-live2d` 接入、`PET_LIVE2D_CONFIG`、4 态状态机与文本 cadence lip-sync。
- [需求 024](../024-pet-live2d-mouse-reactions/) 已把视线跟随、点击反馈、拖拽反馈叠到同一个 Live2D 实例上；当前点击反馈由两层组成：模型 motion（如 `Idle[4] / Hiyori_m06`）+ 项目级 `TapReactionSource` 参数叠加（脸红 / 嘴型等）。
- Hiyori 的 `model3.json` 只暴露 motion group 与 index / 文件路径，没有官方逐条语义说明。单看 `.motion3.json` 参数曲线无法可靠判断“这个动作体感上是什么”。
- 当前要试一个 motion，通常要改 `live2dConfig.ts` / 触发交互 / 观察，再反复调整；效率低，也不利于后续给 motion 建立人工认知。

### 1.2 这次要做什么

本期交付一个 **dev-only Live2D 调试器**：

- 从桌宠 ActionBar 的 dev-only 区域打开独立调试窗口。
- 调试窗口能查看当前模型 motion catalog（group / index / 文件名或可读标识），选择 motion group、motion index、priority 后，让 pet 窗口中的真实 Live2D sprite 执行 `startMotion`。
- 调试窗口提供快捷触发：等价真实点击桌宠的“点击反馈”、回到 idle、播放 idle 随机动作。
- 调试窗口提供一个“参数反馈 only”的隔离观察入口：只触发项目级点击反馈参数源，不播放模型 motion，用于区分“模型动作本身”和“项目叠加参数”的体感来源。
- 调试窗口记录最近触发过的命令与结果，方便人工对照观察。
- release build 不暴露用户入口，避免把开发工具误带到生产可见路径。

### 1.3 扩展性定位

本期 MVP 只要求服务当前内置 Hiyori 模型，但设计不能把调试器写死为 Hiyori-only：

- motion catalog 的数据结构要能表达“当前模型”的 group / index / 文件标识，而不是在 UI 里散落 Hiyori 专属枚举。
- 调试命令要有稳定 command schema，后续能扩展到模型切换、模型 trial、参数源触发、表情 / expression 调试等能力。
- UI 分区要保持“Motion / 快捷触发 / 参数源 / 日志”这类可扩展结构，后续新增模型切换或参数编辑时不需要推翻首版窗口。
- 本期不实现模型切换，但不做会阻断模型切换试点的设计。

---

## 2. 本期范围（In Scope）

| 模块 | 目标 |
| --- | --- |
| **ActionBar dev-only 入口** | 在桌宠 ActionBar 的 dev-only 分页中新增“Live2D 调试器”入口；release build 用户不可触达。 |
| **独立 dev-only 调试窗口** | 新增一个面向开发者的独立窗口，用于 motion 播放、快捷触发与日志观察；窗口可关闭后再次从 ActionBar 打开。 |
| **motion catalog 展示** | 展示当前模型可用 motion group / index / 文件标识；首版可从当前配置 / 模型文件 / pet 窗口查询中任选实现路径，具体由 design 决定。 |
| **motion 播放命令** | 调试窗口选择 group / index / priority 后，pet 窗口里的真实 Live2D sprite 执行对应 `startMotion`；命令失败时窗口可见错误，不导致 pet 窗崩溃。 |
| **快捷触发** | 支持一键触发现有点击反馈、回到 idle、播放 idle 随机动作；点击反馈应等价于真实点击桌宠的项目行为。 |
| **参数反馈隔离观察** | 支持只触发项目级点击反馈参数源、不播放模型 motion，用于观察 `TapReactionSource` 等参数叠加效果。 |
| **最近命令日志** | 调试窗口记录最近触发过的命令、结果与错误，便于人工对照动作。 |
| **扩展接缝** | motion catalog / command schema / UI 分区为后续模型切换、模型 trial、参数源扩展留接缝；本期不实现这些后续能力。 |
| **既有路径回归** | 桌宠形象加载、ActionBar 既有按钮、chat / bubble / settings / memory inspector 等既有路径不退化。 |

---

## 3. 非目标（Out of Scope）

以下本期明确不做：

- **自动识别 motion 语义**：不根据 `.motion3.json` 曲线生成“挥手 / 点头 / 害羞”等解释。
- **动作标注保存**：不保存人工标注、不做 motion 标签库、不写入用户数据或项目配置。
- **完整参数编辑器**：不做任意 Live2D 参数滑杆 / keyframe 编辑 / 曲线编辑。
- **模型切换 / 模型市场 / 模型导入**：本期只为后续保留接缝，不实现模型选择、导入或切换。
- **生产用户入口**：调试器是开发期工具，不作为产品功能暴露。
- **独立离线预览器**：首版调试 pet 窗中的真实 Live2D 实例，不另起一个与桌宠状态脱钩的离线 Live2D 预览环境。
- **LLM / agent 行为调试**：不触发真实 LLM，不调 agent 状态机，不做 push event 造数；只调 Live2D motion 与前端参数反馈。

---

## 4. 核心需求详述

### 4.1 dev-only 入口与窗口

- **R-4.1.1 ActionBar 入口**：桌宠 ActionBar 的 dev-only 区域新增“Live2D 调试器”按钮；入口应与现有“记忆面板 / 注入测试气泡”等 dev 工具处于同一层级。
- **R-4.1.2 独立窗口**：点击入口后打开独立调试窗口；窗口关闭时隐藏或关闭的具体语义由 design 决定，但应支持再次打开。
- **R-4.1.3 release 不暴露**：release build 用户不可通过 ActionBar、菜单或普通路由触达调试器；具体 cfg / env gate 由 design 决定。

### 4.2 motion catalog 与播放

- **R-4.2.1 catalog 展示**：调试窗口展示当前模型的 motion group、motion index、文件名或等效可读标识；Hiyori 场景下至少能看出 `Idle` / `IdleLoop` / tap 相关 group 中每个 index 对应的候选动作。
- **R-4.2.2 catalog 来源**：catalog 可由 pet 窗口基于当前 Live2D sprite / `PET_LIVE2D_CONFIG` / `model3.json` 查询后回传，也可由调试窗口静态读取模型文件；具体路径由 design 决定，但输出结构需为“当前模型 catalog”，不写死 UI 枚举。
- **R-4.2.3 motion 播放**：开发者选择 group、index、priority 后触发播放；pet 窗真实 Live2D sprite 执行 `startMotion`，而不是调试窗口里的假实例。
- **R-4.2.4 priority 可控**：调试器允许选择或切换 priority；默认值由 design 决定，但要能覆盖当前 idle 以便立即观察动作。
- **R-4.2.5 错误可见**：group 不存在、index 越界、sprite 未加载、播放失败等情况在调试窗口日志中可见；pet 窗不崩溃、不进入不可恢复状态。

### 4.3 快捷触发

- **R-4.3.1 点击反馈**：提供“一键点击反馈”，行为等价于真实点击桌宠时的当前项目行为：按现有交互逻辑触发 tap motion 与 `TapReactionSource` 参数反馈。
- **R-4.3.2 参数反馈 only**：提供“参数反馈 only”入口，只触发项目级点击反馈参数源，不播放模型 motion，用于隔离观察参数叠加效果。
- **R-4.3.3 回 idle**：提供回到 idle / 播放默认 idle 的快捷入口，便于从某个调试动作恢复到基线状态继续观察。
- **R-4.3.4 idle 随机动作**：提供播放 idle 随机动作的快捷入口，便于快速扫一遍 idle 候选动作池。

### 4.4 命令通道与状态隔离

- **R-4.4.1 pet 窗执行命令**：调试窗口发出的 motion / 快捷命令由 pet 窗执行，确保观察对象是桌面上真实 Live2D 实例。
- **R-4.4.2 不污染生产状态**：调试命令不写入用户设置、不改持久化配置、不影响 release 用户路径。
- **R-4.4.3 与现有状态机并存**：调试期间允许覆盖当前 motion 用于观察，但不破坏 018 状态机、024 参数源与 lip-sync 的基本生命周期；冲突处理细节由 design 决定。
- **R-4.4.4 命令 schema 可扩展**：命令结构应能继续扩展到模型 trial、参数源触发、expression / parameter 调试等后续能力，而不是为当前几个按钮写一组不可组合的临时事件名。

### 4.5 UI 与日志

- **R-4.5.1 窗口结构**：调试窗口首版至少包含 Motion 区、快捷区、参数源区、日志区；布局保持窄而直观，服务快速操作与观察。
- **R-4.5.2 最近命令日志**：日志显示最近触发过的命令、结果、错误信息与时间顺序；仅需内存态，不持久化。
- **R-4.5.3 视觉一致性**：UI 复用项目现有 `frontend/src/components/ui/` 组件与设计 token，不散写原生交互件，不硬编码视觉 token。

### 4.6 既有路径回归

- **R-4.6.1 ActionBar 既有按钮不退化**：打开对话、语音通话、隐藏桌宠、设置、IM 接入、记忆面板、注入测试气泡等既有 ActionBar 按钮行为不变。
- **R-4.6.2 pet 形象不退化**：Live2D 加载、idle、点击反馈、视线跟随、拖拽反馈、lip-sync 不因调试器入口存在而退化。
- **R-4.6.3 多窗口既有路径不退化**：chat / bubble / settings / memory inspector / voice-call 窗口既有显隐与聚焦路径不变。

---

## 5. 使用约束

- **dev-only 工具**：本期所有入口以开发期调试为目的；release 用户不可触达。
- **不新增 LLM 调用**：本期不需要真实 LLM 调用；如验收过程中另行触发聊天路径，需沿既有授权流程。
- **不引入新前端依赖**：优先复用现有 React / Tauri / shadcn UI / lucide / easy-live2d 能力；如 design 阶段认为需要新依赖，必须单独说明理由。
- **跨平台脚本约定**：若新增可重复运行的 dev / debug 脚本，按 [`cross-platform-dev`](../../../.cursor/rules/cross-platform-dev.mdc) 双端落地并更新 `scripts/README.md`；本期优先不新增脚本。
- **前端 UI 规范**：新增 UI 遵守 [`frontend-ui-conventions`](../../../.cursor/rules/frontend-ui-conventions.mdc)，通用交互件复用 `src/components/ui/`，视觉 token 走 CSS 变量。
- **桌面视觉验收**：本期会影响 Tauri 桌面窗口 UI，验收时按 [`frontend-visual-verification`](../../../.cursor/rules/frontend-visual-verification.mdc) 优先观察真实桌面端。

---

## 6. 验收标准

### 6.1 机制层

- **AC-1 dev 入口可打开**：dev build 中，桌宠 ActionBar dev 分页可见 Live2D 调试器入口；点击后打开调试窗口；关闭后可再次打开。
- **AC-2 release 不可触达**：release build 中，普通用户路径看不到 Live2D 调试器入口，普通菜单 / 窗口入口不可触达该工具。
- **AC-3 motion catalog 可用**：调试窗口能展示当前模型 motion catalog，至少包含 group、index、文件标识或等效可读信息。
- **AC-4 motion 播放可控**：选择任一合法 group / index / priority 后，pet 窗真实 Live2D sprite 播放对应 motion；不需要改代码或重启。
- **AC-5 快捷触发可用**：点击反馈、参数反馈 only、回 idle、idle 随机动作均可触发，并能在 pet 窗看到对应效果。
- **AC-6 错误可见且不崩**：非法 group / index、sprite 未 ready、播放失败等错误显示在日志区；pet 窗与调试窗口不崩溃。
- **AC-7 日志可对照**：调试窗口展示最近命令记录，能帮助人工按顺序对照“刚才播放了哪个动作”。
- **AC-8 扩展接缝可读**：code review 能看到 motion catalog / command schema / UI 分区不是 Hiyori-only 临时写法，后续模型切换或参数源扩展有明确接入点。
- **AC-9 既有路径零退化**：ActionBar 既有按钮、pet 形象交互、chat / bubble / settings / memory inspector / voice-call 窗口路径不退化。
- **AC-10 门禁通过**：`./scripts/check` 全绿；前端相关新增逻辑有必要单测覆盖（至少 catalog / command reducer 或等效纯逻辑）。

### 6.2 手动验收

- **AC-11 桌面端视觉验收**：在真实 Tauri 桌面端观察调试窗口与 ActionBar 入口，确认布局不遮挡、不溢出、按钮 / 输入 / 日志可读。
- **AC-12 人工观察有效**：用户能用调试器连续试多个 Hiyori motion，并明确判断它比“改配置重启观察”的工作流更高效。

---

## 7. 已知风险与监测项（不阻塞验收 / 不进 AC）

| # | 风险 / 监测项 | 来源 | 处理 |
|---|---|---|---|
| 1 | `easy-live2d` 是否能直接反查完整 motions | 库 API 可能不暴露完整 catalog | design 阶段决定从 sprite / model3.json / config 中取 catalog；AC 只要求结果可用 |
| 2 | 调试 motion 与 018 / 024 状态机或参数源抢写 | 调试命令会覆盖当前 motion | design 阶段定义冲突处理；调试器本身是 dev-only，允许短时覆盖但不能破坏生命周期 |
| 3 | release gate 粒度 | 入口隐藏 vs command / window cfg-gate | design 阶段按 Tauri 结构决定；需求只锁“生产用户不可触达” |
| 4 | 后续模型切换试点会扩大 UI | 用户已提出未来可能在这里试模型切换 | 本期只留接缝；若模型切换成为明确交付，另立需求或回溯 design |
| 5 | 桌面端窗口视觉受 Tauri WebView 差异影响 | 新增桌面可见窗口 | 验收时按真实桌面端观察，不只看 Vite web |

---

## 8. 变更记录

| 日期 | 变更内容 | 影响范围 |
|------|---------|---------|
| 2026-06-27 | 立项初稿，基于 `pet-live2d-debugger` exploration 收敛为 dev-only motion / feedback 调试器；加入用户强调的扩展性约束：首版服务 Hiyori，但 catalog / command schema / UI 分区为后续模型切换与模型试点留接缝。 | 全文档初稿 |

---

## 文档元信息

- **状态**：已确认（Confirmed）
- **创建时间**：2026-06-27
- **确认时间**：2026-06-27
- **关联探索**：[exploration · pet-live2d-debugger](../../explorations/pet-live2d-debugger/README.md)
- **关联需求**：
  - [需求 018](../018-pet-live2d-state-and-lipsync/)（Live2D 形象 + `PET_LIVE2D_CONFIG` + 4 态机 + lip-sync）
  - [需求 019](../019-pet-actionbar-rework-and-settings-shell/)（ActionBar 与设置壳）
  - [需求 024](../024-pet-live2d-mouse-reactions/)（点击反馈 / 参数源 / motion 调参上下文）
- **下一步**：本文档确认后撰写同目录 [`design.md`](./design.md)（技术方案）
