# 024 · 桌宠 B 类交互：视线跟随 + 点击触发 + 拖拽视觉反馈（pet live2d mouse reactions）

> Pet Live2D Mouse Reactions — B 类纯前端「活人感」基础包
>
> 在 [需求 018](../018-pet-live2d-state-and-lipsync/) 已落地的「Live2D 形象 + 4 态状态机（`idle` / `thinking` / `speaking` / `error`）+ Codex push 驱动 + lip-sync」基础上，叠加三件纯前端、不出进程、不动协议的鼠标输入反馈，把 Live2D 形象从「独立形象在场」推到「会回应你的小生命」。本期对应 [exploration · pet-liveliness-and-proactive-events](../../explorations/pet-liveliness-and-proactive-events/README.md) §2 / §4 的 **B 类**（桌面端 UI 输入，直接前端消费），不掺 A / C / D 类反向上报和策略 gate。

---

## 状态

<!-- 草稿（Draft） | 已确认（Confirmed） -->
已确认（Confirmed）

---

## 1. 背景

### 1.1 现状

- [需求 018](../018-pet-live2d-state-and-lipsync/) 已落地：Live2D Hiyori 模型在 PetOverlay 内 avatar-slot 渲染、4 态状态机（`idle` / `thinking` / `speaking` / `error`）接 015 push 通道、`MouthDriver` 文本 cadence 嘴动。
- [exploration · pet-liveliness-and-proactive-events](../../explorations/pet-liveliness-and-proactive-events/README.md) §1 已 ground 体感问题：「桌宠形象在场但跟我没互动」—— Live2D 看起来活了，但只在 agent 输出时动嘴 / 切态，不响应用户操作。
- exploration §2 / §4 把「活人感」杠杆分四类：**B 类**（桌面端 UI 输入，直接前端消费，不入 agent inbox）是 ROI 最高、延迟最低、最快出感受的方向；A / C / D 类（agent 主动事件 / UI 反向上报 / OS sensing）都要先在 bridge 铺反向 HTTP + 策略 gate，本期不掺。
- exploration §6 节奏第 1 项明确「B 类是最快出活人感的一波」；exploration §7 留了「Hiyori 自带 motion / expression 是否够细分 head / body 区」作为「立 feature 时评估」的不确定项。

### 1.2 这次要做什么

在 PetOverlay 那一个 Live2D 实例上叠三件鼠标输入反馈：

- **视线 / 头跟随光标**：全屏 mousemove → 低通滤波 → 驱动 `ParamAngleX` / `ParamAngleY` / `ParamAngleZ` + `ParamEyeBallX` / `ParamEyeBallY`；光标距桌宠超阈值后角度回归中立（避免在屏幕角落不停「扫视」反成骚扰）。
- **点击 head / body 触发 motion + 临时反应态**：在 018 已有 4 态机上叠加临时反应态（暂名 `reacting`，**不进 4 态机命名空间**，作为并行 transient 层），点击触发后播 motion + 切表情、motion 自然结束后回归触发前的态；motion 进行中忽略后续点击，避免连点鬼畜。
- **拖拽时的 Live2D 视觉反馈**：鼠标在桌宠上按下拖动时，Live2D 角度叠加偏移 + 表情切换（「被拎起来」），**窗口位置不动**（窗口拖移留作未来需求，不混进本期）；松开后角度 / 表情平滑回归原态。

### 1.3 降级路径（明示）

exploration §7 列了一个本期 Day-1 必须 audit 的不确定项：Hiyori 自带 motion / expression / hit area 是否够把 head 区和 body 区分别匹配到不同 motion。**若 Day-1 audit 通过**（资源够），按 §4.2 完整版做；**若不通过**（资源不够），本期降级为「tap 任意区触发一个通用 motion，不区分区域」，AC-3 同步收缩为统一 motion 验收。此降级条件写进本节正文而非藏在变更记录，是为了把这个已知不确定项摆明。

### 1.4 与 018 / 019 的关系

- **与 018 关系**：018 的 4 态机 + Live2D 加载渲染 + `MouthDriver` 是基础底座。本期叠加临时反应态 `reacting`（不进 4 态机命名空间，作并行 transient 层覆盖）+ 直接读写 Live2D `ParamAngle` / `ParamEyeBall` 参数（不经状态机）+ 拖拽期间临时表情参数偏移。018 状态机 transition table / push event 驱动 / lip-sync 链路全部不动。
- **与 019 关系**：019 ActionBar / 设置壳已就位，本期三件事都**默认开**，**不引入设置开关**（避免设置面板膨胀）；若实际使用反馈「扫视太骚扰」需要关闭开关，留作后续需求。

### 1.5 跨平台定位

沿 [ADR 0002](../../decisions/0002-incubation-tech-stack/README.md) §3.1 + 17a / 18 既有 plumbing：macOS + Win first-class，Linux 不在范围。本期改动集中在前端 PIXI / Live2D 参数 / mousemove 监听层，不引入新 `cfg-gate`。

---

## 2. 本期范围（In Scope）

| 模块 | 目标 |
| --- | --- |
| **视线 / 头跟随光标** | 全屏 mousemove 数据 → 低通滤波 → 驱动 Live2D `ParamAngleX/Y/Z` + `ParamEyeBallX/Y`；光标距桌宠超阈值时角度回归中立（具体阈值 + 滤波时间常数由 design 决定） |
| **点击 head / body 触发反应** | 在 018 4 态机上叠加并行临时反应态（暂名 `reacting`），点击触发后播 motion + 切表情、motion 自然结束后回归触发前的态；motion 进行中忽略后续点击 |
| **拖拽视觉反馈** | 在桌宠区域 mouse down + 移动超阈值时，Live2D 角度参数叠加偏移 + 表情切换「被拎起来」；**窗口位置不动**；mouse up 后平滑回归 |
| **状态优先级与互斥** | 当前思路 `speaking > tap > listening`，拖拽期间暂关视线跟随；**不进 AC 锁定**，由 design.md 钉初版、实现期间可调整 |
| **Day-1 Hiyori 资源 audit** | progress.md 第一项 audit Hiyori 自带 motion / expression / hit area 资源清单，产出「是否够细分 head / body 区」判断；不够则触发降级路径（§1.3 / §4.5） |
| **既有路径回归** | 015 全 9 AC + 016 全 12 AC + 017 全 12 AC + 018 全 12 AC 在本期完成后全部回归通过 |

---

## 3. 非目标（Out of Scope）

以下本期明确**不做**：

- **窗口拖移**：拖动桌宠改 PetOverlay 窗口位置（涉及窗口管理 + 跨平台坐标处理）留作后续需求；本期拖拽只指 Live2D 形象在窗口内的视觉反馈。
- **A 类 / C 类 / D 类事件**：agent 主动事件源（`idle_chat` / `cron:morning`）、UI 反向上报 inbox（`interaction:tap_n` / `interaction:long_idle`）、OS sensing（`system:app_launched` / `system:wake_from_lock`）全部不在本期（对应 exploration §6 节奏第 2-3 项）。
- **策略 gate / quiet hours / 节流**：exploration §6.4 明确「策略 gate 应被第一个真正反向上报事件逼出来，本期不掺空骨架」。
- **bedtime issue 006 修复 / `enable_bedtime` flag 默认值**：exploration §6 末尾说明由用户单独处理，不进本期。
- **chat / bubble 窗口**：无 Live2D 实例，本期不涉及（issue 011 属另一条线）。
- **设置面板开关**：三件事默认开；未来如需开关再立（避免设置面板膨胀）。
- **B 类种子库扩展**：长按 / mouseleave / 边缘「踩墙」 / 双击（exploration §4 B 类种子库内）不在本期，留余地。
- **本期前置 spike**：Day-1 audit 不立 spike，直接在 progress.md 第一项跑完产出判断；audit 若发现需要更深入实验再回头评估。

---

## 4. 核心需求详述

R- 级需求点只讲「做什么 / 做到什么效果」；具体滤波算法、阈值数值、临时反应态实现方式、与 17a drag plumbing 的隔离方式等由 [`design.md`](./design.md) 决定。

### 4.1 视线 / 头跟随光标

- **R-4.1.1 输入源**：全屏 mousemove（不限定 PetOverlay 窗口内）；具体如何获取全屏光标 + 与 17a 既有 cursor passthrough plumbing 的关系由 design 决定（可能复用 17a 60Hz Rust cursor channel，也可能新增 frontend 内 mousemove listener）。
- **R-4.1.2 参数驱动**：光标位置 → 低通滤波 → Live2D `ParamAngleX` / `ParamAngleY` / `ParamAngleZ` + `ParamEyeBallX` / `ParamEyeBallY`；具体滤波算法 / 时间常数由 design 决定。
- **R-4.1.3 距离阈值归零**：光标距桌宠超过阈值（具体距离由 design 决定）时，角度 / 眼球参数平滑回归中立（0±ε），避免在屏幕角落不停「扫视」骚扰用户工作。
- **R-4.1.4 状态门控**：拖拽期间暂关视线跟随（避免抢镜，沿 §4.4）；其他态默认启用。

### 4.2 点击 head / body 触发 motion + 临时反应态

- **R-4.2.1 hit 区分**：点击触发依赖 Live2D hit-test 划 head / body 区（具体 hit area 由 design 决定，以 Hiyori 自带 hit area 为准）；Day-1 audit 不通过则降级为统一 hit 区（沿 §1.3 / §4.5）。
- **R-4.2.2 临时反应态**：点击触发后进入临时反应态（暂名 `reacting`，**不进 018 4 态机命名空间**，作为并行 transient 层覆盖）；motion 自然结束后回归触发前的态；若期间 push event 触发态切换（如新一轮 speaking 进入），让 push event 抢镜（优先级沿 §4.4）。
- **R-4.2.3 连点处理**：反应态进行中（motion 未结束）忽略后续点击；motion 结束后才接受新点击，避免连点洗 motion 队列出鬼畜。
- **R-4.2.4 motion / expression 选择**：具体哪些 motion / expression 对应 head 区 / body 区由 design 决定；若 audit 不通过统一一个 motion 即可（沿 §4.5）。

### 4.3 拖拽视觉反馈

- **R-4.3.1 触发**：mouse down 在桌宠 alpha hit-test 区内 + 移动超阈值 → 进入拖拽视觉反馈；mouse up 退出。
- **R-4.3.2 视觉表现**：Live2D 角度参数叠加偏移（模拟「被拎起来」） + 切换表情（「惊讶 / 摇晃」类）；**窗口位置不动**（与 17a 既有 drag plumbing 的关系由 design 决定如何隔离，可能需要让 17a `update_sprite_pos` / `emitSpritePos` 在本期视觉反馈模式下不真的移窗口）。
- **R-4.3.3 释放回归**：mouse up 后 Live2D 角度 / 表情平滑回归原态。
- **R-4.3.4 与视线跟随互斥**：拖拽期间视线跟随暂关（沿 §4.1.4）。

### 4.4 状态优先级与互斥

- **R-4.4.1 当前思路**：`speaking > tap (reacting) > listening`；具体抢镜规则（如新 push event 进入时正在进行的 motion 是中断还是淡出）由 design 决定。
- **R-4.4.2 拖拽期间视线跟随暂关**（沿 §4.1.4 / §4.3.4）。
- **R-4.4.3 优先级不进 AC 锁定**：本节视为「未完全确定的产品规则」，design.md 钉初版、实现期间可调整；AC 只验「出现冲突时不崩、不卡死」（沿 §6 AC-5），不验具体优先级。

### 4.5 Day-1 Hiyori 资源 audit + 降级路径

- **R-4.5.1 audit 产出**：progress.md 第一项 audit Hiyori 自带 motion / expression / hit area 资源清单，**判断是否够细分 head / body 区**；audit 报告写进 progress.md 实现日志，不单独立文件。
- **R-4.5.2 通过条件**：Hiyori 至少有 2 个明显区分的 hit area（head + body 或等效命名）+ 至少 2 套对应的 motion / expression 资源可指派。
- **R-4.5.3 降级路径**：不通过则本期 R-4.2.1 / R-4.2.4 / AC-3 降级为「点击桌宠任意区都触发同一通用 motion」；触发降级时在 progress.md 实现日志 + 本文档变更记录登记。

### 4.6 既有路径回归

- **R-4.6.1 015 全 9 AC 仍然成立**。
- **R-4.6.2 016 全 12 AC 仍然成立**。
- **R-4.6.3 017 全 12 AC 仍然成立**。
- **R-4.6.4 018 全 12 AC 仍然成立**（状态机 4 态切换 / push event 驱动 / lip-sync 文本 cadence 节奏 / `MouthDriver` 接口扩展位均不退化）。
- **R-4.6.5 既有代码改动最小化**：018 状态机 / push 订阅器 / `MouthDriver` 接口 / `usePetStateStore` 不动；本期改动集中在新增 mousemove / pointerdown 监听 + Live2D 参数直接读写 + 临时反应态层 + 与 17a drag plumbing 的隔离（具体由 design 决定）。

---

## 5. 使用约束

- **沿用 018 设计的 4 态机命名空间**：临时反应态 `reacting` **不进**4 态机，作并行 transient 层。
- **不引入新 cfg-gate**：本期跨平台行为一致，不需要新 `#[cfg(target_os = "...")]`。
- **跨平台脚本约定**：本期如新增 dev / debug 脚本按 [`cross-platform-dev`](../../../.cursor/rules/cross-platform-dev.mdc) 双端落地（`run.sh` + `run.ps1`），并在 `scripts/README.md` 登记。
- **frontend-ui-conventions**：本期新增 hook / store 沿 [`frontend-ui-conventions`](../../../.cursor/rules/frontend-ui-conventions.mdc)。
- **不引入新依赖**：Live2D 库 / Cubism Core JS 沿 018 既有依赖（`easy-live2d@^0.4.4`），本期不引入新前端 / Rust 依赖。
- **真实 LLM 调用授权**：本期不需要触发新维度 LLM 调用；AC 跑通如经 015 / 014 push 通道触发 LLM 沿既有授权流程。

---

## 6. 验收标准

本节 AC 分**机制可观测层**（手测 / 单测可盖）+ **主观体感层**（用户手验）。机制层验「参数变化正确发生 / 不崩 / 既有路径不退化」；主观层验「感受上是不是真有『活人感』」。

### 6.1 机制可观测层

- **AC-1 Hiyori audit 产出**：progress.md 实现日志含 Hiyori motion / expression / hit area 清单 + 「是否够细分 head / body 区」判断 + 触发的是哪条路径（完整 §4.2 / 降级 §4.5.3）。
- **AC-2 视线跟随**：鼠标从屏幕一角缓慢扫到对角，Live2D `ParamAngleY` / `ParamEyeBallX` 参数连续滑动无阶跃；光标停留在桌宠正中，参数稳定在中立（0±ε）；光标移到距桌宠超阈值的位置后，参数平滑归零。
- **AC-3 点击 motion 触发**：
  - **审计通过路径**：点击桌宠 head 区 → 对应 head motion 触发；点击 body 区 → 对应 body motion 触发；motion 进行中再点 → 不重复触发；motion 自然结束后桌宠回到触发前的态。
  - **降级路径**：点击桌宠任意区都触发同一通用 motion，其余规则同上。
  - AC 验收时按 audit 实际结果走对应一路。
- **AC-4 拖拽视觉反馈**：在桌宠 alpha hit-test 区 mouse down + 移动 → Live2D 角度参数偏移 + 表情切换；mouse up → 角度 / 表情平滑回归原态；**PetOverlay 窗口位置不变**（不真的移窗）。
- **AC-5 状态冲突不崩**：speaking 态进行中点击 → speaking 抢镜（tap 不触发或被压；具体表现由 design 钉，AC 只验不崩、不卡死）；拖拽中视线跟随暂关、不与拖拽抢镜；以上各组合在 macOS + Win 跑通，无 Live2D 渲染异常、无控制台 error、FPS 不长时间掉到 0。
- **AC-6 既有路径零退化**：015 全 9 AC / 016 全 12 AC / 017 全 12 AC / 018 全 12 AC（macOS + Win）在本期完成后全部回归通过。
- **AC-7 跨平台覆盖**：AC-2 / AC-3 / AC-4 / AC-5 在 macOS + Win first-class 双端同期跑通，不接受 Win fallback。
- **AC-8 cross-build 全绿**：`./scripts/check`（lint + typecheck + 单测）三平台全绿；`cargo build` macOS / Win 都通；`pnpm test` 全过（含新增 hook / 滤波 / 临时反应态单测 + 既有 015 / 016 / 17a / 018 单测全部维持）。

### 6.2 主观体感层（用户手验）

- **AC-9 主观体感 sign-off**：用户（本仓 owner）开 PetOverlay 用 30 秒以上，主观判断三件事是否达到「真的在看我 / 真的响应我点击 / 真的像被拎起来」的体感。验收通过 = 用户明确说 OK。
  - 此条不可自动化验，sign-off 由用户单独给出。
  - 不达感受门槛 → 触发 design / 参数调优，不算 AC 不通过（参数微调属于实现期细节，不动 AC）。

---

## 7. 已知风险与监测项（不阻塞验收 / 不进 AC）

| # | 风险 / 监测项 | 来源 | 处理 |
|---|---|---|---|
| 1 | Hiyori 自带 motion / expression / hit area 不够细分 head / body 区 | exploration §7 | Day-1 audit；不通过则降级路径（§4.5.3），本期 AC-3 走降级一路 |
| 2 | 全屏 mousemove 监听对性能 / 节流的影响 | 新增 listener | design 决定节流策略（60Hz 上限 / 与 17a cursor channel 复用 / 独立监听）；AC 不卡 FPS，实现期监测 |
| 3 | 状态优先级 `speaking > tap > listening` 的产品观感 | 用户拍板「先这么写，可能会改」 | 本期不锁进 AC（§4.4.3）；design.md 钉初版、实现期可调整；若产品观感不对，改 design.md + progress.md 实现日志，不动 requirement.md AC |
| 4 | 拖拽视觉反馈与 17a 既有 drag plumbing（`update_sprite_pos` / `emitSpritePos`）冲突 | 17a drag 是真的移 sprite 世界坐标 | design 决定如何隔离「视觉反馈 only」与「真实移 sprite」；若发现 17a drag 数据流必须改，回头评估是否升级 17a/18 接缝点表 |
| 5 | 视线跟随的距离阈值 / 滤波时间常数初值 | 纯参数调优 | design.md 钉初值；主观体感不对实现期调，不阻塞 AC |
| 6 | macOS NSPanel 30fps cap 在快速 mousemove 跟随的感官 | ADR 0004 §4.2 + 17a 既有 trade-off | 沿用接受；若 30fps 看起来明显卡顿，立专项 spike |
| 7 | 临时反应态 `reacting` 与 018 4 态机的边界 | 本期新增并行 transient 层 | design 决定具体实现（4 态机外的独立 store / Live2D 参数直写）；保持 018 4 态机 API 不变 |

---

## 8. 变更记录

| 日期 | 变更内容 | 影响范围 |
|------|---------|---------|
| 2026-06-19 | 立项初稿，基于 [exploration · pet-liveliness-and-proactive-events](../../explorations/pet-liveliness-and-proactive-events/README.md) §2.2 + §4 B 类 + §6 节奏第 1 项 锁定 B 类纯前端三件事；不掺 A / C / D + 策略 gate + bedtime / `enable_bedtime` flag。 | 全文档初稿 |

---

## 文档元信息

- **状态**：已确认（Confirmed）
- **创建时间**：2026-06-19
- **确认时间**：2026-06-19
- **关联探索**：[exploration · pet-liveliness-and-proactive-events](../../explorations/pet-liveliness-and-proactive-events/README.md)
- **关联决策**：[ADR 0004 · 桌宠承载形态与渲染路径](../../decisions/0004-pet-overlay-route/README.md)
- **关联需求**：
  - [需求 015](../015-desktop-pet-bubble-and-conversation-owner/)（push 通道桌面端消费 · 主动轮事件流参考）
  - [需求 017 / 17a](../017-pet-overlay-form-switch/)（avatar-slot Container 底座 + cursor passthrough plumbing）
  - [需求 018](../018-pet-live2d-state-and-lipsync/)（Live2D 形象 + 4 态机 + `MouthDriver` 基础底座）
- **下期承接候选**（沿 exploration §6 节奏）：窗口拖移 / B 类种子库扩展（长按 / mouseleave / 双击 / 边缘踩墙）/ A 类 `idle_chat` / D 类 OS sensing + 反向 HTTP 入口 + 策略 gate
- **下一步**：本文档确认后撰写同目录 [`design.md`](./design.md)（技术方案）
