# 040 · Desktop Avatar Model Switching

## 状态

CONFIRMED

## 背景

037 已经为桌宠模型建立了 avatar model catalog 与 `avatarModelId` settings 字段，明确 settings 只保存当前模型的稳定 ID，不保存模型文件或散路径。038 已经把 Live2D 与 Spine runtime 收敛到正式 pet runtime 边界，并把 Hiyori 与首批 Ark-Models Spine 小人纳入 catalog；但当前 pet 窗仍通过 dev-only、本地 React state 选择模型，正式用户没有入口切换，选择也不会持久化。

同时，当前模型资源仍主要以 `frontend/public/...` URL 被前端运行时消费。这不符合用户模型资产的长期边界：桌面端 runtime 应从用户模型目录读取已安装模型，项目内置资源只应作为首次安装 / 升级时的 seed。否则后续用户导入、删除、修复、重建 catalog 时，会出现一部分模型读 public、一部分模型读用户目录的双轨路径。

本需求要把“当前桌宠模型”变成正式桌面端设置项，并补齐内置模型安装到用户模型目录的链路：用户在设置页选择 Hiyori 或内置 Ark Spine 模型；选择写入 `avatarModelId`；pet 窗只消费 settings 中的当前模型，并从用户模型目录加载模型资源。

## 目标

- 在设置窗口提供正式的桌宠模型切换入口，覆盖当前 Live2D Hiyori 与已内置 Ark Spine 模型。
- 当前模型选择持久化到现有 `avatarModelId` settings 字段，并在重启后恢复。
- 内置模型资源在应用启动时自动安装 / 修复到用户模型目录；runtime 不再以 `frontend/public/...` 或项目内路径作为正式读取源。
- pet 窗作为唯一消费方：设置变更后 pet 窗切换当前模型，chat / settings / 其他窗口不做 avatar 预览或同步展示。
- 保留 037 / 038 已建立的 catalog、runtime 与 settings 边界，不重新定义模型资源 schema 或完整导入系统。

## 范围

### 包含

#### 设置页模型切换入口

- 设置窗口左侧新增“桌宠”分类。
- “桌宠”页提供“当前模型”选择控件，展示当前可用的 installed avatar model catalog。
- 可选项至少包括：
  - Live2D Hiyori。
  - 038 已内置的首批 Ark-Models Spine 模型。
- 选择模型后写入 `avatarModelId`，并通过现有 settings 变更广播让 pet 窗感知。
- UI 应沿用现有设置页组件与视觉规范，不在 pet ActionBar 中直接承载模型切换控件。

#### 当前模型持久化与恢复

- `avatarModelId` 是当前模型选择的唯一持久化字段。
- 应用重启后，pet 窗恢复上次选中的模型。
- 如果 `avatarModelId` 缺失，默认使用 `builtin:live2d:hiyori`。
- 如果 `avatarModelId` 指向不存在、未安装或不可用的模型，pet 窗稳定 fallback 到 Hiyori，并给出可排查的日志或状态。

#### 内置模型安装到用户目录

- Hiyori 与内置 Ark Spine 资源作为应用随包 seed。
- 应用启动时自动确保内置模型已安装到 `paths::avatar_model_assets_dir()` 对应的用户模型目录。
- 安装过程应是幂等的：
  - 首次启动复制内置模型资源与内置 catalog / manifest。
  - 后续启动文件已存在且版本 / hash 匹配时跳过。
  - 应用升级或内置资源变化时更新内置模型命名空间下的资源。
- 安装 / 修复只管理 built-in 命名空间，不覆盖未来用户导入模型。
- 用户模型目录属于用户资产，遵守项目现行路径规则；不要把模型资产写入 settings 目录、日志目录或仓库根目录。

#### 从用户目录读取模型资源

- pet runtime 加载模型时，应从已安装 catalog 中解析用户模型目录下的资源位置。
- 正式 runtime 不应继续依赖 `/live2d-models/...`、`/spine-models/...`、`frontend/public/...` 或仓库内路径作为资源读取源。
- WebView 访问用户目录模型资源时，访问范围必须收敛到 avatar model assets 目录，不开放任意文件系统路径。
- Hiyori Live2D 与 Spine runtime 都应走同一类“installed model entry → local resource URL”的读取链路。

#### pet 窗消费模型选择

- pet 窗不再以 dev-only 本地 state 作为当前模型来源。
- pet 窗通过 settings / installed catalog resolve 当前 `AvatarModelCatalogEntry`，并按 backend 切到 Live2D 或 Spine runtime。
- 模型切换只影响 pet 窗：
  - ActionBar、bubble anchor、drag、passthrough、状态机 phase 映射等 038 已有能力不应退化。
  - settings / chat 等窗口不需要展示 avatar 预览。
- 当前 dev-only selector 可删除，或保留为隐藏 smoke 工具，但不能与正式 settings 形成两个并列的当前模型来源。

### 不包含

- 不做用户导入模型向导、模型市场、远程下载、资源自动更新或模型删除 UI。
- 不做 chat / settings / 其他窗口中的 avatar 预览。
- 不把 Ark-Models / Arknights 资源提升为商业可分发策略；继续按当前内置非商用探索边界处理，并保留 notice / license metadata。
- 不重做 037 的 avatar catalog schema。
- 不重做 038 的 Live2D / Spine runtime contract、动作映射或 capability 模型。
- 不实现 Live2D / Spine 之外的新 backend。
- 不把模型选择放进 pet ActionBar；ActionBar 只负责打开设置窗口。

## 关键约束

- 运行期模型资产属于用户数据，路径入口以 `frontend/src-tauri/src/paths.rs` 的 avatar model helper 为准。
- settings 只保存当前模型 ID，不保存模型文件、散路径列表或完整 catalog。
- 内置模型安装必须可重复执行，且不能破坏用户未来导入的模型资产。
- pet runtime 的正式资源读取路径必须指向用户模型目录；项目 public 资源只能作为随包 seed 或开发态 fallback，不作为正式运行时来源。
- 模型切换是用户可见的桌宠行为变更，实现后需要做 pet 窗真实桌面端 smoke；不能只用普通浏览器预览得出桌面端视觉 / 交互结论。

## 关联上下文

- 设置中心与 settings 持久化：`docs/requirements/028-settings-center-and-cross-window-sync/`
- avatar 资源与 catalog 地基：`docs/requirements/037-avatar-model-assets-and-catalog/`
- Spine runtime 与 Ark-Models 接入：`docs/requirements/038-spine-avatar-backend-ark-models/`
- ActionBar 打开设置入口：`docs/requirements/019-pet-actionbar-rework-and-settings-shell/`
- 当前路径入口：`frontend/src-tauri/src/paths.rs`
- 当前 settings：
  - `frontend/src-tauri/src/settings.rs`
  - `frontend/src/lib/settings/index.ts`
- 当前 avatar catalog：`frontend/src/avatar-models/catalog.ts`
- 当前 pet runtime：
  - `frontend/src/pages/pet/App.tsx`
  - `frontend/src/pet/avatarRuntime/`

## 验收标准

- [x] 设置页左侧存在“桌宠”分类，右侧存在“当前模型”选择控件。
- [x] 模型选择控件展示 Hiyori 与首批内置 Ark Spine 模型；展示项来自 installed avatar model catalog，而不是页面内硬编码散路径。
- [x] 选择任一模型后，`avatarModelId` settings 被更新；重启应用后仍恢复该选择。
- [x] pet 窗消费 `avatarModelId`，在 Live2D Hiyori 与 Ark Spine 模型之间切换；切换只影响 pet 窗。
- [x] 当前模型 ID 缺失、无效或目标模型不可用时，pet 窗稳定 fallback 到 Hiyori，并记录可排查信息。
- [x] 应用启动时会把内置 Hiyori 与 Ark Spine 模型安装 / 修复到 `paths::avatar_model_assets_dir()`；首次启动与重复启动均不会报错。
- [x] 内置资源升级或 hash / manifest 变化时，built-in 命名空间下资源可被更新；未来用户模型目录不被覆盖。
- [x] pet runtime 加载 Hiyori 与 Spine 模型时，正式资源 URL 均指向用户模型目录中的 installed assets；不再依赖 `frontend/public/...` 路径作为正式运行时来源。
- [x] WebView 读取本地模型资源的访问范围限制在 avatar model assets 目录，不开放任意本地文件路径。
- [x] dev-only 模型 selector 不再作为正式当前模型来源；如保留，仅作为隐藏 smoke 工具，不与 settings 竞争。
- [x] 覆盖核心安装 / manifest / fallback / settings selection resolver 的自动化测试；UI 选择链路至少有可重复的前端单测或集成层断言。
- [x] `./scripts/check` 通过。
- [x] 完成真实桌面端 smoke：切 Hiyori、切至少一个 Ark Spine 模型、重启恢复、拖拽 / ActionBar / bubble anchor 基础行为不退化；若无法完成，交付说明必须写明原因与剩余风险。

## 开放问题

- 内置模型安装 manifest 的版本策略与 hash 粒度需要在 design 阶段确定：可以按模型目录整体版本，也可以按文件 hash 精确比对。
- WebView 读取用户模型目录资源的具体通道需要在 design 阶段确定：倾向 Tauri asset protocol + scoped path，但需结合当前 `tauri.conf.json` 与安全边界落地。
- 启动期安装若遇到文件损坏、磁盘权限或部分复制失败，UI 层是否需要给用户提示，还是本期只记录日志并 fallback，需要在 design 阶段细化。

## 变更记录

| 日期 | 变更内容 | 影响范围 |
|------|---------|---------|
| 2026-06-30 | 初版确认：正式模型切换入口放在设置页“桌宠”分类；当前模型写入 `avatarModelId`；内置 Live2D / Ark Spine 模型启动期安装到用户模型目录；pet 窗只从 installed assets 读取并作为唯一消费方 | Phase 1 |
