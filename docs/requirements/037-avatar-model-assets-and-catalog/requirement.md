# 037 · Avatar Model Assets and Catalog

## 状态

CONFIRMED

## 背景

当前桌宠形象已经有一条 Live2D 主路径，默认模型 Hiyori 放在 `frontend/public/live2d-models/hiyori/`，`frontend/src/pet/live2dConfig.ts` 直接以 public URL 指向 `Hiyori.model3.json`。并行探索里，Spine 小人路线也已经出现真实资源样例与 spike 页面：

- `frontend/public/spine-spike-fixtures/ark-amiya/`：仓库内置的 Arknights / Ark-Models 非商用 spike fixture。
- `frontend/public/spine-spike-local/`：开发者本地试验 Spine 资源目录，当前用于 spike 替换资源。
- `frontend/src/pages/spine-spike/`：独立 Spine runtime spike 页面，仍使用 public URL 与 query 参数加载 skeleton / atlas。

这些路径适合早期验证，但不适合作为正式桌面产品的运行时模型资产管理方式。后续桌面端需要支持切换不同 avatar 模型，包括现有 Live2D 与未来 Spine / Ark-Models 方向；模型文件体积较大、授权边界不同、来源可能是内置或用户本地导入，不应该长期把运行时资产散落在 repo `public/` 并让业务代码直接拼路径。

项目现行路径规则要求：Tauri / frontend 壳的路径入口统一走 `frontend/src-tauri/src/paths.rs`，不要在各模块散写平台路径、bundle id 或 OS 目录字面量。028 已经建立 settings store 与跨窗口同步能力，适合保存轻量用户偏好；但 settings 不应承载模型大资源、散路径或完整资源 manifest。

## 目标

本需求要先打牢 avatar 模型资源落盘与 catalog 地基，为后续模型切换与 Spine backend 接入提供稳定边界：

- 定义桌面端 avatar / model assets 在用户电脑目录中的位置，并统一通过 Tauri `paths.rs` 暴露给后续代码使用。
- 建立一个可扩展的 avatar model catalog / schema，至少能表达 Live2D 与 Spine 两类模型。
- 明确内置 / 默认模型与用户目录模型之间的关系，包括开发态、打包态、首次启动 seed / copy 或 fallback 策略。
- 让 settings 只保存当前模型选择的稳定 ID 或轻量 metadata，不把模型文件、散路径或完整 catalog 塞进业务 store。
- 明确资源授权与分发边界：哪些资源可以内置，哪些只能作为用户本地导入或非商用探索样例。

本期默认模型仍是当前可稳定渲染的 Live2D Hiyori。Spine 在本期进入 schema、目录策略与开发 / 探索 catalog 能力，但不作为正式产品默认选中模型；等并行需求 `spine-avatar-backend-ark-models` 跑通正式 Spine runtime 后，再单独评估把某个 Spine 模型提升为可选或默认。

## 范围

### 包含

#### 用户目录与路径策略

- 定义 avatar 模型资产的用户目录位置，遵守项目现行用户数据 / 设置 / 日志目录分类。
- Tauri 侧新增或扩展统一路径 helper；前端和 Tauri 业务模块不得各自散写平台目录。
- 路径策略需要覆盖开发态和打包态，并能被测试或本地 replay 使用的 env override 隔离。
- 模型资产目录需能承载多模型、多 backend、多文件资源，以及后续导入 / 删除 / 重建 catalog 的演进空间。

#### Avatar model catalog / schema

- 建立 catalog 类型或等价 schema，至少支持：
  - Live2D 模型，能表达当前 Hiyori 这类 `.model3.json` 入口及必要的默认 motion / 尺寸 metadata。
  - Spine 模型，能表达 `.skel` 或 `.json` skeleton、`.atlas`、贴图、runtime / format 版本、默认 animation 或 action mapping 所需的轻量 metadata。
  - 模型稳定 ID、显示名、backend kind、来源、资源引用、授权 / 分发级别、启用状态或可用性。
- catalog 应能区分内置模型、用户目录模型、开发 / 探索 fixture。
- schema 设计要为后续新增 avatar backend 留扩展点，但本期不需要做完整插件系统或模型市场。

#### 内置默认模型与用户模型关系

- 当前默认选择保持 Live2D Hiyori，保证现有桌宠启动路径不因为 Spine 尚未正式接入而退化。
- 明确内置模型在开发态和打包态的读取策略。
- 明确首次启动时是否需要把内置模型 seed / copy 到用户模型目录，或保留内置 fallback 并在用户目录叠加 catalog；具体实现策略在 design 阶段确定并落地。
- 用户目录中的模型应以 catalog entry 的稳定 ID 被选择和引用，不能依赖 UI 或业务 store 保存任意散路径。
- 当前 public 目录中的 Hiyori / Spine spike 资源需要有迁移或兼容策略，避免后续继续扩大 public 运行时资产。

#### Settings 持久化边界

- settings schema 增加当前 avatar 模型选择所需的轻量字段，例如稳定 model ID 或等价选择 metadata。
- settings 默认值应指向当前可用的 Live2D Hiyori。
- settings 不保存模型大资源、不保存完整 catalog、不保存多文件散路径列表、不保存 Arknights / Ark-Models 素材本体。
- 前端 `frontend/src/lib/settings/` 与 Tauri `settings.rs` 的类型需要保持一致，并继续支持启动期注入与跨窗口同步。

#### 资源授权与分发边界

- 可内置资源必须授权清楚、适合随 app 分发，并保留对应 license / notice。
- Ark-Models / Arknights 素材只按非商用探索边界处理：
  - 可以作为开发 / spike fixture 或用户本地导入资源被 schema 表达。
  - 不扩大为正式可分发素材策略。
  - 不把 Ark-Models 资源设为正式产品默认模型。
- `frontend/public/spine-spike-local/` 这类本地试验资源继续保持开发者本地语义，不成为正式资源管理路径。

### 不包含

- 不实现正式 Spine runtime backend。
- 不把 Spine 接入 pet 窗正式渲染路径。
- 不做用户切换模型 UI、设置页交互或模型导入向导。
- 不实现模型下载、模型市场、远程 catalog 拉取、资源完整性校验或自动更新。
- 不把 Arknights / Ark-Models 素材作为正式产品随包可分发资源策略。
- 不清理所有历史 public 资源到最终状态，除非 design 阶段确认这是本需求完成资产边界的必要动作。
- 不改变 Live2D 状态机、lip-sync、点击 / 拖拽反馈的用户可见行为。
- 不要求兄弟需求 `spine-avatar-backend-ark-models` 已完成；本需求只为它提供资源目录和 catalog 基础。

## 关键约束

- 路径统一入口以 `frontend/src-tauri/src/paths.rs` 为准；不得在 `settings.rs`、前端业务代码或新模块里散写平台目录字面量。
- settings 继续作为轻量偏好存储，不承载运行时资产。
- catalog 是 avatar 模型资源与 runtime 的边界契约；后续 runtime backend 通过 catalog 理解模型，不从 UI store 里猜路径。
- 设计必须兼顾开发态与打包态，不能只在 Vite public URL 下成立。
- 本期是地基需求，优先保证边界、schema、默认值和兼容策略清楚，不提前做完整多 backend 调试平台。

## 关联上下文

- 探索文档：`docs/explorations/arknights-chibi-avatar-resources/README.md`
- 当前 Live2D 默认模型配置：`frontend/src/pet/live2dConfig.ts`
- 当前 Tauri 路径入口：`frontend/src-tauri/src/paths.rs`
- 当前 settings 持久化：`frontend/src-tauri/src/settings.rs`
- 当前前端 settings facade：`frontend/src/lib/settings/index.ts`
- 当前 Spine spike：`frontend/src/pages/spine-spike/`
- 平行需求边界：`spine-avatar-backend-ark-models` 负责把 Spine spike 转成正式 runtime backend，本需求不实现渲染。

## 验收标准

- [ ] `paths.rs` 中存在 avatar model assets / catalog 所需的统一路径 helper，平台目录符合项目现行用户数据规则，调用方不散写 OS 路径。
- [ ] 项目中存在 avatar model catalog / schema，至少能表达 Live2D Hiyori 与 Spine 模型资源条目。
- [ ] catalog 能区分内置模型、用户目录模型、开发 / 探索 fixture，并能表达授权 / 分发级别。
- [ ] 当前默认模型选择保持 Live2D Hiyori，现有桌宠启动路径不退化。
- [ ] settings 中只保存当前 avatar 模型选择的稳定 ID 或轻量 metadata；不会保存模型文件、完整 catalog 或散路径列表。
- [ ] Tauri `settings.rs` 与前端 `frontend/src/lib/settings/` 的 settings 类型、默认值、set/get 路径保持一致。
- [ ] 开发态、打包态、首次启动 seed / copy 或 fallback 策略在技术方案和代码中明确，且核心路径有自动化测试或等价断言覆盖。
- [ ] Ark-Models / Arknights 素材未被提升为正式默认模型或正式可分发素材策略；相关 catalog entry 如存在，必须体现非商用探索或用户本地导入边界。
- [ ] 兄弟需求后续实现 Spine runtime backend 时，可以复用本需求落地的目录与 catalog，而无需重新定义模型资源位置或 settings 持久化字段。
- [ ] `./scripts/check` 通过；如本需求不改用户可见 UI，交付说明明确不做正式桌面视觉验收及原因。

## 开放问题

- 首次启动策略采用“内置资源 copy / seed 到用户目录”，还是“内置 fallback catalog + 用户目录 overlay”，需要在 design 阶段结合 Tauri 打包资源访问方式与 Hiyori 当前 public 资源路径确定。
- 当前 `frontend/public/spine-spike-fixtures/ark-amiya/` 在本需求中是保留为 spike fixture、迁移到更明确的 dev-only 位置，还是仅通过 catalog 标记为 exploration，需要在 design 阶段结合兄弟需求进度确定。
- 用户导入模型时的 license / notice 展示与风险提示是否要作为后续独立需求登记；本需求只定义 schema 能表达这些信息，不实现导入 UI。

## 变更记录

| 日期 | 变更内容 | 影响范围 |
|------|---------|---------|
| 2026-06-30 | 初版确认：定义 avatar 模型资产用户目录、catalog/schema、settings 轻量选择字段、内置与用户模型关系；默认保持 Live2D Hiyori，Spine 进入 schema 与探索边界但不作为正式默认模型 | Phase 1 |
